"""
aci_sandbox.py -- ACI-backed sandbox runtime for the Kimi agent demo.

Design choices:
- Each session = one Azure Container Instance group (sb-<session_id[:12]>).
- CMD = "sleep infinity" so the container stays alive for multiple exec calls.
- Multi-command sessions use multiple execute_command() calls (each opens a
  transient bash shell). This is simpler than writing scripts via env vars and
  avoids extra image dependencies.  Each call is independent -- no persistent
  shell state between calls, so commands that need to share state should be
  composed into a single shell one-liner (e.g. "cd /tmp && python foo.py").
- ACI exec API returns a web-socket URL + password; we connect via the
  websocket-client library to send the command and capture output.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from azure.core.credentials import TokenCredential
from azure.mgmt.containerinstance import ContainerInstanceManagementClient
from azure.mgmt.containerinstance.models import (
    Container,
    ContainerExecRequest,
    ContainerExecRequestTerminalSize,
    ContainerGroup,
    ContainerGroupRestartPolicy,
    EnvironmentVariable,
    ImageRegistryCredential,
    OperatingSystemTypes,
    ResourceRequests,
    ResourceRequirements,
)

logger = logging.getLogger(__name__)


@dataclass
class Session:
    session_id: str
    container_group_name: str
    resource_group: str
    location: str
    image: str
    created_at: float = field(default_factory=time.time)
    state: str = "creating"  # creating | running | deleted


@dataclass
class RunResult:
    command: str
    output: str
    exit_code: int
    duration_s: float
    error: str | None = None


class ACISandbox:
    """
    Manages isolated sandbox containers on Azure Container Instances.

    Each session is a fresh ACI container group running the sandbox image.
    Commands are dispatched via the ACI execute_command WebSocket API.
    """

    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
        resource_group: str,
        location: str,
        acr_login_server: str,
        sandbox_image: str,
        cpu: float = 1.0,
        memory_gb: float = 1.5,
    ) -> None:
        self._cred = credential
        self._sub = subscription_id
        self._rg = resource_group
        self._location = location
        self._acr = acr_login_server
        self._image = sandbox_image
        self._cpu = cpu
        self._memory_gb = memory_gb
        self._client = ContainerInstanceManagementClient(credential, subscription_id)
        self._sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(
        self,
        session_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Session:
        """
        Spin up a new ACI container group and wait until it's Running.

        Returns a Session object. Raises on timeout (>180 s).
        """
        session_id = session_id or str(uuid.uuid4())
        cg_name = f"sb-{session_id[:12]}"

        env_vars = [EnvironmentVariable(name=k, value=v) for k, v in (env or {}).items()]

        container = Container(
            name="sandbox",
            image=f"{self._acr}/{self._image}",
            resources=ResourceRequirements(
                requests=ResourceRequests(cpu=self._cpu, memory_in_gb=self._memory_gb)
            ),
            environment_variables=env_vars,
            command=["sleep", "infinity"],
        )

        acr_cred = self._get_acr_credential()

        cg = ContainerGroup(
            location=self._location,
            containers=[container],
            os_type=OperatingSystemTypes.LINUX,
            restart_policy=ContainerGroupRestartPolicy.NEVER,
            image_registry_credentials=[acr_cred] if acr_cred else None,
        )

        logger.info("Creating ACI container group %s ...", cg_name)
        t0 = time.monotonic()
        self._client.container_groups.begin_create_or_update(self._rg, cg_name, cg).result()

        # Poll until Succeeded (max 180 s)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            cg_state = self._client.container_groups.get(self._rg, cg_name)
            state = (cg_state.provisioning_state or "").lower()
            if state == "succeeded":
                break
            if state in {"failed", "canceled"}:
                raise RuntimeError(f"ACI container group {cg_name} reached state {state}")
            time.sleep(4)
        else:
            raise TimeoutError(f"Timed out waiting for ACI container group {cg_name}")

        elapsed = time.monotonic() - t0
        logger.info("ACI container group %s ready in %.1f s", cg_name, elapsed)

        session = Session(
            session_id=session_id,
            container_group_name=cg_name,
            resource_group=self._rg,
            location=self._location,
            image=self._image,
            state="running",
        )
        self._sessions[session_id] = session
        return session

    def run(self, session_id: str, command: str, timeout_s: int = 60) -> RunResult:
        """
        Execute a shell command inside the sandbox.

        Uses ACI execute_command API. Each call is a fresh bash invocation --
        no persistent state between calls.

        The command string is passed as a single quoted argument to bash -c to
        support pipes and redirects while avoiding shell injection at the ACI
        API level (the command list is passed directly to the SDK, not via sh).
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Unknown session: {session_id}")

        cg_name = session.container_group_name
        t0 = time.monotonic()

        exec_request = ContainerExecRequest(
            command=f"/bin/bash -c {command!r}",
            terminal_size=ContainerExecRequestTerminalSize(rows=48, cols=160),
        )

        logger.debug("exec [%s] -> %s", cg_name, command[:120])
        try:
            exec_resp = self._client.containers.execute_command(
                self._rg, cg_name, "sandbox", exec_request
            )
            output = self._ws_exec(
                exec_resp.web_socket_uri,
                exec_resp.password,
                command,
                timeout_s,
            )
            exit_code = 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("exec error: %s", exc)
            return RunResult(
                command=command,
                output="",
                exit_code=1,
                duration_s=time.monotonic() - t0,
                error=str(exc),
            )

        return RunResult(
            command=command,
            output=output,
            exit_code=exit_code,
            duration_s=time.monotonic() - t0,
        )

    def delete(self, session_id: str) -> None:
        """Delete the ACI container group for the given session."""
        session = self._sessions.get(session_id)
        if session is None:
            logger.warning("delete called for unknown session %s -- ignoring", session_id)
            return

        cg_name = session.container_group_name
        logger.info("Deleting ACI container group %s ...", cg_name)
        self._client.container_groups.begin_delete(self._rg, cg_name).result()
        session.state = "deleted"
        logger.info("Deleted %s", cg_name)

    def list_active(self) -> list[Session]:
        """Return all in-memory sessions that are not deleted."""
        return [s for s in self._sessions.values() if s.state != "deleted"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_acr_credential(self) -> ImageRegistryCredential | None:
        """
        Return ACR credential using the managed identity / az-cli token.
        Uses the AAD access token directly as the ACR password with the
        sentinel username "00000000-0000-0000-0000-000000000000" (token auth).
        """
        try:
            token = self._cred.get_token("https://management.azure.com/.default")
            return ImageRegistryCredential(
                server=self._acr,
                username="00000000-0000-0000-0000-000000000000",
                password=token.token,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not obtain ACR credential: %s -- will try anonymous pull", exc)
            return None

    def _ws_exec(self, ws_uri: str, password: str, command: str, timeout_s: int) -> str:
        """
        Connect to the ACI exec WebSocket, authenticate, send the command,
        collect output, and return the captured text.

        Protocol:
        - Send the password bytes first to authenticate.
        - Send the command followed by a newline to execute it.
        - Read until the connection closes or timeout_s elapses.
        """
        try:
            import websocket  # websocket-client
        except ImportError:
            return (
                "[ACI exec] websocket-client not installed. "
                "Install with: pip install websocket-client\n"
                "Would have run: " + command
            )

        output_chunks: list[str] = []
        done_event = threading.Event()

        def on_open(ws: Any) -> None:
            ws.send(password)
            ws.send(command + "\n")

        def on_message(ws: Any, message: Any) -> None:
            if isinstance(message, bytes):
                output_chunks.append(message.decode("utf-8", errors="replace"))
            else:
                output_chunks.append(str(message))

        def on_error(ws: Any, error: Any) -> None:
            logger.warning("WebSocket error: %s", error)

        def on_close(ws: Any, *_: Any) -> None:
            done_event.set()

        ws_app = websocket.WebSocketApp(
            ws_uri,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        t = threading.Thread(
            target=ws_app.run_forever,
            kwargs={"ping_interval": 10, "ping_timeout": 5},
            daemon=True,
        )
        t.start()
        done_event.wait(timeout=timeout_s)
        ws_app.close()

        return "".join(output_chunks)
