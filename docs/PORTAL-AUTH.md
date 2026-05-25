# Portal Auth: Dev Mode vs Prod Mode

## Dev Mode (current — Portal v2)

The portal runs entirely as the **developer's local identity**. No service principal, no managed identity, no OAuth front-door.

| Resource | How it's accessed |
|---|---|
| AKS cluster (`az aks start/stop`) | `az` CLI — uses the logged-in `az account` session |
| Kubernetes API (pods, nodes, events, Pool CRs) | `kubectl` kubeconfig — `load_kube_config()` in `apps/portal-api/app/clients.py:48` |
| OpenSandbox control plane | HTTP + API key from `apps/portal-api/app/config.py:_read_key_file` (reads `examples/.opensandbox-api-key`) |
| Kimi / Foundry | AAD bearer token via `az account get-access-token` — minted as the signed-in user |

**Constraints that make dev mode safe:**
- CORS is restricted to `localhost` origins (see `main.py`).
- A startup banner (logged at WARNING level) names the `az` user, subscription, kubectl context, and namespace so the developer cannot accidentally act on the wrong cluster.
- The `GET /api/identity` endpoint surfaces the same information in the UI header chip.

**This is intentionally single-user-on-localhost.** Do not run this portal on a shared host or expose it via a public ingress without implementing prod mode.

---

## Prod Mode (not yet implemented)

Prod mode replaces every ambient-credential touch-point with an explicit, least-privilege identity.

### What changes

| Concern | Dev mode | Prod mode |
|---|---|---|
| Kubernetes API access | `load_kube_config()` (local `~/.kube/config`) | `load_incluster_config()` — pod runs with a Workload Identity-bound service account |
| `kubectl` actions (RBAC) | Developer's cluster-admin kubeconfig | Managed Identity with a scoped ClusterRole (list/watch pods, get Pool CRs, list events) bound via `ClusterRoleBinding` |
| `az aks start/stop` | Developer's `az` session | Managed Identity with **AKS Cluster Admin** role (`Microsoft.ContainerService/managedClusters/start/action`, `…/stop/action`) on the AKS resource |
| Kimi / Foundry token | Developer's AAD token | Managed Identity with **Cognitive Services OpenAI User** role on the AI Hub resource |
| API key file | Local file `examples/.opensandbox-api-key` | Key Vault CSI secret mount — secret injected as a file at the same path inside the pod |
| User authentication | None (localhost-only) | Easy Auth on Azure App Service **or** Azure AD Application Proxy in front of the portal pod, scoped to the dev Entra group |

### Migration checklist (6 steps)

1. **Workload Identity setup** — Create a user-assigned Managed Identity; federate it with the AKS OIDC issuer for the portal's Kubernetes service account (`kubectl annotate serviceaccount portal-api azure.workload.identity/client-id=<MI_CLIENT_ID>`).

2. **Kubeconfig swap** — In `apps/portal-api/app/clients.py:48`, replace `await kubernetes_asyncio.config.load_kube_config()` with `kubernetes_asyncio.config.load_incluster_config()`. Guard with an env flag (`IN_CLUSTER=true`) so dev mode still works locally.

3. **RBAC bindings** — Create a `ClusterRole` with rules for `pods`, `nodes`, `events`, and the `sandbox.opensandbox.io/pools` custom resource. Bind it to the portal's service account via `ClusterRoleBinding`.

4. **Managed Identity role assignments** — Grant the MI:
   - `Azure Kubernetes Service Cluster Admin Role` on `aks-opensandbox-dev` (for start/stop)
   - `Cognitive Services OpenAI User` on the AI Hub (for Kimi tokens)

5. **OAuth front-door** — Enable Easy Auth (App Service Authentication) on the portal's App Service, or configure Azure AD Application Proxy. Restrict to the relevant Entra security group.

6. **Secret mount** — Store the OpenSandbox API key in Key Vault. Use the AKS Key Vault CSI driver to mount it as a file at `examples/.opensandbox-api-key` (matching `apps/portal-api/app/config.py:_read_key_file`). No code changes needed — the path resolution is already environment-agnostic.

---

## Relevant code locations

- **Identity resolution**: `apps/portal-api/app/identity.py` — calls `az account show` and `kubectl config current-context`; all results are `None`-safe.
- **Kubeconfig load point**: `apps/portal-api/app/clients.py:48` — the single line to swap for prod (`load_incluster_config()`).
- **API key read**: `apps/portal-api/app/config.py:_read_key_file` — reads `REPO_ROOT/examples/.opensandbox-api-key`; replace with CSI mount at the same resolved path.
