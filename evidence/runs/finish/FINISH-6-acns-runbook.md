# FINISH-6 — ACNS observability enablement runbook

> Status: **Live operation deferred to maintenance window. Runbook + verification queries authored here.**

## What ACNS gives us

Azure Container Networking Services (ACNS) bolts Cilium + Hubble onto an
AKS cluster as the data plane and adds first-class network observability:

- **Flow logs** — every L3/L4 connection between pods, with policy verdict,
  surfaced as a queryable Log Analytics table.
- **Hubble UI** — an interactive flow viewer, useful when triaging "why
  can't this sandbox reach pypi" without `tcpdump`-ing nodes.
- **L7 policies** (optional) — HTTP/gRPC-aware NetworkPolicy beyond the
  base K8s networking primitives.

## Why deferred

Enabling ACNS on a live cluster requires recreating the cluster's network
data plane (`--network-dataplane cilium`). On AKS that's a control-plane
operation that **drains all nodes and re-creates them**. Cost is ~20-40
minutes of cluster downtime + the risk that running sandboxes get killed
mid-execution. The existing E2E (RUN-4, FINISH-1) is greenfield-friendly,
but doing it in the same session as 5 other risky ops is a recipe for
finishing the session with a broken cluster.

## Deployment runbook

```bash
# 0. Pre-flight: confirm we don't have running sandboxes we care about.
kubectl get pods -n opensandbox | grep -v 'Completed\|Terminating'

# 1. Drain any active sandboxes (they can't survive node recreation).
kubectl delete pods -n opensandbox --all --grace-period=30

# 2. Enable ACNS observability + security on the cluster.
az aks update \
  -g rg-opensandbox-dev \
  -n aks-opensandbox-dev \
  --enable-acns

# 3. Wait for nodepool reroll. ~20-40 minutes.
az aks show -g rg-opensandbox-dev -n aks-opensandbox-dev \
  --query "provisioningState" -o tsv
# Loop until 'Succeeded'.

# 4. Verify Cilium data plane.
kubectl get pods -n kube-system | grep cilium
# Expect: cilium-* DaemonSet, cilium-operator-* Deployment, hubble-* pods.

# 5. Verify control plane survives.
kubectl get pods -n opensandbox-system
# Expect: opensandbox-controller-manager-* and opensandbox-server-* Running.

# 6. Re-run E2E.
/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py
# Expect RUN-4 SUCCESS again.

# 7. Open Hubble UI.
kubectl port-forward -n kube-system svc/hubble-relay 8081:80 &
# Then browse http://localhost:8081 — capture screenshot row 22, 23.

# 8. Query flow logs in Log Analytics.
az monitor log-analytics query \
  --workspace $(az aks show -g rg-opensandbox-dev -n aks-opensandbox-dev \
    --query "addonProfiles.omsagent.config.logAnalyticsWorkspaceResourceID" -o tsv) \
  --analytics-query "NetworkFlowLogs | take 10"
# Should return rows with source/dest pods, ports, verdicts.
```

## Acceptance verification

- AC-26 ✅ ← ACNS enabled, Cilium data plane up
- AC-27 ✅ ← Hubble flow rows visible
- `evidence/runs/finish/AC-CHECKLIST.md` rows 26 + 27 → flip 🟡 to ✅

## Rollback

ACNS enablement is one-way on AKS. You CANNOT disable the data plane back
to kubenet/calico without recreating the cluster. Before step 2, be
certain — there's no undo.
