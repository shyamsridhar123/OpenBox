# DarkForge Portal API

FastAPI backend that joins the OpenSandbox control-plane with live Kubernetes data and serves the frontend.

## Prerequisites

- Python 3.12+
- `uv` installed
- `kubectl` configured with a valid kubeconfig pointing at your AKS cluster
- Port-forward to the in-cluster control plane:
  ```
  kubectl port-forward -n opensandbox svc/control-plane 18080:8080
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
| `OPENSANDBOX_NAMESPACE` | `opensandbox` | Kubernetes namespace to watch |

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness check |
| GET | `/api/sandboxes` | List sandboxes with pod/node enrichment |
| GET | `/api/cluster/summary` | Node and pod counts by pool |
| GET | `/` | Serves frontend `index.html` |
