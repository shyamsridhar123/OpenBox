# DarkForge Portal API

FastAPI backend that joins the OpenSandbox control-plane with live Kubernetes data and serves the frontend.

## Security

> **⚠️ DEV ONLY — runs as YOU.** This portal uses the developer's local `az` session, kubeconfig, and API key file. It can start/stop the cluster, create/delete sandboxes, and call Kimi as the signed-in user. Do not expose it beyond `localhost` without reading [`docs/PORTAL-AUTH.md`](../../docs/PORTAL-AUTH.md).

## Prerequisites

- Python 3.12+
- `uv` installed
- `kubectl` configured with a valid kubeconfig pointing at your AKS cluster
- Port-forward to the in-cluster control plane:
  ```
  kubectl port-forward -n opensandbox-system svc/opensandbox-server 18080:80
  ```

## Run

```bash
cd apps/portal-api
uv sync
uv run uvicorn app.main:app --reload --port 8090
```

Open http://localhost:8090 for the dashboard.

## Environment

| Variable | Default | Description |
|---|---|---|
| `CONTROL_PLANE_URL` | `http://localhost:18080` | Base URL of the OpenSandbox control plane |
| `CONTROL_PLANE_API_KEY` | *(from key file)* | API key; falls back to reading the local key file |
| `OPENSANDBOX_NAMESPACE` | `opensandbox` | Kubernetes namespace for sandbox pods (the control-plane itself lives in `opensandbox-system`) |

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET    | `/api/health` | Liveness check |
| GET    | `/api/identity` | Resolved az user, subscription, kubectx, namespace, key file presence |
| GET    | `/api/cluster/state` | AKS provisioning + power state (2 s cached) |
| GET    | `/api/cluster/summary` | Node and pod counts by pool |
| POST   | `/api/cluster/start` | `az aks start --no-wait` |
| POST   | `/api/cluster/stop` | `az aks stop --no-wait` |
| GET    | `/api/sandboxes` | List sandboxes with pod/node enrichment |
| POST   | `/api/sandboxes` | Proxy create-sandbox to the control plane |
| DELETE | `/api/sandboxes/{id}` | Proxy delete-sandbox to the control plane |
| POST   | `/api/sandbox/exec` | Run a Python snippet in a fresh Kata VM; auto-captures matplotlib charts as base64 PNG. Spawns `.venv-swarm/Scripts/python.exe examples/run_in_sandbox.py`. |
| GET    | `/api/pool/{name}` | Normalized Pool CR (`total/allocated/available/poolMax/bufferMin`) |
| GET    | `/api/events` | Recent namespace events (newest first) |
| POST   | `/api/swarm/runs` | Kick off `examples/hypothesis_swarm.py` |
| GET    | `/api/swarm/runs` | List active + recent runs |
| GET    | `/api/swarm/runs/{id}/events` | SSE stream (phase, result, summary, log, done) |
| DELETE | `/api/swarm/runs/{id}` | Cancel a run |
| POST   | `/api/kimi/chat` | Foundry chat-completions proxy (K2.6 default, K2.5 auto-fallback) |
| GET    | `/` | Serves frontend `index.html` |

> For DEV-MODE vs prod-mode auth, see [`../../docs/PORTAL-AUTH.md`](../../docs/PORTAL-AUTH.md).
