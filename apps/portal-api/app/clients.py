from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import anyio
import httpx

logger = logging.getLogger(__name__)

# Module-level cache: keyed by (resource_group, cluster_name) → (timestamp, value)
_state_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_STATE_TTL = 2.0  # seconds


def _run_az(*args: str) -> tuple[int, str, str]:
    """Run az CLI synchronously (called via anyio.to_thread.run_sync).

    Uses shell=True so that Windows .cmd shims (az.cmd) are resolved correctly.
    """
    cmd = "az " + " ".join(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        shell=True,
    )
    return result.returncode, result.stdout, result.stderr


class AzClient:
    def __init__(self, resource_group: str, cluster_name: str) -> None:
        self._rg = resource_group
        self._cluster = cluster_name

    async def get_state(self) -> dict[str, Any]:
        cache_key = (self._rg, self._cluster)
        now = time.monotonic()
        cached = _state_cache.get(cache_key)
        if cached and (now - cached[0]) < _STATE_TTL:
            return cached[1]

        try:
            rc, stdout, stderr = await anyio.to_thread.run_sync(
                lambda: _run_az(
                    "aks", "show",
                    "-g", self._rg,
                    "-n", self._cluster,
                    "--query", "{state:provisioningState,power:powerState.code,name:name,location:location}",
                    "-o", "json",
                )
            )
            if rc != 0:
                return {"error": stderr.strip() or f"az exited {rc}"}
            value: dict[str, Any] = json.loads(stdout)
        except Exception as exc:
            return {"error": str(exc)}

        # Timestamp AFTER the call completes so the 2s TTL is measured from now.
        _state_cache[cache_key] = (time.monotonic(), value)
        return value

    async def start(self) -> dict[str, Any]:
        try:
            rc, _out, stderr = await anyio.to_thread.run_sync(
                lambda: _run_az(
                    "aks", "start",
                    "-g", self._rg,
                    "-n", self._cluster,
                    "--no-wait",
                    "-o", "none",
                )
            )
            if rc != 0:
                return {"error": stderr.strip() or f"az exited {rc}"}
        except Exception as exc:
            return {"error": str(exc)}
        return {
            "action": "start",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    async def stop(self) -> dict[str, Any]:
        try:
            rc, _out, stderr = await anyio.to_thread.run_sync(
                lambda: _run_az(
                    "aks", "stop",
                    "-g", self._rg,
                    "-n", self._cluster,
                    "--no-wait",
                    "-o", "none",
                )
            )
            if rc != 0:
                return {"error": stderr.strip() or f"az exited {rc}"}
        except Exception as exc:
            return {"error": str(exc)}
        return {
            "action": "stop",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }


class ControlPlaneClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def list_sandboxes(self) -> list[dict[str, Any]] | dict[str, str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/sandboxes",
                    headers={"OPEN-SANDBOX-API-KEY": self._api_key},
                )
                if resp.status_code != 200:
                    return {"error": f"control-plane returned {resp.status_code}"}
                data = resp.json()
                if isinstance(data, list):
                    return data
                # some shapes wrap in a key
                if isinstance(data, dict):
                    for key in ("sandboxes", "items", "data"):
                        if key in data and isinstance(data[key], list):
                            return data[key]
                return {"error": "unexpected control-plane response shape"}
        except httpx.ConnectError as exc:
            return {"error": f"cannot reach control plane: {exc}"}
        except Exception as exc:
            logger.exception("control plane error")
            return {"error": str(exc)}

    async def create_sandbox(self, body: dict) -> dict[str, Any] | dict[str, str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/sandboxes",
                    headers={"OPEN-SANDBOX-API-KEY": self._api_key},
                    json=body,
                )
                if resp.status_code not in (200, 202):
                    return {"error": f"control-plane returned {resp.status_code}"}
                return resp.json()
        except httpx.ConnectError as exc:
            return {"error": f"cannot reach control plane: {exc}"}
        except Exception as exc:
            logger.exception("control plane error")
            return {"error": str(exc)}

    async def delete_sandbox(self, sandbox_id: str) -> dict[str, Any] | dict[str, str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{self._base_url}/v1/sandboxes/{sandbox_id}",
                    headers={"OPEN-SANDBOX-API-KEY": self._api_key},
                )
                if resp.status_code not in (200, 204):
                    return {"error": f"control-plane returned {resp.status_code}"}
                return {"deleted": True, "id": sandbox_id}
        except httpx.ConnectError as exc:
            return {"error": f"cannot reach control plane: {exc}"}
        except Exception as exc:
            logger.exception("control plane error")
            return {"error": str(exc)}


class K8sClient:
    def __init__(self, namespace: str) -> None:
        self._namespace = namespace

    async def list_pods(self) -> list[dict[str, Any]]:
        try:
            from kubernetes_asyncio import client, config  # type: ignore[import]
            await config.load_kube_config()
            v1 = client.CoreV1Api()
            pod_list = await v1.list_namespaced_pod(self._namespace)
            result: list[dict[str, Any]] = []
            for pod in pod_list.items:
                result.append({
                    "pod_name": pod.metadata.name if pod.metadata else "",
                    "node_name": pod.spec.node_name if pod.spec else None,
                    "runtime_class": (
                        pod.spec.runtime_class_name if pod.spec else None
                    ),
                    "phase": pod.status.phase if pod.status else None,
                    "labels": pod.metadata.labels or {} if pod.metadata else {},
                })
            await v1.api_client.close()
            return result
        except Exception as exc:
            logger.warning("k8s pod listing failed: %s", exc)
            return []

    async def list_nodes(self) -> list[dict[str, Any]]:
        try:
            from kubernetes_asyncio import client, config  # type: ignore[import]
            await config.load_kube_config()
            v1 = client.CoreV1Api()
            node_list = await v1.list_node()
            result: list[dict[str, Any]] = []
            for node in node_list.items:
                result.append({
                    "name": node.metadata.name if node.metadata else "",
                })
            await v1.api_client.close()
            return result
        except Exception as exc:
            logger.warning("k8s node listing failed: %s", exc)
            return []

    async def get_pool_cr(self, pool_name: str) -> dict[str, Any]:
        """Fetch a Pool custom resource and return a normalized dict.

        Live status field names (confirmed from cluster):
          status.total, status.allocated, status.available
        Spec field names:
          spec.capacitySpec.poolMin/poolMax/bufferMin/bufferMax
        """
        try:
            from kubernetes_asyncio import client, config  # type: ignore[import]
            await config.load_kube_config()
            custom = client.CustomObjectsApi()
            cr = await custom.get_namespaced_custom_object(
                group="sandbox.opensandbox.io",
                version="v1alpha1",
                plural="pools",
                namespace=self._namespace,
                name=pool_name,
            )
            await custom.api_client.close()

            spec = cr.get("spec") or {}
            capacity = spec.get("capacitySpec") or {}
            status = cr.get("status") or {}

            return {
                "name": pool_name,
                # Live field names: total / allocated / available
                "total": int(status.get("total") or status.get("totalCount") or 0),
                "allocated": int(status.get("allocated") or status.get("allocatedCount") or 0),
                "available": int(status.get("available") or status.get("availableCapacity") or 0),
                "pool_min": int(capacity.get("poolMin") or 0),
                "pool_max": int(capacity.get("poolMax") or 0),
                "buffer_min": int(capacity.get("bufferMin") or 0),
                "buffer_max": int(capacity.get("bufferMax") or 0),
                "conditions": list(status.get("conditions") or []),
                "raw": cr,
            }
        except Exception as exc:
            logger.warning("k8s pool CR fetch failed for %s: %s", pool_name, exc)
            return {"error": str(exc), "name": pool_name}

    async def patch_pool_cr(self, pool_name: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Strategic-merge-patch a Pool CR's spec.capacitySpec fields.

        `patch` is a flat dict of pool_min / pool_max / buffer_min / buffer_max
        (any subset). They are translated to the camelCase field names on the
        wire. Returns the patched object or {"error": ...}.
        """
        capacity_patch: dict[str, Any] = {}
        for src, dst in (
            ("pool_min", "poolMin"),
            ("pool_max", "poolMax"),
            ("buffer_min", "bufferMin"),
            ("buffer_max", "bufferMax"),
        ):
            if src in patch and patch[src] is not None:
                capacity_patch[dst] = int(patch[src])
        if not capacity_patch:
            return {"error": "no capacity fields supplied", "name": pool_name}

        body = {"spec": {"capacitySpec": capacity_patch}}
        try:
            from kubernetes_asyncio import client, config  # type: ignore[import]
            await config.load_kube_config()
            custom = client.CustomObjectsApi()
            patched = await custom.patch_namespaced_custom_object(
                group="sandbox.opensandbox.io",
                version="v1alpha1",
                plural="pools",
                namespace=self._namespace,
                name=pool_name,
                body=body,
            )
            await custom.api_client.close()
            return patched
        except Exception as exc:
            logger.warning("k8s pool CR patch failed for %s: %s", pool_name, exc)
            return {"error": str(exc), "name": pool_name}

    async def list_events(self, since_seconds: int = 300, limit: int = 50) -> list[dict[str, Any]]:
        """List recent namespace events, sorted newest-first, capped to `limit`."""
        try:
            from datetime import timedelta

            from kubernetes_asyncio import client, config  # type: ignore[import]
            await config.load_kube_config()
            v1 = client.CoreV1Api()
            event_list = await v1.list_namespaced_event(self._namespace)
            await v1.api_client.close()

            cutoff = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
            result: list[dict[str, Any]] = []

            for ev in event_list.items:
                # Prefer lastTimestamp; fall back to eventTime or firstTimestamp
                ts_raw = (
                    ev.last_timestamp
                    or ev.event_time
                    or ev.first_timestamp
                )
                if ts_raw is None:
                    continue
                # kubernetes_asyncio returns datetime objects
                if isinstance(ts_raw, datetime):
                    ts_dt = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=timezone.utc)
                else:
                    # string fallback
                    try:
                        ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    except ValueError:
                        continue

                if ts_dt < cutoff:
                    continue

                involved = ev.involved_object
                result.append({
                    "ts": ts_dt.isoformat(),
                    "reason": ev.reason or "",
                    "type": ev.type or "Normal",
                    "message": ev.message or "",
                    "involved_object": {
                        "kind": involved.kind if involved else "",
                        "name": involved.name if involved else "",
                        "namespace": (involved.namespace if involved else "") or self._namespace,
                    },
                    "count": ev.count or 1,
                    "_sort_key": ts_dt,
                })

            result.sort(key=lambda x: x["_sort_key"], reverse=True)
            for item in result:
                del item["_sort_key"]

            return result[:limit]
        except Exception as exc:
            logger.warning("k8s event listing failed: %s", exc)
            return []


# ── KimiClient (Step 5) ──────────────────────────────────────────────────────

class KimiClient:
    """Foundry chat-completions proxy with cached AAD token.

    Lifts the call shape verbatim from examples/hypothesis_swarm.py:80-121.
    """

    # Class-level token cache — persists across requests (same process lifetime).
    _token: str | None = None
    _token_expires_at: float = 0.0  # unix timestamp

    def __init__(
        self,
        endpoint: str,
        deployments: tuple[str, ...],
        api_version: str,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._deployments = deployments
        self._api_version = api_version

    @classmethod
    async def _get_token(cls) -> str:
        """Return cached AAD token; refresh via az CLI if within 5 min of expiry."""
        import platform
        import shutil

        # Use cached token if still valid (5-minute safety margin).
        if cls._token and time.time() < cls._token_expires_at - 300:
            logger.debug("kimi: using cached token %s...", cls._token[:12])
            return cls._token

        # Locate az CLI.
        az_cmd: str | None = shutil.which("az")
        if az_cmd is None and platform.system() == "Windows":
            az_cmd = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"

        def _run_az_token() -> subprocess.CompletedProcess:  # type: ignore[type-arg]
            cmd = (
                f'"{az_cmd}"' if az_cmd and " " in az_cmd else (az_cmd or "az")
            )
            return subprocess.run(
                f"{cmd} account get-access-token "
                "--resource https://cognitiveservices.azure.com -o json",
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                shell=True,
            )

        result = await anyio.to_thread.run_sync(_run_az_token)
        if result.returncode != 0:
            raise httpx.HTTPError(
                f"az account get-access-token failed: {result.stderr.strip()[:200]}"
            )

        data = json.loads(result.stdout)
        token: str = data["accessToken"]
        # expiresOn is "YYYY-MM-DD HH:MM:SS.ffffff" (local time, not UTC).
        expires_on_str: str = data.get("expiresOn", "")
        try:
            from datetime import datetime as _dt
            expires_dt = _dt.strptime(expires_on_str[:19], "%Y-%m-%d %H:%M:%S")
            expires_ts = expires_dt.timestamp()
        except Exception:
            # Fallback: assume 60 minutes from now.
            expires_ts = time.time() + 3600

        cls._token = token
        cls._token_expires_at = expires_ts
        logger.info("kimi: minted new AAD token %s... expires %.0f", token[:12], expires_ts)
        return token

    async def chat(
        self,
        messages: list[dict],
        deployment: str | None = None,
        max_tokens: int = 16000,
        temperature: float = 0.7,
    ) -> dict:
        """POST to Foundry chat-completions with retry+fallback.

        Returns:
            {"deployment_used": str, "duration_s": float,
             "message": {"role": "assistant", "content": str}}
            or {"error": str} on total failure.
        """
        dep_order: tuple[str, ...] = (
            (deployment,) + tuple(d for d in self._deployments if d != deployment)  # type: ignore[assignment]
            if deployment
            else self._deployments
        )

        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        for dep in dep_order:
            url = (
                f"{self._endpoint}/openai/deployments/{dep}"
                f"/chat/completions?api-version={self._api_version}"
            )
            for attempt in range(3):
                try:
                    token = await self._get_token()
                    t0 = time.time()
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        resp = await client.post(
                            url,
                            json=payload,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json",
                            },
                        )
                    duration_s = time.time() - t0

                    if resp.status_code == 401:
                        # Force token refresh on next attempt.
                        KimiClient._token = None
                        logger.warning(
                            "kimi: 401 on %s attempt %d, refreshing token",
                            dep, attempt + 1,
                        )
                        continue

                    if resp.status_code == 429:
                        sleep_s = 2 ** attempt
                        logger.warning(
                            "kimi: 429 on %s attempt %d, sleeping %ds",
                            dep, attempt + 1, sleep_s,
                        )
                        await anyio.sleep(sleep_s)
                        continue

                    if resp.status_code != 200:
                        logger.warning(
                            "kimi: HTTP %d on %s attempt %d: %s",
                            resp.status_code, dep, attempt + 1,
                            resp.text[:200],
                        )
                        break  # non-retryable; try next deployment

                    data = resp.json()
                    msg = data["choices"][0]["message"]
                    content = msg.get("content") or msg.get("reasoning_content") or ""
                    if content.strip():
                        return {
                            "deployment_used": dep,
                            "duration_s": round(duration_s, 3),
                            "message": {"role": "assistant", "content": content},
                        }
                    # Empty response — fall through to next deployment.
                    break

                except httpx.HTTPError as exc:
                    logger.warning(
                        "kimi: HTTP error on %s attempt %d: %s",
                        dep, attempt + 1, exc,
                    )
                    if attempt < 2:
                        await anyio.sleep(2 ** attempt)
                except Exception as exc:
                    logger.warning(
                        "kimi: error on %s attempt %d: %s",
                        dep, attempt + 1, exc,
                    )
                    if attempt < 2:
                        await anyio.sleep(2 ** attempt)

        return {"error": "all Kimi deployments failed"}
