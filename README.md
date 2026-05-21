# OpenSandbox on Azure

A working deployment of the upstream [alibaba/OpenSandbox](https://github.com/alibaba/OpenSandbox)
control plane on Azure Kubernetes Service, with Kata Containers providing per-pod VM-grade
isolation for untrusted code. The stack is wired end-to-end: a developer SDK on a laptop, or a
Kimi K2.5/K2.6 model deployment in Microsoft Foundry, can call `Sandbox.create`, get back a
running Kata-isolated pod, execute code, and read results — through the same OpenSandbox
controller, server, and `execd` daemon that ships upstream, with only minimal patches
(goproxy.cn → proxy.golang.org and a CRLF fix in `bootstrap.sh`).

This repo is **not** a fork of upstream OpenSandbox. The upstream tree is vendored under
[`third_party/opensandbox/`](third_party/opensandbox/) and consumed unchanged except for the two
patches above. Everything in [`infra/`](infra/) and the docs you are reading describe the Azure
landing zone around it.

## What's actually running right now

Resource groups `rg-opensandbox-dev` (cluster + control plane) and `rg-opensandbox-demo` (ACR),
both in `eastus2`. Verified end-to-end by the runs under [`evidence/runs/finish/`](evidence/runs/finish/).

| Layer | Resource | Notes |
|---|---|---|
| Cluster | `aks-opensandbox-dev` | Kubernetes 1.34.7, Azure CNI Overlay + Cilium dataplane, ACNS + Hubble UI |
| System pool | 3 nodes (runc) | OpenSandbox controller, server, ingress, system addons |
| Kata pool | Kata Containers, `kata-vm-isolation` runtime class | Cloud Hypervisor (MSHV), inner-VM kernel `6.6.130.1-3.azl3` (Azure Linux 3) |
| Container registry | `acropensandboxdemo7075` (ACR Premium) | Public access disabled, private endpoint `pe-acr-opensandbox-dev` (10.10.12.6), private DNS zone `privatelink.azurecr.io` |
| Egress firewall | `afw-opensandbox-dev` (Azure Firewall Premium) | Private IP 10.10.10.4, policy `afwp-opensandbox-dev`, two rule collection groups (`rcg-aks-bootstrap` p100, `rcg-sandbox-egress` p200), deny-all at p300 |
| Sandbox UDR | `rt-snet-kata-dev` | Forces 0.0.0.0/0 from `snet-kata` to the firewall |
| Audit pipeline | Event Hubs `evhns-opensandbox-dev` (LocalAuthDisabled) → Stream Analytics `asa-opensandbox-audit-dev` → blob `stasadevse3bwihj3in4s/audit-fast` | Event hub `sandbox-audit-fast`, 4 partitions; ASA uses system-assigned MI with EH Data Receiver + Storage Blob Data Contributor |
| Control plane (ACA) | `acaenv-opensandbox-dev` in snet-aca | 3 container apps; wiring in progress under FINISH-7 |
| Foundry | `aihubeastus26267492086` | Kimi-K2.5 + Kimi-K2.6 deployments |
| Workload identity | `id-kimi-demo-dev` | Federated to the `demo` namespace's service account |
| Key Vault | `kv-opensandbox-dev` | Private endpoint `pe-kv-opensandbox-dev` |

## Architecture at a glance

Two paths are demonstrated in `evidence/runs/finish/`. Both bottom out in the same controller +
Kata sandbox pod.

```
Path A — Laptop SDK (sdk_e2e.py)
================================

   developer laptop                       AKS cluster (aks-opensandbox-dev)
  +-----------------+                +------------------------------------------+
  |                 |                |                                          |
  |  Sandbox.create | --HTTP-->      |  opensandbox-server (FastAPI)            |
  |  Python SDK     |  kubectl       |     |                                    |
  |                 |  port-forward  |     v  creates BatchSandbox CR           |
  |  api-key auth   |  :18080        |  opensandbox-controller-manager (Go)     |
  +-----------------+                |     |                                    |
                                     |     v  schedules pod onto Kata pool      |
                                     |  +----------------------------------+   |
                                     |  | Sandbox pod                      |   |
                                     |  |   runtimeClassName:              |   |
                                     |  |   kata-vm-isolation              |   |
                                     |  |                                  |   |
                                     |  |   init: execd (v1.0.8, CRLF-fixed|   |
                                     |  |   sidecar: execd daemon         )|   |
                                     |  |   user container: python:3.12   )|   |
                                     |  +----------------------------------+   |
                                     +------------------------------------------+
                                                       |
                                                       v   egress via UDR
                                                  Azure Firewall (allowlist)
                                                       |
                                                       v
                                                 pypi / npm / proxy.golang.org


Path B — Kimi agentic app (kimi_via_osb.py)
============================================

  Kimi-K2.5 / K2.6  ----(AAD bearer)----> Microsoft Foundry (aihubeastus...)
       ^                                       |
       | code in <code>...</code>              | generated Python
       |                                       v
  +----+-------------------------------------------------------------+
  | kimi_via_osb.py — extracts code, hands to OpenSandbox SDK        |
  +------------------------------------------------------------------+
                                       |
                                       v   (same path as A from here)
                              opensandbox-server -> controller -> Kata pod
                                       |
                                       v
                              python3 /tmp/kimi_code.py inside the sandbox
                                       |
                                       v
                              SUM=88   (PASS, see kimi-via-osb.log)
```

A deeper diagram with all eleven components, the VNet/subnet table, identity flow, and the egress
data path lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart — reproduce the two demos

These steps assume an operator with cluster-admin (or equivalent kubelogin) access to
`aks-opensandbox-dev` and a Python 3.11+ environment.

```bash
# 0. Auth + cluster context
az login
az aks get-credentials -g rg-opensandbox-dev -n aks-opensandbox-dev --overwrite-existing

# 1. Install the upstream SDK from the vendored tree
pip install -e third_party/opensandbox/sdks/python

# 2. Port-forward the OpenSandbox server to localhost:18080
kubectl -n opensandbox-system port-forward svc/opensandbox-server 18080:8080 &

# 3. Drop the server API key next to the demo script
#    (read once from the running deployment — see runbooks for rotation)
kubectl -n opensandbox-system get secret opensandbox-server -o jsonpath='{.data.OPENSANDBOX_SERVER_API_KEY}' \
  | base64 -d > evidence/runs/finish/.opensandbox-api-key

# 4a. Run the laptop SDK demo
python evidence/runs/finish/sdk_e2e.py
#     Expected last line: RUN-4 SUCCESS

# 4b. Run the Kimi agentic demo
#     (requires az login with access to aihubeastus26267492086)
python evidence/runs/finish/kimi_via_osb.py
#     Expected last line: verdict     = PASS
```

The recorded runs for both scripts (raw stdout, sandbox IDs, the Kimi prompt and response) are
checked in under [`evidence/runs/finish/`](evidence/runs/finish/) — `sdk_e2e.log`,
`kimi-via-osb.log`, and `kimi-demo-success.log` for the in-cluster Workload Identity variant.

## Repository layout

| Path | Purpose |
|---|---|
| [`third_party/opensandbox/`](third_party/opensandbox/) | Upstream Alibaba OpenSandbox, vendored. Do not edit; sync via the upstream-sync workflow. |
| [`infra/bicep/`](infra/bicep/) | Subscription-scope Bicep for the Azure landing zone (cluster, ACR, firewall, audit). Owned by infra workstream. |
| [`infra/helm/opensandbox/`](infra/helm/opensandbox/) | Helm chart deploying the upstream images (controller, server, execd) with Azure-specific values. |
| [`apps/`](apps/) | Forthcoming control-plane services on ACA (FastAPI, portal). FINISH-7 work in progress. |
| [`sdks/`](sdks/) | Azure-flavored SDK wrappers and examples. |
| [`docs/`](docs/) | This documentation set. |
| [`evidence/runs/finish/`](evidence/runs/finish/) | E2E proof artefacts (logs, manifests, runbooks per FINISH slice). |
| [`runbooks/`](runbooks/) | Ops runbooks: incident response, onboarding, CVE response, DR drill. |

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — full architecture deep-dive, VNet table,
  identity and egress flows, image supply chain, failure modes, the CRLF bootstrap story.
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — runbook index, cluster health checklist, image
  onboarding, API key rotation, execd rebuild and roll-out.
- [docs/index.md](docs/index.md) — entry point linking to everything above.
- [docs/acceptance-checklist.md](docs/acceptance-checklist.md) — the 34 acceptance criteria for v1.
- [ROADMAP.md](ROADMAP.md) — what is done, what is deferred, what is next.

## Patches against upstream

There are exactly two delta points against `third_party/opensandbox/`:

1. `goproxy.cn` → `proxy.golang.org` in the build for Azure-region pulls.
2. CRLF protection in `bootstrap.sh` (the script must be LF-only or `execd` init crashes the
   sandbox before the daemon attaches). Enforced by `.gitattributes`. See
   [docs/ARCHITECTURE.md#the-crlf-bootstrap-story](docs/ARCHITECTURE.md#the-crlf-bootstrap-story).

## License

This wrapper is provided under the repo `LICENSE`. Upstream OpenSandbox is licensed under its own
terms — see [`third_party/opensandbox/LICENSE`](third_party/opensandbox/LICENSE).
