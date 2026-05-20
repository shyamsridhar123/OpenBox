# Onboarding Runbook

> **Version:** 1.0  
> **Plan tasks:** Phase 6, Task 6.3  
> **Owner:** Platform Team

---

## 1. Onboard a New User

**Prerequisites:**
- User has an Entra account in the tenant.
- User's manager has submitted an access request.
- Platform team has approved access.

### Step 1 — Add user to the Sandbox Users Entra group

```bash
USER_UPN="user@contoso.com"
GROUP_NAME="sg-opensandbox-users"

# Get user's object ID
USER_OID=$(az ad user show --id "${USER_UPN}" --query id -o tsv)
echo "User OID: ${USER_OID}"

# Get group object ID
GROUP_ID=$(az ad group show --group "${GROUP_NAME}" --query id -o tsv)
echo "Group ID: ${GROUP_ID}"

# Add user to group
az ad group member add --group "${GROUP_ID}" --member-id "${USER_OID}"
echo "Added ${USER_UPN} to ${GROUP_NAME}"
```

### Step 2 — Assign Sandbox User RBAC role

```bash
RG="rg-opensandbox-prod"
SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}"

az role assignment create \
  --assignee "${USER_OID}" \
  --role "Sandbox User" \
  --scope "${SCOPE}"
echo "Sandbox User role assigned."
```

### Step 3 — Provision user resources via control plane API

```bash
# Use an admin token (or the provisioning service identity)
ADMIN_TOKEN=$(az account get-access-token \
  --resource "api://${SANDBOX_API_APP_ID}" \
  --query accessToken -o tsv)

CONTROL_PLANE_URL="https://<app-gateway-fqdn>/api"

curl -sf -X POST \
  "${CONTROL_PLANE_URL}/users/${USER_OID}/provision" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"oid\": \"${USER_OID}\", \"upn\": \"${USER_UPN}\"}"

echo "User provisioning complete."
```

This call:
- Creates UAMI `id-user-<short-oid>`
- Creates Key Vault `kv-user-<short-oid>` (AC #11)
- Assigns `Key Vault Secrets User` to the UAMI at that vault's scope only
- Creates the federated credential linking the UAMI to the user's K8s service account
- Runs the synchronous propagation probe (fails with 503 + Retry-After: 90 if FC not propagated)

### Step 4 — Verify user can authenticate

```bash
# Ask the user to run the SDK smoke test:
python3 -c "
from opensandbox import SandboxClient
client = SandboxClient()
session = client.sessions.create(image='python312-sandbox')
result = session.run('print(\"hello world\")')
print(result.output)
session.close()
"
```

### Step 5 — Send welcome instructions to user

Provide the user with:
- SDK installation: `pip install opensandbox`
- Portal URL: `https://<portal-fqdn>`
- Documentation: `docs/user-guide.md`

---

## 2. Onboard a New Curated Image

**Prerequisites:**
- Image has been security-reviewed.
- Dockerfile has been approved in a PR.
- ACR Task definition has been reviewed.

### Step 1 — Write the Dockerfile

Place it at `images/<image-name>/Dockerfile`. Requirements:
- Base from a Microsoft or distroless base (no `latest` tags).
- No package manager caches left in the image (`rm -rf /var/cache/apt`).
- Non-root user: `USER 1000`.
- No secrets, credentials, or API keys.

Example:
```dockerfile
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 sandbox
USER 1000
WORKDIR /sandbox
```

### Step 2 — Add ACR Task definition

Create `images/<image-name>/acr-task.yaml`:

```yaml
version: v1.1.0
steps:
  - build: >
      -t {{.Run.Registry}}/{{.Values.imageName}}:{{.Run.ID}}
      -t {{.Run.Registry}}/{{.Values.imageName}}:latest
      -f images/{{.Values.imageName}}/Dockerfile .
  - push:
      - "{{.Run.Registry}}/{{.Values.imageName}}:{{.Run.ID}}"
      - "{{.Run.Registry}}/{{.Values.imageName}}:latest"
```

Register the ACR Task:

```bash
IMAGE_NAME="<image-name>"
ACR_NAME="acropensandboxprod"

az acr task create \
  --registry "${ACR_NAME}" \
  --name "build-${IMAGE_NAME}" \
  --file "images/${IMAGE_NAME}/acr-task.yaml" \
  --context "https://github.com/<org>/openbox.git#main" \
  --set "imageName=${IMAGE_NAME}" \
  --assign-identity
```

### Step 3 — Build and Notation-sign the image

```bash
# Trigger the ACR Task
az acr task run \
  --registry "${ACR_NAME}" \
  --name "build-${IMAGE_NAME}"

# Get the digest of the built image
DIGEST=$(az acr repository show \
  --name "${ACR_NAME}" \
  --image "${IMAGE_NAME}:latest" \
  --query digest -o tsv)

IMAGE_REF="${ACR_NAME}.azurecr.io/${IMAGE_NAME}@${DIGEST}"
echo "Image ref: ${IMAGE_REF}"

# Sign with Notation (primary cert)
CERT_ID=$(az keyvault certificate show \
  --vault-name "kv-opensandbox-prod" \
  --name notation-cert-primary \
  --query id -o tsv)

notation sign \
  --signature-format cose \
  --plugin azure-kv \
  --id "${CERT_ID}" \
  "${IMAGE_REF}"

echo "Image signed: ${IMAGE_REF}"
```

### Step 4 — Verify Ratify admits the image

```bash
# Schedule a test pod with the new image
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: curated-image-verify-${IMAGE_NAME}
  namespace: image-verify
spec:
  runtimeClassName: kata-vm-isolation
  restartPolicy: Never
  containers:
    - name: verify
      image: ${IMAGE_REF}
      command: ["echo", "image-ok"]
EOF

kubectl wait pod "curated-image-verify-${IMAGE_NAME}" \
  --namespace image-verify \
  --for=condition=Ready --timeout=120s

kubectl delete pod "curated-image-verify-${IMAGE_NAME}" -n image-verify
echo "Ratify admission: PASS"
```

### Step 5 — Add to pre-warm list

Add the image to the pre-warm DaemonSet configuration in:
`infra/helm/opensandbox/values.yaml` under `prewarm.images`:

```yaml
prewarm:
  images:
    - python312-sandbox:latest
    - node20-sandbox:latest
    - golang122-sandbox:latest
    - <new-image-name>:latest   # ← add here
```

Deploy the Helm update:

```bash
helm upgrade opensandbox infra/helm/opensandbox \
  --reuse-values \
  --set "prewarm.images[3]=<new-image-name>:latest"
```

### Step 6 — Update curated image registry documentation

Add entry to `docs/curated-images.md`:

| Image | Base | Purpose | Added | Signed |
|-------|------|---------|-------|--------|
| `<image-name>` | `<base>` | `<purpose>` | `YYYY-MM-DD` | ✅ |
