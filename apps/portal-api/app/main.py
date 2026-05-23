from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .clients import ControlPlaneClient, K8sClient
from .config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="DarkForge Portal API", version="0.1.0")

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


@app.get("/")
async def index() -> FileResponse:
    index_file = _FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    from fastapi.responses import HTMLResponse
    return HTMLResponse("<h1>DarkForge Portal</h1><p>Frontend dist not found.</p>")  # type: ignore[return-value]
