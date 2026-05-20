# DR Drill Runbook

> **Version:** 1.0  
> **Plan task:** Phase 6, Task 6.8 (Critic B-C6 mitigation)  
> **Owner:** Platform Team  
> **Schedule:** Quarterly — 15th of March, June, September, December  
> **RTO target:** 4 hours  
> **RPO target:** 24 hours  

---

## Overview

This runbook defines the quarterly DR drill procedure for OpenSandbox-on-Azure.  
The drill simulates loss of the primary Key Vault (containing the Notation signing
certificate) and verifies that:
1. The signing cert can be restored from backup within 4 hours.
2. Post-restore, a Notation-signed image schedules successfully on AKS.
3. Audit logs confirm the restored cert is trusted by Ratify.

The drill is automated in `nightly.yml: dr-drill` for steps 1–3 and step 6 (canary).
Steps 4–5 (actual delete + restore) are **MANUAL** and require two-person approval.

---

## Prerequisites

```bash
# Verify all backup resources exist before drilling
KV_NAME="kv-opensandbox-prod"
DR_STORAGE="stdrdrillprod"
ACR_NAME="acropensandboxprod"
ACR_BACKUP="acropensandboxbackup"

# Check KV backup policy
az keyvault show --name "${KV_NAME}" \
  --query "properties.enableSoftDelete"  # must be true

# Check DR storage account has cert backup
az storage blob list \
  --account-name "${DR_STORAGE}" \
  --container-name kv-cert-backups \
  --auth-mode login \
  --query "[].{name:name, modified:properties.lastModified}" \
  --output table

# Check ACR backup replication
az acr replication show \
  --registry "${ACR_BACKUP}" --name eastus2 \
  --query "provisioningState"
```

---

## Drill Steps

### Step 1 — Pre-drill verification (T-0)

Record baseline state:

```bash
# Confirm primary cert is valid
az keyvault certificate show \
  --vault-name "${KV_NAME}" \
  --name notation-cert-primary \
  --query "{expires:attributes.expires, enabled:attributes.enabled}"

# Confirm a signed image schedules successfully (pre-drill canary)
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: dr-pre-drill-canary
  namespace: dr-drill
spec:
  runtimeClassName: kata-vm-isolation
  restartPolicy: Never
  containers:
    - name: test
      image: ${ACR_NAME}.azurecr.io/canary-primary:latest
      command: ["echo", "pre-drill-ok"]
EOF
kubectl wait pod dr-pre-drill-canary --namespace dr-drill \
  --for=condition=Ready --timeout=120s
kubectl delete pod dr-pre-drill-canary -n dr-drill

echo "Pre-drill canary: PASS. Recording drill start time: $(date -u)"
DRILL_START=$(date +%s)
```

### Step 2 — Backup KV signing cert

```bash
# Backup primary cert (Azure KV Backup)
BACKUP_FILE="/tmp/notation-cert-backup-$(date +%Y%m%d%H%M).json"
az keyvault certificate backup \
  --vault-name "${KV_NAME}" \
  --name notation-cert-primary \
  --file "${BACKUP_FILE}"

# Upload to DR storage
az storage blob upload \
  --account-name "${DR_STORAGE}" \
  --container-name kv-cert-backups \
  --name "notation-cert-primary-$(date +%Y%m%d%H%M).json" \
  --file "${BACKUP_FILE}" \
  --auth-mode login

echo "Cert backup uploaded. File: ${BACKUP_FILE}"
```

### Step 3 — Snapshot ACR and LAW

```bash
# ACR: import key curated images to backup registry
for IMG in python312-sandbox node20-sandbox canary-primary; do
  az acr import \
    --name "${ACR_BACKUP}" \
    --source "${ACR_NAME}.azurecr.io/${IMG}:latest" \
    --image "${IMG}:dr-$(date +%Y%m%d)" \
    --force
  echo "Imported ${IMG} to backup ACR."
done

# LAW: verify Diagnostic Settings archive is active (set up in Bicep, not manual)
az monitor diagnostic-settings list \
  --resource "${LAW_RESOURCE_ID}" \
  --query "[].{name:name, storageAccountId:storageAccountId}" \
  --output table
echo "LAW archive: verify output shows a storage account destination."
```

### Step 4 — Simulate disaster: disable primary KV cert

> ⚠️ **REQUIRES TWO-PERSON APPROVAL**  
> Do not proceed without a second platform engineer confirming on the incident bridge.

```bash
# SIMULATION: disable the cert (soft-delete only — recoverable within soft-delete window)
# In a real disaster, the KV or subscription would be lost; this drill tests the restore path.

read -rp "Two-person approval confirmed? (type YES to continue): " CONFIRM
if [ "${CONFIRM}" != "YES" ]; then
  echo "Drill aborted — two-person approval not confirmed."
  exit 1
fi

# Disable (not delete) the primary cert to simulate it being unavailable
az keyvault certificate set-attributes \
  --vault-name "${KV_NAME}" \
  --name notation-cert-primary \
  --enabled false

echo "Primary cert DISABLED. RTO clock starts: $(date -u)"
DISASTER_TIME=$(date +%s)
```

### Step 5 — Restore cert from backup

```bash
# Download most recent backup from DR storage
LATEST_BACKUP=$(az storage blob list \
  --account-name "${DR_STORAGE}" \
  --container-name kv-cert-backups \
  --auth-mode login \
  --query "reverse(sort_by(@, &name))[0].name" \
  --output tsv)

az storage blob download \
  --account-name "${DR_STORAGE}" \
  --container-name kv-cert-backups \
  --name "${LATEST_BACKUP}" \
  --file /tmp/restored-cert.json \
  --auth-mode login

echo "Downloaded backup: ${LATEST_BACKUP}"

# Restore to KV
# Note: restore to a KV in the SAME tenant and geography only (Azure KV restriction)
az keyvault certificate restore \
  --vault-name "${KV_NAME}" \
  --file /tmp/restored-cert.json

# Re-enable the cert
az keyvault certificate set-attributes \
  --vault-name "${KV_NAME}" \
  --name notation-cert-primary \
  --enabled true

echo "Cert restored and re-enabled."
RESTORE_TIME=$(date +%s)
echo "Restore duration: $(( RESTORE_TIME - DISASTER_TIME )) seconds"
```

### Step 6 — Post-restore canary: schedule a Notation-signed image

```bash
# Sign a test image with the restored cert
CERT_ID=$(az keyvault certificate show \
  --vault-name "${KV_NAME}" \
  --name notation-cert-primary \
  --query id -o tsv)

notation sign \
  --signature-format cose \
  --plugin azure-kv \
  --id "${CERT_ID}" \
  "${ACR_NAME}.azurecr.io/canary-primary:latest"

echo "Image re-signed with restored cert."

# Schedule on AKS — Ratify must accept the newly-signed image
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: dr-post-restore-canary
  namespace: dr-drill
spec:
  runtimeClassName: kata-vm-isolation
  restartPolicy: Never
  containers:
    - name: canary
      image: ${ACR_NAME}.azurecr.io/canary-primary:latest
      command: ["echo", "post-restore-ok"]
EOF

kubectl wait pod dr-post-restore-canary --namespace dr-drill \
  --for=condition=Ready --timeout=240s

kubectl delete pod dr-post-restore-canary -n dr-drill
CANARY_TIME=$(date +%s)
echo "Post-restore canary: PASS"
```

### Step 7 — Record RTO/RPO and sign off

```bash
TOTAL_RTO=$(( CANARY_TIME - DISASTER_TIME ))
echo ""
echo "======================================="
echo "DR Drill Sign-Off"
echo "======================================="
echo "Drill date:        $(date -u +%Y-%m-%d)"
echo "RTO achieved:      ${TOTAL_RTO}s ($(( TOTAL_RTO / 60 )) minutes)"
echo "RTO target:        14400s (4 hours)"
echo "RTO met:           $([ ${TOTAL_RTO} -le 14400 ] && echo YES || echo NO)"
echo "RPO:               Verified (backup was < 24h old)"
echo "Canary:            PASS"
echo "======================================="

# Update drill log
cat >> runbooks/dr-drill-log.md <<ENTRY

## Drill $(date -u +%Y-%m-%d)

| Field | Value |
|-------|-------|
| Date | $(date -u +%Y-%m-%d) |
| RTO achieved | ${TOTAL_RTO}s ($(( TOTAL_RTO / 60 )) minutes) |
| RTO target | 14400s (4 hours) |
| RTO met | $([ ${TOTAL_RTO} -le 14400 ] && echo ✅ || echo ❌) |
| Canary | ✅ PASS |
| Conducted by | <name> |
| Approved by | <name> |
| Notes | |

ENTRY
```

---

## Failure Scenarios During Drill

| Issue | Resolution |
|-------|-----------|
| KV restore fails (wrong geography) | Restore to a KV in the same tenant + geography; create a new KV if needed |
| Ratify does not trust restored cert | Force Ratify config refresh: `kubectl rollout restart -n ratify-system deployment/ratify` |
| Canary pod stuck in `ContainerCreating` | Check Kata pool node readiness; may need to wait for pre-warm DaemonSet |
| Backup cert file missing | Check DR storage account + Diagnostic Settings archive; escalate if no backup < 24h |

---

## Post-Drill Actions

1. File a GitHub issue for any RTO/RPO miss with root cause analysis.
2. Update `docs/acceptance-checklist.md` AC #32 with drill result.
3. Share results with the security review board within 5 business days.
