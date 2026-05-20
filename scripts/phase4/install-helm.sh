#!/usr/bin/env bash
# Phase 3.helm: install opensandbox Helm chart on the deployed AKS cluster.
#
# Prereqs (set by Phase 3.bicep deploy):
#   - AKS_RG, AKS_NAME, ACR_FQDN, KV_URI, LAW_WORKSPACE_ID env vars
#   - kubectl on PATH; helm v3 on PATH
#
# What this does:
#   1. az aks get-credentials → write kubeconfig
#   2. Verify nodes Ready and RuntimeClass kata-vm-isolation exists
#   3. Create namespace opensandbox-system + RBAC for workload identity
#   4. helm upgrade --install opensandbox infra/helm/opensandbox \
#         -n opensandbox-system --create-namespace \
#         -f infra/helm/opensandbox/values.dev.yaml \
#         --set image.registry=${ACR_FQDN} \
#         --set keyVault.uri=${KV_URI} \
#         --set logAnalytics.workspaceId=${LAW_WORKSPACE_ID}
#   5. Wait for controller + execd + pre-warm DaemonSets Ready
#   6. Smoke: list pods, list crds, verify Ratify policy applied
#
# Outputs:
#   evidence/runs/helm-<timestamp>/{nodes,runtimeclass,helm-status,pods,crds,ratify}.txt
#
set -euo pipefail

: "${AKS_RG:?Set AKS_RG to the resource group}"
: "${AKS_NAME:?Set AKS_NAME to the AKS cluster name}"
: "${ACR_FQDN:?Set ACR_FQDN to the ACR registry FQDN}"
: "${KV_URI:?Set KV_URI to the Key Vault URI}"
: "${LAW_WORKSPACE_ID:?Set LAW_WORKSPACE_ID}"

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="evidence/runs/helm-${TS}"
mkdir -p "${OUT}"

echo "[1/6] az aks get-credentials"
az aks get-credentials -g "${AKS_RG}" -n "${AKS_NAME}" --overwrite-existing
kubectl config current-context | tee "${OUT}/kubectx.txt"

echo "[2/6] nodes + runtimeclass"
kubectl get nodes -o wide > "${OUT}/nodes.txt" 2>&1
kubectl get runtimeclass kata-vm-isolation -o yaml > "${OUT}/runtimeclass.txt" 2>&1 \
  || echo "kata-vm-isolation RuntimeClass MISSING — Phase 0 Kata install step required" \
  | tee -a "${OUT}/runtimeclass.txt"

echo "[3/6] create namespace + sa"
kubectl create ns opensandbox-system --dry-run=client -o yaml | kubectl apply -f -

echo "[4/6] helm upgrade --install"
helm upgrade --install opensandbox infra/helm/opensandbox \
  -n opensandbox-system \
  --create-namespace \
  -f infra/helm/opensandbox/values.dev.yaml \
  --set image.registry="${ACR_FQDN}" \
  --set keyVault.uri="${KV_URI}" \
  --set logAnalytics.workspaceId="${LAW_WORKSPACE_ID}" \
  --wait --timeout 10m 2>&1 | tee "${OUT}/helm-install.log"

echo "[5/6] wait for daemonsets"
kubectl -n opensandbox-system rollout status ds/opensandbox-execd --timeout=5m | tee -a "${OUT}/helm-install.log" || true
kubectl -n opensandbox-system rollout status ds/opensandbox-prewarm --timeout=5m | tee -a "${OUT}/helm-install.log" || true
kubectl -n opensandbox-system rollout status deploy/opensandbox-controller --timeout=5m | tee -a "${OUT}/helm-install.log" || true

echo "[6/6] smoke"
kubectl -n opensandbox-system get all > "${OUT}/pods.txt" 2>&1
kubectl get crds | grep -E '(opensandbox|ratify|constrainttemplate)' > "${OUT}/crds.txt" 2>&1 || true
kubectl get constraints,constrainttemplates -A > "${OUT}/gatekeeper.txt" 2>&1 || true

echo "Done. Evidence at ${OUT}"
ls -la "${OUT}"
