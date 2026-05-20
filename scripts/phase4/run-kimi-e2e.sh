#!/usr/bin/env bash
# Phase 4: End-to-end Kimi K2.5 agent run against the deployed OpenSandbox stack.
#
# Prereqs (set by Phase 3 deploy):
#   - kubectl context pointing at the AKS cluster (`az aks get-credentials ...`)
#   - The Helm chart installed (Phase 3.helm)
#   - Control-plane FQDN reachable from the runner (you'll need to either run this
#     inside the VNet or set up a temporary jumpbox / port-forward).
#
# Outputs:
#   evidence/runs/kimi-e2e-<timestamp>/
#     ├── 01-healthz.txt          control-plane /healthz response
#     ├── 02-runtimeclass.txt     kubectl get runtimeclass kata-vm-isolation
#     ├── 03-prewarm.txt          kubectl get ds image-prewarm -A
#     ├── 04-create-session.json  POST /sessions response
#     ├── 05-run-task-A.txt       data-analysis task transcript
#     ├── 06-run-task-B.txt       sandbox-isolation-test transcript
#     ├── 07-run-task-C.txt       multi-step-coding transcript
#     ├── 08-delete-session.json  DELETE /sessions/<id> response
#     ├── 09-audit-kql.json       SandboxAuditFast_CL row for trace_id
#     └── 10-trace-kql.json       App Insights span graph for trace_id
#
# Usage:
#   API_URL=https://opensandbox.eastus2.cloudapp.azure.com \
#   USER_TOKEN=$(az account get-access-token --resource <api-app-id> -q accessToken) \
#   LAW_WORKSPACE_ID=... \
#   ./scripts/phase4/run-kimi-e2e.sh
#
set -euo pipefail

: "${API_URL:?Set API_URL to the control-plane URL}"
: "${USER_TOKEN:?Set USER_TOKEN to an Entra Bearer for the API app}"

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="evidence/runs/kimi-e2e-${TS}"
mkdir -p "${OUT}"
echo "Writing evidence to ${OUT}"

# 1. Health check
echo "[1/10] /healthz"
curl -sS "${API_URL}/healthz" -o "${OUT}/01-healthz.txt" || echo "(failed)" > "${OUT}/01-healthz.txt"

# 2. RuntimeClass + 3. pre-warm DaemonSet (require kubectl)
if command -v kubectl >/dev/null 2>&1; then
  echo "[2/10] kubectl runtimeclass"
  kubectl get runtimeclass kata-vm-isolation -o yaml > "${OUT}/02-runtimeclass.txt" 2>&1 || true
  echo "[3/10] kubectl daemonsets"
  kubectl get ds -A -l app=image-prewarm -o wide > "${OUT}/03-prewarm.txt" 2>&1 || true
else
  echo "kubectl not on PATH; skipping cluster-level evidence" | tee "${OUT}/02-runtimeclass.txt" "${OUT}/03-prewarm.txt"
fi

# 4-8: SDK + agent
echo "[4-8/10] Kimi agent end-to-end"
export OPENSANDBOX_API_URL="${API_URL}"
export OPENSANDBOX_USER_TOKEN="${USER_TOKEN}"
export EVIDENCE_DIR="${OUT}"
cd examples/kimi-agent-demo
python -m agent --tasks all --evidence-dir "${EVIDENCE_DIR}" 2>&1 | tee "${OUT}/agent.log"
cd ../..

# 9-10: KQL evidence — only if LAW_WORKSPACE_ID is set
if [[ -n "${LAW_WORKSPACE_ID:-}" ]]; then
  TRACE_ID=$(jq -r .trace_id "${OUT}/04-create-session.json" 2>/dev/null || echo "")
  if [[ -n "${TRACE_ID}" ]]; then
    echo "[9/10] LAW audit query for trace_id=${TRACE_ID}"
    az monitor log-analytics query \
      --workspace "${LAW_WORKSPACE_ID}" \
      --analytics-query "SandboxAuditFast_CL | where trace_id_s == '${TRACE_ID}' | take 50" \
      -o json > "${OUT}/09-audit-kql.json" 2>&1 || true
  fi
fi

echo "Done. Evidence at ${OUT}"
ls -la "${OUT}"
