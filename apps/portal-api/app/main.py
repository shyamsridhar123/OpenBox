from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import json
import uuid

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from . import swarm as swarm_module
from . import cluster_history
from . import history
from .clients import AzClient, ControlPlaneClient, K8sClient
from .config import settings
from .identity import resolve_identity

# P0-6: last-action history is persisted to apps/portal-api/data/cluster-history.json
# via cluster_history.py. The in-memory _last_action global is gone.

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _poll_sandbox_expiry() -> None:
    """Background task: detect vanished sandboxes and record auto-expiry every 30 s."""
    import asyncio as _asyncio

    while True:
        try:
            await _asyncio.sleep(30)
            cp = ControlPlaneClient(settings.CONTROL_PLANE_URL, settings.CONTROL_PLANE_API_KEY)
            live_raw = await cp.list_sandboxes()
            if not isinstance(live_raw, list):
                continue
            live_ids = {
                str(sb.get("id") or sb.get("sandbox_id") or "")
                for sb in live_raw
                if isinstance(sb, dict)
            }
            live_ids.discard("")

            tracked = history.list_sandbox_creations(limit=200)
            now = int(time.time())
            for row in tracked:
                if row.get("expired_at") is not None:
                    continue
                sid = row.get("sandbox_id", "")
                if sid and sid not in live_ids:
                    history.record_sandbox_expiry(sid, now, "auto-expire")
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            pass  # don't crash the loop on transient errors


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    # C3: initialise SQLite history store (idempotent)
    history.init_db()

    identity = resolve_identity()
    key_status = "present" if identity["key_file_exists"] else "MISSING"
    sub = identity["az_subscription_name"] or "—"
    sub_id = identity["az_subscription_id"] or "—"
    logger.warning(
        "\n============================================================\n"
        "DarkForge Portal — DEV MODE\n"
        "  az user:        %s\n"
        "  subscription:   %s (%s)\n"
        "  kubectl ctx:    %s\n"
        "  namespace:      %s\n"
        "  api key file:   %s\n"
        "This portal runs as YOU. Do not expose beyond localhost.\n"
        "============================================================",
        identity["az_user"] or "—",
        sub,
        sub_id,
        identity["kubectx"] or "—",
        identity["cluster_namespace"],
        key_status,
    )

    expiry_task = asyncio.create_task(_poll_sandbox_expiry())
    yield
    expiry_task.cancel()
    try:
        await expiry_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="DarkForge Portal API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8090", "http://127.0.0.1:8090"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND_DIST = Path(__file__).parent.parent.parent / "portal-frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIST)), name="static")


def _node_pool(node_name: str | None) -> str:
    """Classify a node by name. AKS naming pattern: aks-<poolname>-<vmss-id>-<instance>."""
    if not node_name:
        return "unknown"
    n = node_name.lower()
    # Kata pool: aks-kata-*
    if "aks-kata" in n or n.startswith("kata-"):
        return "kata"
    # System / default nodepool: aks-nodepool1-*, aks-system*, etc.
    if "aks-nodepool" in n or "aks-system" in n or n.startswith("nodepool"):
        return "system"
    return "unknown"


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    """Expose resolved configuration so the frontend never hardcodes
    ACR URIs or default images (C1)."""
    return {
        "acr_registry": settings.ACR_REGISTRY,
        "sandbox_base_image": settings.SANDBOX_BASE_IMAGE,
        "vnc_image": settings.VNC_IMAGE,
        "default_pool_name": settings.DEFAULT_POOL_NAME,
        "opensandbox_namespace": settings.OPENSANDBOX_NAMESPACE,
        "kimi_deployments": list(settings.KIMI_DEPLOYMENTS),
        "control_plane_url": settings.CONTROL_PLANE_URL,
    }


@app.get("/api/identity")
async def identity() -> dict[str, Any]:
    return resolve_identity()


@app.get("/api/cluster/state")
async def cluster_state() -> dict[str, Any]:
    az = AzClient(settings.RESOURCE_GROUP, settings.CLUSTER_NAME)
    data = await az.get_state()
    last = cluster_history.read_last_action()
    if last:
        data = {
            **data,
            "last_action": last.get("last_action"),
            "last_action_at": last.get("last_action_at"),
            "last_actor": last.get("last_actor"),
            "last_outcome": last.get("outcome"),
            "last_duration_s": last.get("duration_s"),
        }
    return data


async def _poll_cluster_completion(action: str, expected_terminal: str) -> None:
    """Background task: poll AKS power state until it reaches the expected
    terminal value (Running for Start, Stopped for Stop) or timeout, then
    write the completion record. Bounded at 30 minutes."""
    import asyncio

    az = AzClient(settings.RESOURCE_GROUP, settings.CLUSTER_NAME)
    deadline_s = 1800
    interval_s = 10
    elapsed = 0
    while elapsed < deadline_s:
        await asyncio.sleep(interval_s)
        elapsed += interval_s
        try:
            state = await az.get_state()
        except Exception:  # noqa: BLE001
            continue
        power = (state.get("power") or "").lower() if isinstance(state, dict) else ""
        if expected_terminal.lower() in power:
            cluster_history.record_action_completed("success")
            return
    cluster_history.record_action_completed("failed")


@app.post("/api/cluster/start", status_code=202)
async def cluster_start() -> dict[str, Any]:
    import asyncio

    az = AzClient(settings.RESOURCE_GROUP, settings.CLUSTER_NAME)
    actor = (resolve_identity() or {}).get("az_user")
    record = cluster_history.record_action_started("Start", actor)
    result = await az.start()
    if "error" in result:
        cluster_history.record_action_completed("failed")
        return result
    asyncio.create_task(_poll_cluster_completion("Start", "Running"))
    return {
        "job_id": str(uuid.uuid4()),
        "state": "Starting",
        "started_at": result["started_at"],
        "actor": record.get("last_actor"),
    }


@app.post("/api/cluster/stop", status_code=202)
async def cluster_stop() -> dict[str, Any]:
    import asyncio

    az = AzClient(settings.RESOURCE_GROUP, settings.CLUSTER_NAME)
    actor = (resolve_identity() or {}).get("az_user")
    record = cluster_history.record_action_started("Stop", actor)
    result = await az.stop()
    if "error" in result:
        cluster_history.record_action_completed("failed")
        return result
    asyncio.create_task(_poll_cluster_completion("Stop", "Stopped"))
    return {
        "job_id": str(uuid.uuid4()),
        "state": "Stopping",
        "started_at": result["started_at"],
        "actor": record.get("last_actor"),
    }


@app.get("/api/sandboxes")
async def list_sandboxes() -> Any:
    cp = ControlPlaneClient(settings.CONTROL_PLANE_URL, settings.CONTROL_PLANE_API_KEY)
    k8s = K8sClient(settings.OPENSANDBOX_NAMESPACE)

    sandboxes_raw = await cp.list_sandboxes()
    if isinstance(sandboxes_raw, dict) and "error" in sandboxes_raw:
        return {"error": sandboxes_raw["error"], "sandboxes": []}

    pods = await k8s.list_pods()
    # Build lookup: sandbox id → pod info.
    # OpenSandbox names pods as "{sandbox-uuid}-0", so match by prefix.
    pod_by_sandbox: dict[str, dict[str, Any]] = {}
    for pod in pods:
        labels: dict[str, str] = pod.get("labels", {})
        sb_id = labels.get("sandbox-id") or labels.get("opensandbox-id") or ""
        if sb_id:
            pod_by_sandbox[sb_id] = pod
        # Also index by pod-name prefix (uuid before "-0" suffix)
        pod_name = pod.get("pod_name", "")
        if pod_name:
            # Strip the trailing "-<n>" replica index
            base = pod_name.rsplit("-", 1)[0]
            pod_by_sandbox.setdefault(base, pod)
            pod_by_sandbox.setdefault(pod_name, pod)

    def _status_str(raw: Any) -> str:
        """Flatten control-plane status (which may be {state, reason, message, ...})."""
        if isinstance(raw, dict):
            return str(raw.get("state") or raw.get("phase") or raw.get("reason") or "unknown")
        return str(raw) if raw else "unknown"

    result = []
    for sb in sandboxes_raw:
        if not isinstance(sb, dict):
            continue
        sb_id: str = str(sb.get("id", sb.get("sandbox_id", "")))
        created_raw = sb.get("created_at") or sb.get("createdAt") or ""
        try:
            created_dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
            created_at = created_dt.isoformat()
            age_s = int((datetime.now(timezone.utc) - created_dt).total_seconds())
        except Exception:
            created_at = str(created_raw)
            age_s = -1

        pod_info = pod_by_sandbox.get(sb_id, {})
        node_name: str | None = pod_info.get("node_name")
        result.append({
            "id": sb_id,
            "status": _status_str(sb.get("status", sb.get("state"))),
            "created_at": created_at,
            "age_seconds": age_s,
            "pod_name": pod_info.get("pod_name", ""),
            "node_name": node_name or "",
            "node_pool": _node_pool(node_name),
            "runtime_class": pod_info.get("runtime_class") or sb.get("runtime_class", "") or "runc",
            "phase": pod_info.get("phase", ""),
        })
    return result


@app.get("/api/cluster/summary")
async def cluster_summary() -> Any:
    k8s = K8sClient(settings.OPENSANDBOX_NAMESPACE)
    nodes = await k8s.list_nodes()
    pods = await k8s.list_pods()

    kata_nodes = sum(1 for n in nodes if _node_pool(n.get("name", "")) == "kata")
    system_nodes = sum(1 for n in nodes if _node_pool(n.get("name", "")) == "system")

    cp = ControlPlaneClient(settings.CONTROL_PLANE_URL, settings.CONTROL_PLANE_API_KEY)
    sandboxes_raw = await cp.list_sandboxes()
    total_sandboxes = 0
    if isinstance(sandboxes_raw, list):
        total_sandboxes = len(sandboxes_raw)

    kata_pods = sum(1 for p in pods if _node_pool(p.get("node_name", "")) == "kata")
    system_pods = sum(1 for p in pods if _node_pool(p.get("node_name", "")) == "system")
    running = sum(1 for p in pods if p.get("phase") == "Running")
    pending = sum(1 for p in pods if p.get("phase") == "Pending")

    return {
        "nodes": {"kata": kata_nodes, "system": system_nodes},
        "sandboxes": {
            "total": total_sandboxes,
            "kata_pods": kata_pods,
            "system_pods": system_pods,
            "running": running,
            "pending": pending,
        },
    }


# ---------------------------------------------------------------------------
# Swarm endpoints
# ---------------------------------------------------------------------------

_VALID_MODELS = {"Kimi-K2.5", "Kimi-K2.6"}


class SwarmRunRequest(BaseModel):
    n: int
    model: str = "Kimi-K2.6"
    image: str | None = None

    @field_validator("n")
    @classmethod
    def _validate_n(cls, v: int) -> int:
        if not (1 <= v <= 200):
            raise ValueError("n must be between 1 and 200")
        return v

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if v not in _VALID_MODELS:
            raise ValueError(f"model must be one of {sorted(_VALID_MODELS)}")
        return v


@app.post("/api/swarm/runs", status_code=202)
async def swarm_create(req: SwarmRunRequest) -> dict[str, str]:
    run_id = await swarm_module.start_run(req.n, req.model, req.image)
    return {"run_id": run_id}


@app.get("/api/swarm/runs")
async def swarm_list() -> list[dict]:
    return swarm_module.list_runs()


@app.get("/api/swarm/runs/{run_id}")
async def swarm_get(run_id: str) -> dict:
    handle = swarm_module.get_run(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    summary = handle.summary or {}
    return {
        "run_id": handle.run_id,
        "state": handle.state,
        "n": handle.n,
        "model": handle.model,
        "image": handle.image,
        "started_at": handle.started_at.isoformat(),
        "finished_at": handle.finished_at.isoformat() if handle.finished_at else None,
        "events": handle.events,
        "leaderboard": handle.leaderboard,
        "summary": summary,
    }


@app.get("/api/swarm/runs/{run_id}/events")
async def swarm_events(run_id: str):
    from sse_starlette.sse import EventSourceResponse

    handle = swarm_module.get_run(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    async def _generator():
        async for evt in swarm_module.stream_events(run_id):
            evt_type = evt.get("type", "message")
            evt_data = evt.get("data", {})
            yield {"event": evt_type, "data": json.dumps(evt_data)}

    return EventSourceResponse(_generator())


@app.delete("/api/swarm/runs/{run_id}")
async def swarm_cancel(run_id: str) -> dict[str, bool]:
    cancelled = await swarm_module.cancel_run(run_id)
    if not cancelled:
        handle = swarm_module.get_run(run_id)
        if handle is None:
            raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return {"cancelled": cancelled}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

# ── Sandbox CRUD (Step 4) ──

class CreateSandboxRequest(BaseModel):
    image: str = settings.SWARM_DEFAULT_IMAGE
    timeout: int = 300  # seconds, min 60
    entrypoint: list[str] = ["/bin/bash"]
    runtime_class: str = "kata-vm-isolation"
    cpu: str = "500m"
    memory: str = "512Mi"
    env: dict[str, str] = {}

    @field_validator("timeout")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        if v < 60:
            raise ValueError("timeout must be at least 60 seconds")
        return v


@app.post("/api/sandboxes", status_code=202)
async def create_sandbox(req: CreateSandboxRequest) -> Any:
    from .clients import ControlPlaneClient
    cp = ControlPlaneClient(settings.CONTROL_PLANE_URL, settings.CONTROL_PLANE_API_KEY)
    body = {
        "image": {"uri": req.image},
        "timeout": req.timeout,
        "resourceLimits": {"cpu": req.cpu, "memory": req.memory},
        "entrypoint": req.entrypoint,
        "env": req.env,
        "metadata": {"runtime_class": req.runtime_class, "created_via": "portal-v2"},
    }
    created = await cp.create_sandbox(body)
    # C3: record creation in history
    if isinstance(created, dict) and not created.get("error"):
        sb_id = str(created.get("id") or created.get("sandbox_id") or "")
        if sb_id:
            history.record_sandbox_creation(sb_id, req.image, req.runtime_class, int(time.time()))
    return created


@app.delete("/api/sandboxes/{sandbox_id}")
async def delete_sandbox(sandbox_id: str) -> Any:
    from .clients import ControlPlaneClient
    cp = ControlPlaneClient(settings.CONTROL_PLANE_URL, settings.CONTROL_PLANE_API_KEY)
    result = await cp.delete_sandbox(sandbox_id)
    # C3: record manual expiry
    history.record_sandbox_expiry(sandbox_id, int(time.time()), "manual")
    return result


# ---------------------------------------------------------------------------
# C3: History endpoints
# ---------------------------------------------------------------------------


@app.get("/api/history/chat")
async def get_chat_history(conversation_id: str, limit: int = 100) -> list[dict]:
    return history.list_chat_messages(conversation_id, limit=limit)


@app.get("/api/history/chat/conversations")
async def get_conversations() -> list[dict]:
    return history.list_conversations()


@app.get("/api/history/swarm")
async def get_swarm_history(limit: int = 20) -> list[dict]:
    return history.list_swarm_runs(limit=limit)


@app.get("/api/history/sandbox")
async def get_sandbox_history(limit: int = 50) -> list[dict]:
    return history.list_sandbox_creations(limit=limit)


# ── VNC sandbox (#18) ──

class VncSandboxRequest(BaseModel):
    image: str | None = None  # defaults to settings.VNC_IMAGE
    timeout_s: int = 1800
    runtime_class: str = "kata-vm-isolation"

    @field_validator("timeout_s")
    @classmethod
    def _validate_timeout(cls, v: int) -> int:
        if v < 60:
            raise ValueError("timeout_s must be at least 60 seconds")
        if v > 14400:  # 4h ceiling — these are heavyweight
            raise ValueError("timeout_s must be at most 14400 seconds (4h)")
        return v


@app.post("/api/sandbox/vnc", status_code=202)
async def create_vnc_sandbox(req: VncSandboxRequest) -> Any:
    """Create a desktop sandbox running noVNC on port 6080. Response
    includes the proxy URL the frontend can iframe directly."""
    from .clients import ControlPlaneClient

    image = req.image or settings.VNC_IMAGE
    cp = ControlPlaneClient(settings.CONTROL_PLANE_URL, settings.CONTROL_PLANE_API_KEY)

    # Entry-point left default — the VNC image's CMD owns the lifecycle
    # (xvfb + window manager + noVNC). We just need the port exposed.
    body = {
        "image": {"uri": image},
        "timeout": req.timeout_s,
        # The control plane requires entrypoint even when the image has a
        # CMD baked in (docker semantics don't carry over). Mirror the
        # desktop-vnc Dockerfile: ENTRYPOINT ["/usr/bin/tini","--"]
        # CMD ["/usr/local/bin/start.sh"]. Flatten to a single argv list
        # since the control plane wants one.
        "entrypoint": ["/usr/bin/tini", "--", "/usr/local/bin/start.sh"],
        # 4 GiB ceiling lets Chromium + a single tab run comfortably.
        "resourceLimits": {"cpu": "2", "memory": "4Gi"},
        "metadata": {
            "runtime_class": req.runtime_class,
            "created_via": "portal-v2-vnc",
            "kind": "vnc-desktop",
        },
        # The control plane exposes 6080 via /v1/sandboxes/{id}/proxy/6080.
        # Some control-plane builds require explicit portMap — declare it so
        # the proxy is wired even on those builds. Newer builds ignore the
        # field, which is harmless.
        "portMap": [{"name": "novnc", "port": 6080, "protocol": "TCP"}],
    }
    created = await cp.create_sandbox(body)
    if isinstance(created, dict) and "error" in created:
        raise HTTPException(status_code=502, detail=created["error"])

    sb_id = ""
    if isinstance(created, dict):
        sb_id = str(created.get("id") or created.get("sandbox_id") or "")
    if not sb_id:
        raise HTTPException(
            status_code=502,
            detail=f"control plane returned no sandbox id: {created!r}",
        )

    base = settings.CONTROL_PLANE_URL.rstrip("/")
    vnc_url = f"{base}/v1/sandboxes/{sb_id}/proxy/6080/vnc.html?autoconnect=true&resize=remote"
    return {
        "sandbox_id": sb_id,
        "vnc_url": vnc_url,
        "image": image,
        "timeout_s": req.timeout_s,
        "raw": created,
    }


@app.get("/api/sandbox/{sandbox_id}/vnc-probe")
async def vnc_probe(sandbox_id: str) -> Any:
    """Server-side probe for noVNC readiness. Returns 200 once the in-pod
    websockify is reachable via the control plane proxy. The browser polls
    this so it can show a 'booting...' message until the VM and the VNC
    stack are both up (cold start ~60-90s)."""
    import httpx

    url = (
        f"{settings.CONTROL_PLANE_URL.rstrip('/')}"
        f"/v1/sandboxes/{sandbox_id}/proxy/6080/vnc.html"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                url,
                headers={"OPEN-SANDBOX-API-KEY": settings.CONTROL_PLANE_API_KEY},
            )
            if r.status_code == 200:
                return {"ready": True, "status": 200}
            raise HTTPException(status_code=503, detail=f"backend not ready: {r.status_code}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"probe failed: {exc}")


# ── end VNC sandbox (#18) ──



# ── end Sandbox CRUD (Step 4) ──


# ── Sandbox exec (Step 16 — Code Interpreter) ──

class SandboxExecRequest(BaseModel):
    code: str
    image: str | None = None
    timeout_s: int = 90

    @field_validator("timeout_s")
    @classmethod
    def _validate_timeout_s(cls, v: int) -> int:
        return max(5, min(v, 300))


@app.post("/api/sandbox/exec")
async def sandbox_exec(req: SandboxExecRequest) -> Any:
    """Run a Python snippet in a fresh Kata sandbox; returns stdout/stderr/chart_b64."""
    helper = Path(__file__).parent.parent.parent.parent / "examples" / "run_in_sandbox.py"
    timeout_s = req.timeout_s
    outer_cap = timeout_s + 30

    tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8")
    try:
        tmp.write(req.code)
        tmp.close()

        env = os.environ.copy()
        env["SNIPPET_PATH"] = tmp.name
        env["SANDBOX_IMAGE"] = req.image or settings.SWARM_DEFAULT_IMAGE
        env["EXEC_TIMEOUT_S"] = str(timeout_s)
        env["OPENSANDBOX_DOMAIN"] = "localhost:18080"
        env["PYTHONUNBUFFERED"] = "1"

        # NOTE: uvicorn forces SelectorEventLoop on Windows, which raises
        # NotImplementedError on asyncio.create_subprocess_exec. Run the
        # blocking subprocess on a worker thread instead — same pattern as
        # swarm.py (Popen + asyncio.to_thread).
        def _run_helper() -> tuple[bytes, bytes, bool]:
            p = subprocess.Popen(  # noqa: S603
                [str(settings.SWARM_VENV_PYTHON), str(helper)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                out, err = p.communicate(timeout=float(outer_cap))
                return out, err, False
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
                return out, err, True

        raw_out, raw_err, timed_out = await asyncio.to_thread(_run_helper)
        if timed_out:
            return {"error": "subprocess timeout"}

        if raw_err:
            logger.info("sandbox_exec stderr: %s", raw_err.decode(errors="replace"))

        lines = [ln for ln in raw_out.decode(errors="replace").splitlines() if ln.strip()]
        if not lines:
            return {"error": "runner produced no output"}

        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            return {"error": f"runner output not valid JSON: {exc}", "raw": lines[-1]}

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

# ── end Sandbox exec (Step 16) ──


@app.get("/")
async def index() -> FileResponse:
    index_file = _FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    from fastapi.responses import HTMLResponse
    return HTMLResponse("<h1>DarkForge Portal</h1><p>Frontend dist not found.</p>")  # type: ignore[return-value]


# -- Observability (Step 6) --

@app.get("/api/pool/{name}")
async def get_pool(name: str) -> dict:
    """Return normalized Pool CR for the given pool name (e.g. kata)."""
    from .clients import K8sClient
    k8s = K8sClient(settings.OPENSANDBOX_NAMESPACE)
    return await k8s.get_pool_cr(name)


class PoolPatchRequest(BaseModel):
    poolMin: int | None = None
    poolMax: int | None = None
    bufferMin: int | None = None
    bufferMax: int | None = None


@app.patch("/api/pool/{name}")
async def patch_pool(name: str, req: PoolPatchRequest) -> Any:
    """Patch Pool CR capacity fields (#19). Validates 1≤poolMin≤poolMax≤50
    and 0≤bufferMin≤bufferMax≤poolMax before hitting the API server."""
    from .clients import K8sClient

    k8s = K8sClient(settings.OPENSANDBOX_NAMESPACE)
    current = await k8s.get_pool_cr(name)
    if "error" in current:
        raise HTTPException(status_code=404, detail=f"pool {name!r}: {current['error']}")

    # Merge requested values onto current to validate the resulting state.
    effective = {
        "pool_min": req.poolMin if req.poolMin is not None else current.get("pool_min", 0),
        "pool_max": req.poolMax if req.poolMax is not None else current.get("pool_max", 0),
        "buffer_min": req.bufferMin if req.bufferMin is not None else current.get("buffer_min", 0),
        "buffer_max": req.bufferMax if req.bufferMax is not None else current.get("buffer_max", 0),
    }

    pmin, pmax = effective["pool_min"], effective["pool_max"]
    bmin, bmax = effective["buffer_min"], effective["buffer_max"]
    if not (1 <= pmin <= pmax <= 50):
        raise HTTPException(
            status_code=422,
            detail=f"poolMin/poolMax must satisfy 1 <= poolMin <= poolMax <= 50 (got {pmin}/{pmax})",
        )
    if not (0 <= bmin <= bmax <= pmax):
        raise HTTPException(
            status_code=422,
            detail=f"bufferMin/bufferMax must satisfy 0 <= bufferMin <= bufferMax <= poolMax (got {bmin}/{bmax}, poolMax={pmax})",
        )

    patch = {
        "pool_min": req.poolMin,
        "pool_max": req.poolMax,
        "buffer_min": req.bufferMin,
        "buffer_max": req.bufferMax,
    }
    result = await k8s.patch_pool_cr(name, patch)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.get("/api/events")
async def list_events(since: int = 300, limit: int = 50) -> dict:
    """Return recent namespace events, newest-first, with severity class
    and human-language translations (P0-5)."""
    from .clients import ControlPlaneClient, K8sClient
    from .events import enrich_events

    k8s = K8sClient(settings.OPENSANDBOX_NAMESPACE)
    raw = await k8s.list_events(since_seconds=since, limit=limit)

    # Best-effort: fetch live sandbox ids so we can flag stale events.
    cp = ControlPlaneClient(settings.CONTROL_PLANE_URL, settings.CONTROL_PLANE_API_KEY)
    sandboxes_raw = await cp.list_sandboxes()
    live_ids: set[str] | None = None
    if isinstance(sandboxes_raw, list):
        live_ids = {
            str(sb.get("id") or sb.get("sandbox_id") or "")
            for sb in sandboxes_raw
            if isinstance(sb, dict)
        }
        live_ids.discard("")

    events = enrich_events(raw, live_sandbox_ids=live_ids)
    return {"events": events, "count": len(events)}


# ── Kimi chat (Step 5) ────────────────────────────────────────────────────────

class KimiChatRequest(BaseModel):
    messages: list[dict]  # OpenAI-style: [{"role": "user", "content": "..."}]
    deployment: str | None = None  # e.g. "Kimi-K2.6"; None means walk the deployments tuple (K2.6 → K2.5)
    max_tokens: int = 16000
    temperature: float = 0.7
    conversation_id: str | None = None  # C3: thread ID for history persistence


@app.post("/api/kimi/chat")
async def kimi_chat(req: KimiChatRequest) -> Any:
    from .clients import KimiClient

    # C3: record the user turn; mint conversation_id if this is a new thread
    last_user_text = ""
    for m in reversed(req.messages):
        if m.get("role") == "user":
            last_user_text = str(m.get("content") or "")
            break
    cid = history.record_chat_turn(
        req.conversation_id, "user", last_user_text, deployment=req.deployment
    )

    kimi = KimiClient(
        settings.KIMI_ENDPOINT,
        settings.KIMI_DEPLOYMENTS,
        settings.KIMI_API_VERSION,
    )
    result = await kimi.chat(
        messages=req.messages,
        deployment=req.deployment,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
    )
    if "error" in result:
        return JSONResponse(status_code=502, content=result)

    # C3: record the assistant reply
    reply_content = ""
    msg = result.get("message", {})
    if isinstance(msg, dict):
        reply_content = str(msg.get("content") or "")
    used_deployment = result.get("deployment_used", req.deployment)
    history.record_chat_turn(cid, "assistant", reply_content, deployment=used_deployment)

    return {**result, "conversation_id": cid}
