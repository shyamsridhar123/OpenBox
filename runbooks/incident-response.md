# Incident Response Runbook

> **Version:** 1.0  
> **Plan tasks:** Phase 6, Tasks 6.1–6.2  
> **Owner:** Platform Security Team  
> **Review cycle:** Quarterly (aligned with DR drill)

---

## 1. Revoke a Compromised User

**Trigger:** User OID reported as compromised; suspected credential theft or insider threat.

**Time target:** Complete within 30 minutes of incident declaration.

### Step 1 — Delete UAMI federated credential

```bash
# Identify the user's UAMI name (format: id-user-<short-oid>)
USER_OID="<user-entra-oid>"
SHORT_OID="${USER_OID:0:8}"
UAMI_NAME="id-user-${SHORT_OID}"
RG="rg-opensandbox-prod"

# List federated credentials on the UAMI
az identity federated-credential list \
  --identity-name "${UAMI_NAME}" \
  --resource-group "${RG}" \
  --query "[].name" -o tsv

# Delete all federated credentials on this UAMI
for FC in $(az identity federated-credential list \
    --identity-name "${UAMI_NAME}" \
    --resource-group "${RG}" \
    --query "[].name" -o tsv); do
  az identity federated-credential delete \
    --identity-name "${UAMI_NAME}" \
    --resource-group "${RG}" \
    --name "${FC}" --yes
  echo "Deleted federated credential: ${FC}"
done
```

### Step 2 — Force token expiry via Conditional Access

```bash
# Revoke all active refresh tokens for the user via Microsoft Graph
# Requires Global Admin or User Admin role.
az rest --method POST \
  --uri "https://graph.microsoft.com/v1.0/users/${USER_OID}/revokeSignInSessions"
```

Alternatively, in the Entra admin portal:
1. **Users → [User] → Sign-in activity → Revoke sessions**

### Step 3 — Terminate user's active sandbox pods

```bash
# List all sandbox pods for this user (labeled with user OID)
kubectl get pods \
  --all-namespaces \
  -l "sandbox.opensandbox.io/user-oid=${USER_OID}" \
  --no-headers -o custom-columns="NS:.metadata.namespace,NAME:.metadata.name"

# Delete all found pods (grace period 0 for immediate termination)
kubectl delete pods \
  --all-namespaces \
  -l "sandbox.opensandbox.io/user-oid=${USER_OID}" \
  --grace-period=0 --force
```

### Step 4 — Remove user from Sandbox Users Entra group

```bash
# Find group object ID
GROUP_ID=$(az ad group show \
  --group "sg-opensandbox-users" \
  --query id -o tsv)

# Remove user from group
az ad group member remove \
  --group "${GROUP_ID}" \
  --member-id "${USER_OID}"
```

### Step 5 — Audit trail verification

```bash
# Query AKS audit log for all actions by this user in the last 24h
az monitor log-analytics query \
  -w "${LAW_WORKSPACE_ID}" \
  --analytics-query "
    AKSAudit
    | where User.Username contains '${USER_OID}'
       or User.Username contains '$(az ad user show --id ${USER_OID} --query userPrincipalName -o tsv)'
    | where TimeGenerated > ago(24h)
    | project TimeGenerated, Verb, ObjectRef, User_Username = User.Username
    | order by TimeGenerated desc
  " \
  --output table
```

---

## 2. Quarantine a Compromised Pod

**Trigger:** Kata pod exhibiting suspicious behaviour (unexpected outbound connections, anomalous exec patterns, container escape attempt detected by CI or KQL alert).

### Step 1 — Cordon the node hosting the pod

```bash
# Find the node
POD_NAME="<sandbox-pod-name>"
POD_NS="<sandbox-namespace>"
NODE=$(kubectl get pod "${POD_NAME}" -n "${POD_NS}" \
  -o jsonpath='{.spec.nodeName}')
echo "Pod is on node: ${NODE}"

# Cordon node (prevent new pods scheduling)
kubectl cordon "${NODE}"
```

### Step 2 — NetworkPolicy isolation (network quarantine)

```bash
# Apply an emergency isolation policy that denies ALL ingress/egress for this pod
kubectl apply -f - <<EOF
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: quarantine-${POD_NAME}
  namespace: ${POD_NS}
spec:
  endpointSelector:
    matchLabels:
      statefulset.kubernetes.io/pod-name: ${POD_NAME}
  ingressDeny:
    - fromEntities:
        - world
        - cluster
  egressDeny:
    - toEntities:
        - world
        - cluster
EOF
echo "Network isolation applied to ${POD_NAME}"
```

### Step 3 — Snapshot pod filesystem for forensics

```bash
# Capture pod manifest + logs before deletion
kubectl get pod "${POD_NAME}" -n "${POD_NS}" -o yaml > "/tmp/forensics-${POD_NAME}-manifest.yaml"
kubectl logs "${POD_NAME}" -n "${POD_NS}" --all-containers=true > "/tmp/forensics-${POD_NAME}-logs.txt"
kubectl describe pod "${POD_NAME}" -n "${POD_NS}" > "/tmp/forensics-${POD_NAME}-describe.txt"

# Capture Kata VM disk snapshot via Azure (requires VM resource ID)
# Note: Kata pods run on dedicated VMs — find the VM via node name
AKS_NODE_VM=$(az vm list -g "${AKS_NODE_RG}" \
  --query "[?contains(name, '${NODE}')].id" -o tsv | head -1)
if [ -n "${AKS_NODE_VM}" ]; then
  SNAPSHOT_NAME="forensic-${POD_NAME}-$(date +%Y%m%d%H%M)"
  az snapshot create \
    --name "${SNAPSHOT_NAME}" \
    --resource-group "${AKS_NODE_RG}" \
    --source "${AKS_NODE_VM}" \
    --sku Standard_ZRS
  echo "Snapshot created: ${SNAPSHOT_NAME}"
fi

# Upload forensics files to secure storage
az storage blob upload-batch \
  --account-name "${FORENSICS_STORAGE_ACCOUNT}" \
  --destination forensics-$(date +%Y%m%d) \
  --source /tmp \
  --pattern "forensics-${POD_NAME}*" \
  --auth-mode login
```

### Step 4 — Terminate the pod

```bash
kubectl delete pod "${POD_NAME}" -n "${POD_NS}" \
  --grace-period=0 --force
echo "Pod ${POD_NAME} terminated."
```

### Step 5 — Post-quarantine: drain and un-cordon (or decommission node)

```bash
# If node is known-clean: drain remaining pods and un-cordon
kubectl drain "${NODE}" --ignore-daemonsets --delete-emptydir-data
kubectl uncordon "${NODE}"

# If node is suspected compromised: delete from AKS node pool
az aks nodepool scale \
  --resource-group "${AKS_RG}" \
  --cluster-name "${AKS_NAME}" \
  --name katapool \
  --node-count <current_count - 1>
```

---

## 3. Rotate Notation Signing Certificate (Dual-Cert Dance)

**Trigger:** Scheduled rotation (before 21-day remaining lifetime), or emergency rotation on cert compromise.

**Design:** Ratify TrustPolicy always holds TWO trusted certs (primary + secondary). Rotation = promote secondary → primary, mint new secondary. Overlap is minimum 14 days (IaC-enforced).

### Step 1 — Verify current dual-cert state

```bash
KV_NAME="kv-opensandbox-prod"

# Check primary cert expiry
az keyvault certificate show \
  --vault-name "${KV_NAME}" \
  --name notation-cert-primary \
  --query "{name:name, expires:attributes.expires, enabled:attributes.enabled}"

# Check secondary cert expiry
az keyvault certificate show \
  --vault-name "${KV_NAME}" \
  --name notation-cert-secondary \
  --query "{name:name, expires:attributes.expires, enabled:attributes.enabled}"
```

### Step 2 — Mint new secondary certificate (14-day overlap from current primary expiry)

```bash
# Generate new cert in KV (policy defined in kv.bicep — auto-renewed)
az keyvault certificate create \
  --vault-name "${KV_NAME}" \
  --name notation-cert-secondary-new \
  --policy "$(az keyvault certificate get-default-policy)"

# Wait for cert to be issued
az keyvault certificate wait \
  --vault-name "${KV_NAME}" \
  --name notation-cert-secondary-new \
  --created
```

### Step 3 — Update Ratify TrustPolicy to add new cert (now three certs — briefly)

```bash
# Export new cert public key for Ratify TrustPolicy
az keyvault certificate download \
  --vault-name "${KV_NAME}" \
  --name notation-cert-secondary-new \
  --file /tmp/notation-cert-secondary-new.pem

# Update Ratify TrustPolicy ConfigMap / CRD to include the new cert
# (This is parameterized in Bicep — re-deploy with new cert thumbprint)
helm upgrade opensandbox infra/helm/opensandbox \
  --reuse-values \
  --set "ratify.trustPolicy.secondaryCertThumbprint=$(
    openssl x509 -fingerprint -sha256 -noout \
      -in /tmp/notation-cert-secondary-new.pem \
    | cut -d= -f2 | tr -d ':'
  )"
```

### Step 4 — Run canary CI test (both old + new cert sign and schedule)

```bash
# Trigger the notation-rotation-canary workflow manually
gh workflow run main.yml \
  --ref main \
  --field run_canary=true
```

Wait for the canary job to pass. If it fails, do NOT proceed to Step 5.

### Step 5 — Promote: rename secondary-new → primary (after canary passes 7 days)

```bash
# Promote new cert to primary via Bicep re-deploy
# Update Bicep parameter: notationCertPrimaryName = "notation-cert-secondary-new"
# Update Bicep parameter: notationCertSecondaryName = "notation-cert-secondary"
az deployment sub create \
  -f infra/bicep/main.bicep \
  -p infra/bicep/main.prod.parameters.json \
  -p "notationCertPrimaryName=notation-cert-secondary-new" \
  --location eastus2
```

### Step 6 — Retire old primary cert (only after 7-day canary pass)

```bash
# Disable (not delete) old primary cert — retain for audit
az keyvault certificate set-attributes \
  --vault-name "${KV_NAME}" \
  --name notation-cert-primary \
  --enabled false

echo "Old primary cert disabled. Rotation complete."
```

### Step 7 — Verify

```bash
# Confirm Ratify still admits a signed pod
kubectl apply -f infra/helm/opensandbox/templates/test-signed-pod.yaml
kubectl wait pod ratify-verify-test \
  --for=condition=Ready --timeout=60s
kubectl delete pod ratify-verify-test
```
