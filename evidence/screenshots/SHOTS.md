# Screenshot capture guide — OpenSandbox on Azure

> User-driven capture. For each row: run the command (or open the URL) on
> the target machine, take the screenshot, save it as the filename shown
> in `evidence/screenshots/`, and I will embed it with a caption into the
> final evidence report.
>
> `kubectl`, `az`, and a web browser logged into the right Azure tenant
> are the only prereqs. Where I expect a portal page, the equivalent
> CLI/text dump command is shown so we have a text fallback too.

## How to use this file

1. Walk row-by-row top to bottom.
2. Run the **Capture command** in your terminal OR open the **URL** in your browser.
3. Wait until the screen shows the **Must show** content.
4. Take a screenshot (Win+Shift+S → "Save as…") to `evidence/screenshots/<filename>`.
5. Mark the row ✓ in the "Captured" column once saved.
6. After all rows are ✓, ping me and I'll assemble the annotated report.

---

## Cluster + sandbox runtime (4 shots)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 1 | AKS cluster overview in portal | https://portal.azure.com → resource group `rg-opensandbox-demo` → click `aks-opensandbox-demo` | Cluster Running, K8s version 1.34.7, 2 nodepools (`nodepool1`, `kata`) | `01-aks-overview.png` | ☐ |
| 2 | Kata nodepool detail | Portal: AKS → Node pools → click `kata` | runtimeClass = KataVmIsolation, `sandbox.io/runtime=kata` taint visible | `02-kata-nodepool.png` | ☐ |
| 3 | `kubectl get nodes -o wide` showing both pools | `kubectl get nodes -o wide` | 3 nodepool1 nodes + Kata node(s) all `Ready`, Container-Runtime `containerd://2.0.0`, OS `Microsoft Azure Linux 3.0` | `03-kubectl-nodes.png` | ☐ |
| 4 | `kubectl get runtimeclass` | `kubectl get runtimeclass` | rows `kata-vm-isolation (kata)` and `runc (runc)` | `04-runtimeclass.png` | ☐ |

## OpenSandbox control plane (4 shots)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 5 | Helm release | `helm.exe list -n opensandbox-system` (or `kubectl get deploy -n opensandbox-system`) | `opensandbox-controller-manager` + `opensandbox-server` deployments, both `1/1 Ready` | `05-helm-release.png` | ☐ |
| 6 | Server config points at our ACR | `kubectl get cm -n opensandbox-system opensandbox-server-config -o yaml \| Select-String execd_image` | `execd_image = "acropensandboxdemo7075.azurecr.io/opensandbox/execd:v1.0.8"` | `06-server-configmap.png` | ☐ |
| 7 | API key secret exists | `kubectl get secret -n opensandbox-system opensandbox-api-key -o yaml` | A `data.api-key` field (base64 — content not important) | `07-api-key-secret.png` | ☐ |
| 8 | Server `/health` returns 200 | After `kubectl port-forward -n opensandbox-system svc/opensandbox-server 18080:80`, in browser: http://localhost:18080/health | Browser shows `{"status":"ok"}` (or equivalent) | `08-server-health.png` | ☐ |

## ACR images (3 shots)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 9 | ACR repositories list | https://portal.azure.com → ACR `acropensandboxdemo7075` → Repositories | `opensandbox/controller`, `opensandbox/server`, `opensandbox/execd`, `opensandbox/ingress`, `opensandbox/code-interpreter-base` | `09-acr-repos.png` | ☐ |
| 10 | execd v1.0.8 manifest | Portal: ACR → Repositories → `opensandbox/execd` → click `v1.0.8` | Digest `sha256:63dec85f1513bb5...`, platform linux/amd64 | `10-acr-execd-v108.png` | ☐ |
| 11 | ACR public network access disabled (post FINISH-5) | Portal: ACR → Networking → Public access | Toggle = `Disabled`, private endpoint visible in Private access tab | `11-acr-private.png` | ☐ |

## SDK end-to-end run (3 shots)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 12 | RUN-4 SDK terminal session | `/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py` | Output ending with `RUN-4 SUCCESS` and stdout containing `HELLO_FROM_REAL_OPENSANDBOX` + Azure Linux uname + `4` | `12-sdk-e2e-terminal.png` | ☐ |
| 13 | Live sandbox pod from SDK | `kubectl get pods -n opensandbox -o wide` | At least one `<uuid>-0` pod Running on a node with name starting `aks-kata-` | `13-sandbox-pod.png` | ☐ |
| 14 | Inside-sandbox kernel | `kubectl exec -n opensandbox <uuid>-0 -- uname -a` | `Linux <uuid>-0 6.6.130.1-3.azl3 ... x86_64 GNU/Linux` | `14-sandbox-uname.png` | ☐ |

## Kimi agentic app (3 shots)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 15 | Foundry deployments page | https://portal.azure.com → Cognitive Services `aihubeastus26267492086` → Model deployments | rows `Kimi-K2.5` and `Kimi-K2.6` both `Succeeded` | `15-foundry-deployments.png` | ☐ |
| 16 | Kimi-via-OSB terminal session | `export AAD_TOKEN=$(az account get-access-token --resource https://cognitiveservices.azure.com --query accessToken -o tsv); /tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/kimi_via_osb.py` | Output showing model `Kimi-K2.5`, extracted code, sandbox.id, stdout containing fib sequence + `SUM=88`, `verdict = PASS` | `16-kimi-via-osb.png` | ☐ |
| 17 | (Optional) in-cluster Kimi demo | `kubectl apply -f evidence/runs/finish/kimi-demo.yaml; kubectl logs -n demo kimi-demo -f` | "E2E SUCCESS" line at the end | `17-kimi-incluster.png` | ☐ |

## Network + security (4 shots — after FINISH-4 / FINISH-5)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 18 | Azure Firewall overview | Portal: `azfw-opensandbox-demo` (or whatever name we land on) | Status `Succeeded`, SKU `Premium` | `18-firewall-overview.png` | ☐ |
| 19 | FW rule collection group with AKS bootstrap rules | Portal: Firewall → Rules (classic) → Application rules → `rc-aks-bootstrap` | Rules for `mcr.microsoft.com`, `*.azurelinux.microsoft.com`, FQDN tag `AzureKubernetesService` | `19-firewall-rules.png` | ☐ |
| 20 | UDR attached to snet-kata | Portal: route table `rt-snet-kata-demo` → Subnets | `snet-kata` listed; route forces `0.0.0.0/0` to `10.10.10.4` (FW private IP) | `20-udr-snet-kata.png` | ☐ |
| 21 | ACR private endpoint in snet-pe | Portal: Private endpoints → `pe-acr-opensandbox-demo` | Resource = ACR `acropensandboxdemo7075`; subnet `snet-pe`; connection `Approved` | `21-acr-pe.png` | ☐ |

## Observability + audit (3 shots — after FINISH-6 / FINISH-8)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 22 | ACNS / Cilium enabled | Portal: AKS → Networking → Advanced Container Networking Services | `Enabled` for both Observability + Security; data plane = Cilium | `22-acns-enabled.png` | ☐ |
| 23 | Hubble UI flow logs | Portal: AKS → Monitoring → ACNS or `az aks` shortcut to Hubble UI | At least one row of sandbox-namespace traffic | `23-hubble-flows.png` | ☐ |
| 24 | LAW custom table receiving audit events | Portal: Log Analytics → `law-opensandbox-demo` → Logs; run `OpenSandbox_CL \| take 10` | At least one row from Stream Analytics with command audit data | `24-law-audit.png` | ☐ |

## Control plane on ACA (2 shots — after FINISH-7)

| # | Title | Capture command / URL | Must show | Filename | Captured |
|---|---|---|---|---|---|
| 25 | ACA environment + app | Portal: Container Apps Environment `cae-opensandbox-demo` → Apps | `opensandbox-server` running on ACA, replica count ≥ 1 | `25-aca-server.png` | ☐ |
| 26 | App Gateway routing to ACA | Portal: App Gateway `agw-opensandbox-demo` → Backend health | Backend = ACA app, Healthy | `26-appgw-backend.png` | ☐ |

---

## Total: 26 shots (3 optional)

Once you have these saved under `evidence/screenshots/`, ping me with
"screenshots done" and I'll generate `evidence/runs/finish/FINAL-REPORT.md`
that embeds each shot with a caption, links it to the matching AC row,
and produces the final evidence dossier.

If a row's screen doesn't yet exist (e.g., row 22 before FINISH-6
completes), leave it ☐ — I'll fill those in as the underlying tasks
finish, and the table doubles as a checklist for the remaining work.
