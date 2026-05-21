# Operations

Day-2 runbook index for OpenBox on Azure. The per-slice runbooks under
[`evidence/runs/finish/`](../evidence/runs/finish/) are the source of truth for each component —
this document is the entry point.

## Runbook index

| Slice | Topic | Runbook |
|---|---|---|
| FINISH-4 | Azure Firewall Premium policy + UDR for Kata egress | [evidence/runs/finish/FINISH-4-fw-runbook.md](../evidence/runs/finish/FINISH-4-fw-runbook.md) |
| FINISH-5 | ACR Premium private endpoint + DNS | [evidence/runs/finish/FINISH-5-acr-pe-runbook.md](../evidence/runs/finish/FINISH-5-acr-pe-runbook.md) |
| FINISH-6 | Cilium ACNS, Hubble UI, L7 FQDN policies | [evidence/runs/finish/FINISH-6-acns-runbook.md](../evidence/runs/finish/FINISH-6-acns-runbook.md) |
| FINISH-7 | ACA environment + control-plane container apps | [evidence/runs/finish/FINISH-7-aca-runbook.md](../evidence/runs/finish/FINISH-7-aca-runbook.md) (in progress) |
| FINISH-8 | Event Hubs + Stream Analytics audit pipeline | [evidence/runs/finish/FINISH-8-audit-runbook.md](../evidence/runs/finish/FINISH-8-audit-runbook.md) |

Older general runbooks (incident response, onboarding, CVE response, DR drill) live in
[`runbooks/`](../runbooks/).

## 60-second cluster health checklist

Run this before changing anything in `rg-opensandbox-dev`. Anything red is a stop-the-line.

```bash
# 0. Context
az aks get-credentials -g rg-opensandbox-dev -n aks-opensandbox-dev --overwrite-existing

# 1. Nodes Ready, including the Kata pool
kubectl get nodes -o wide
# Expected: 3 system + N kata nodes, all Ready, kernel 6.6.x on kata nodes

# 2. Kata runtime class present
kubectl get runtimeclass kata-vm-isolation
# Expected: object present, handler kata

# 3. Control plane up
kubectl -n opensandbox-system get deploy
# Expected: opensandbox-server and opensandbox-controller-manager both 1/1

# 4. No crash-looping pods anywhere
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
# Expected: no rows (or only short-lived Completed jobs)

# 5. Firewall provisioning state
az network firewall show -g rg-opensandbox-dev -n afw-opensandbox-dev --query provisioningState -o tsv
# Expected: Succeeded

# 6. ACR reachable via private endpoint (from a pod)
kubectl run -n default acr-probe --rm -it --restart=Never --image=mcr.microsoft.com/azure-cli -- \
  nslookup acropensandboxdemo7075.azurecr.io
# Expected: resolves to 10.10.12.6 (private)

# 7. Audit pipeline alive
az stream-analytics job show -g rg-opensandbox-dev -n asa-opensandbox-audit-dev --query jobState -o tsv
# Expected: Running

# 8. Smoke a sandbox
python evidence/runs/finish/sdk_e2e.py
# Expected last line: RUN-4 SUCCESS
```

If any step fails, drop into the corresponding FINISH-* runbook above.

## How to add a new sandbox image

Curated images live in `infra/helm/opensandbox/` (chart values) and are built into the ACR with
`az acr build`. The Helm chart's `images.sandbox.allowedList` controls which images the
controller will accept; an image not on that list is rejected before scheduling.

```bash
# 1. Author the Dockerfile under a new directory
mkdir -p infra/images/julia
$EDITOR infra/images/julia/Dockerfile

# 2. Build into ACR (private; the task uses the Azure-managed runner)
az acr build \
  --registry acropensandboxdemo7075 \
  --resource-group rg-opensandbox-demo \
  --image sandbox/julia:1.10 \
  infra/images/julia

# 3. Tag verification (note: registry has public access disabled, so use az)
az acr repository show-tags --name acropensandboxdemo7075 --repository sandbox/julia

# 4. Update the chart values to include the new image
$EDITOR infra/helm/opensandbox/values.dev.yaml
# Add "sandbox/julia:1.10" to images.sandbox.allowedList

# 5. Upgrade in place
helm upgrade opensandbox infra/helm/opensandbox \
  -n opensandbox-system \
  -f infra/helm/opensandbox/values.dev.yaml

# 6. Smoke the new image
python - <<'EOF'
import asyncio
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
# ... use sandbox/julia:1.10 in place of python:3.12-slim
EOF
```

If the controller rejects the image, check
`kubectl -n opensandbox-system logs deploy/opensandbox-controller-manager` for the validation
error — usually a typo in the allow-list entry.

## How to rotate the OPENSANDBOX_SERVER_API_KEY

The server's API key gates every request that comes in from outside the cluster (laptop SDK
calls, ACA calls, anything). It lives in two places that must stay in sync: the Helm secret on
the cluster, and the operator's local copy at `evidence/runs/finish/.opensandbox-api-key` (and
analogous places for any other clients).

```bash
# 1. Generate a new key (URL-safe, 48 bytes of entropy)
NEW_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')

# 2. Store it in Key Vault (versioned)
az keyvault secret set \
  --vault-name kv-opensandbox-dev \
  --name opensandbox-server-api-key \
  --value "$NEW_KEY"

# 3. Update the Kubernetes secret (the Helm chart reads OPENSANDBOX_SERVER_API_KEY)
kubectl -n opensandbox-system create secret generic opensandbox-server \
  --from-literal=OPENSANDBOX_SERVER_API_KEY="$NEW_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Roll the server deployment
kubectl -n opensandbox-system rollout restart deploy/opensandbox-server
kubectl -n opensandbox-system rollout status deploy/opensandbox-server

# 5. Refresh local copies for the demo scripts
echo "$NEW_KEY" > evidence/runs/finish/.opensandbox-api-key

# 6. Smoke
python evidence/runs/finish/sdk_e2e.py
```

The Stream Analytics job and Fluent Bit DaemonSet do NOT use this key (they authenticate to
Event Hubs via Managed Identity), so they are unaffected by rotation.

## How to rebuild execd and roll out a new version

The `execd` binary is the sidecar+init container inside every sandbox pod. After the CRLF fix
in v1.0.8 the build is stable; the procedure below is for bumping the version or applying a
security patch.

```bash
# 0. From repo root
cd third_party/opensandbox/components/execd

# 1. Build inside the ACR (do not build locally on Windows — CRLF risk)
az acr build \
  --registry acropensandboxdemo7075 \
  --resource-group rg-opensandbox-demo \
  --image opensandbox/execd:v1.0.9 \
  --file Dockerfile \
  ../..

# 2. Verify .sh files in the new image are LF-only
docker run --rm acropensandboxdemo7075.azurecr.io/opensandbox/execd:v1.0.9 \
  sh -c 'file /bootstrap.sh; head -1 /bootstrap.sh | od -c | head -1'
# Expected: "ASCII text", first line ending in \n not \r\n

# 3. Update the chart and roll
$EDITOR infra/helm/opensandbox/values.dev.yaml   # bump execd.image.tag

helm upgrade opensandbox infra/helm/opensandbox \
  -n opensandbox-system \
  -f infra/helm/opensandbox/values.dev.yaml

# 4. Existing sandbox pods are NOT rolled (they belong to the controller, not Helm).
#    Create a fresh sandbox to verify the new execd is the one being injected.
python evidence/runs/finish/sdk_e2e.py

# 5. Confirm the running pod is on the new tag
kubectl -n <sandbox-ns> describe pod <sandbox-pod> | grep -i image:
```

If the new pod refuses to start past `execd-init`, dump the init container logs and look for the
CRLF symptom (see [docs/ARCHITECTURE.md#the-crlf-bootstrap-story](ARCHITECTURE.md#the-crlf-bootstrap-story)).

## Where to look next

- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — what each component does and how it connects.
- [docs/acceptance-checklist.md](acceptance-checklist.md) — the 34 acceptance criteria.
- [ROADMAP.md](../ROADMAP.md) — what's done, deferred, and next.
- [runbooks/incident-response.md](../runbooks/incident-response.md) — generic IR procedure.
