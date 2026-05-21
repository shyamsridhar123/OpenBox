# FINISH-7 — ACA control plane wiring runbook

> Status: **Live operation deferred. Bicep module + runbook here.**

## Why this matters

The original architecture moved `opensandbox-server` off AKS and onto
Azure Container Apps (ACA), keeping AKS purely for sandbox workload pods.
Why:

- ACA scales-to-zero on idle, AKS deployment replicas don't
- ACA has built-in revision-based rollback, simpler than Helm rollback
- Decouples server lifecycle from cluster lifecycle (cluster ops don't
  page on server restarts)
- Frees the system nodepool from running the control plane

Today the server runs in `opensandbox-system` on AKS — it works, but
this is the original-brief gap to close.

## Bicep state

`infra/bicep/modules/aca.bicep` already provisions a Container Apps
Environment in `snet-aca` with the right subnet delegation
(`Microsoft.App/environments`). The Container App resource needs adding
on top of that.

## Deployment runbook

```bash
# 0. Pre-flight: confirm AKS-resident server is healthy (so we can compare).
curl -s http://localhost:18080/health  # via existing port-forward → 200

# 1. Build a Bicep delta that adds a `opensandbox-server` container app
#    pointing at the existing acropensandboxdemo7075/opensandbox/server:v0.1.14.
#    Bind it to the ACA Environment in snet-aca, give it a managed
#    identity with AcrPull on the registry, mount the API key from KV.
#    File: infra/bicep/modules/aca-server.bicep
#    Status: NOT YET WRITTEN — would be the live execution step.

# 2. The server needs to reach the AKS API server to create CRDs. Two options:
#    a) Embed a kubeconfig in KV, mount as secret. Simpler.
#    b) Use Workload Identity federation from ACA → AKS. Cleaner but
#       requires AKS to be in the same Entra tenant (it is — same tenant
#       as the laptop's az login).
#    Pick (a) for v1.

# 3. Deploy the new server, leave the AKS one running in parallel.
az deployment group create -g rg-opensandbox-dev \
  --template-file infra/bicep/modules/aca-server.bicep \
  --parameters env=dev

# 4. Point an SDK config at the ACA server's FQDN.
ACA_FQDN=$(az containerapp show -g rg-opensandbox-dev -n opensandbox-server \
  --query "properties.configuration.ingress.fqdn" -o tsv)

# Test against the new endpoint.
DOMAIN=$ACA_FQDN /tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py

# 5. If green, point AppGW backend pool at the ACA app, drain the
#    in-cluster server. (See infra/bicep/modules/appgw.bicep for routing.)

# 6. helm uninstall opensandbox or `helm upgrade --set server.enabled=false`
#    to scale the in-cluster server to zero.
```

## Acceptance verification

- AC-29 ✅ ← opensandbox-server runs on ACA, AKS deployment scaled to 0
- AC-30 ✅ ← AppGW backend points at ACA app, Healthy
- `evidence/runs/finish/AC-CHECKLIST.md` rows 29 + 30 → flip 🟡 to ✅

## Risk

The opensandbox-server reaches into the AKS API to create BatchSandbox
CRDs. Moving it off-cluster means giving an ACA app cluster-wide kube
permissions, which is a real privilege expansion. The mitigation is
(a) the API key gate on the server's own ingress, (b) a tightly-scoped
ClusterRole limited to `batchsandboxes.opensandbox.alibaba.com/*` and
the few core verbs the controller needs. Audit-log the cluster role
binding before flipping traffic.
