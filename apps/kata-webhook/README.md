# kata-webhook

MutatingAdmissionWebhook that forces every new sandbox Pod in the `opensandbox` namespace onto the `kata-vm-isolation` RuntimeClass.

## How it works

`POST /mutate` receives an `AdmissionReview`, inspects the Pod spec, and injects three fields via JSONPatch:

| Field | Value | Condition |
|---|---|---|
| `spec.runtimeClassName` | `kata-vm-isolation` | only if currently unset |
| `spec.tolerations` | `runtime=kata:NoSchedule` | only if not already present |
| `spec.nodeSelector["sandbox.io/runtime"]` | `kata` | only if not already present |

**Skip rules (no mutation):**
- Pod already has a `runtimeClassName` set (idempotent)
- Pod has a `pool` label (pool pre-warmed pods already have the right fields)
- Pod name starts with `kata-` (defensive check)

**Failure policy:** `Ignore` — if the webhook is down or returns an error, `allowed: true` is still returned so sandbox creation is never blocked.

## Build

```bash
# From repo root
docker build -t kata-webhook:dev apps/kata-webhook/
```

## Push to ACR

```bash
az acr login --name acropensandboxdemo7075
docker tag kata-webhook:dev acropensandboxdemo7075.azurecr.io/kata-webhook:dev
docker push acropensandboxdemo7075.azurecr.io/kata-webhook:dev
```

## Generate TLS certs

```bash
cd infra/kata-webhook
chmod +x generate-certs.sh
./generate-certs.sh ./certs
```

Outputs `certs/ca.crt`, `certs/tls.crt`, `certs/tls.key`.

## Deploy to cluster

```bash
cd infra/kata-webhook

# 1. Base64-encode the cert material
CA_BUNDLE=$(base64 < certs/ca.crt | tr -d '\n')
TLS_CRT=$(base64 < certs/tls.crt | tr -d '\n')
TLS_KEY=$(base64 < certs/tls.key | tr -d '\n')

# 2. Substitute into manifest and apply
sed \
  -e "s|__CA_BUNDLE__|$CA_BUNDLE|g" \
  -e "s|__TLS_CRT__|$TLS_CRT|g" \
  -e "s|__TLS_KEY__|$TLS_KEY|g" \
  manifest.yaml | kubectl apply -f -

# 3. Wait for rollout
kubectl -n opensandbox-system rollout status deploy/kata-webhook --timeout=120s
```

## Verify

```bash
# Check webhook registered
kubectl get mutatingwebhookconfigurations kata-runtime-mutator

# Tail webhook logs
kubectl -n opensandbox-system logs -l app=kata-webhook -f

# Create a test sandbox and confirm runtimeClassName
cd /path/to/openbox
.venv-swarm/Scripts/python -c "
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
api_key = open('examples/.opensandbox-api-key').read().strip()
sb = Sandbox(connection=ConnectionConfig(domain='localhost:18080', api_key=api_key, use_server_proxy=True))
sb.create()
import subprocess, sys
out = subprocess.check_output(['kubectl','get','pod',f'{sb.id}-0','-n','opensandbox','-o','jsonpath={.spec.runtimeClassName}|{.spec.nodeName}']).decode()
print('runtimeClassName | nodeName:', out)
assert out.startswith('kata-vm-isolation'), f'FAIL: {out}'
assert 'aks-kata-' in out, f'FAIL node: {out}'
print('PASS')
sb.delete()
"
```

## Local dry-run (no Docker)

```bash
cd apps/kata-webhook
pip install fastapi uvicorn
# Generate self-signed cert for local testing
openssl req -x509 -nodes -newkey rsa:2048 -keyout /tmp/tls.key -out /tmp/tls.crt -days 1 -subj '/CN=localhost'
uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-certfile /tmp/tls.crt --ssl-keyfile /tmp/tls.key
```

## Architecture

```
SDK → OpenSandbox controller → creates Pod (no runtimeClassName)
                                      ↓
                        kube-apiserver calls /mutate
                                      ↓
                         kata-webhook injects:
                           runtimeClassName: kata-vm-isolation
                           toleration: runtime=kata:NoSchedule
                           nodeSelector: sandbox.io/runtime=kata
                                      ↓
                         Pod scheduled on aks-kata-* node
                         running under Kata Containers
```
