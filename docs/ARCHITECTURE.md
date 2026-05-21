# Architecture

This document describes what is actually deployed in `rg-opensandbox-dev` and
`rg-opensandbox-demo` today, how the pieces fit together, and the failure modes the team has hit
and recovered from. Each claim should be reproducible from a `kubectl` or `az` command; lines
labelled "current state, may drift" depend on live cluster output rather than code.

## TL;DR

DarkForge is a sandbox runtime running on AKS, with Kata Containers providing per-pod
VM-grade isolation. Everything else — registry, firewall, audit pipeline, identity, Foundry
integration — is the Azure landing zone wrapped around it. The trust boundary is **Kata**, not
the Linux namespace. The runtime itself is vendored under
[`third_party/opensandbox/`](../third_party/opensandbox/); see
[`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md) for attribution.

## Component map

```
                                  internet
                                     |
                                     |  (laptop)
                                     v
        +-----------------------+        +-----------------------+
        | Microsoft Foundry     |        | developer SDK         |
        | aihubeastus26267492086|        | (sandbox python)      |
        | Kimi-K2.5 / Kimi-K2.6 |        |  kubectl port-forward |
        +-----------+-----------+        +-----------+-----------+
                    |                                |
                    | AAD bearer                     | api-key
                    | (cognitiveservices.azure.com)  | localhost:18080
                    v                                v
                    +------+    +-------------------------------------+
                           |    |  AKS cluster aks-opensandbox-dev    |
                           |    |  v1.34.7, Azure CNI Overlay, Cilium |
                           |    |                                     |
                           |    |  +-------------------------------+  |
                           |    |  | sandbox server (FastAPI)      |  |
                           |    +->|   POST /v1/sandboxes ...      |  |
                           |       |   WebSocket exec              |  |
                           |       +---------------+---------------+  |
                           |                       |                  |
                           |                       v                  |
                           |       +-------------------------------+  |
                           |       | sandbox controller-manager    |  |
                           |       | (Go, watches BatchSandbox CRD)|  |
                           |       +---------------+---------------+  |
                           |                       |                  |
                           |                       v  scheduled to    |
                           |                          Kata node pool  |
                           |       +-------------------------------+  |
                           |       | Sandbox pod                   |  |
                           |       |  runtimeClassName:            |  |
                           |       |   kata-vm-isolation           |  |
                           |       |  init:  execd v1.0.8          |  |
                           |       |  side:  execd daemon          |  |
                           |       |  user:  python:3.12-slim      |  |
                           |       |  inner kernel: 6.6.130.1-azl3 |  |
                           |       +---------------+---------------+  |
                           |                       |                  |
                           |   ACA env             | egress           |
                           |   acaenv-opensandbox  |                  |
                           |   (FINISH-7 wiring)   v                  |
                           |    snet-aca       UDR rt-snet-kata-dev   |
                           |                       |                  |
                           +-----------------------+------------------+
                                                   |
                                                   v
                                  +-------------------------------+
                                  | Azure Firewall Premium        |
                                  | afw-opensandbox-dev           |
                                  | policy afwp-opensandbox-dev   |
                                  | rcg-aks-bootstrap (p=100)     |
                                  | rcg-sandbox-egress (p=200)    |
                                  | deny-all     (p=300)          |
                                  +---------------+---------------+
                                                  |
                                                  v
                              pypi / npm / proxy.golang.org / azlinux pkg repos


   +---------------------------+     +--------------------------------+
   | ACR Premium               |     | Key Vault kv-opensandbox-dev   |
   | acropensandboxdemo7075    |     |   private endpoint pe-kv-...   |
   |   public access: DISABLED |     |   stores: API keys, certs      |
   |   PE pe-acr-... 10.10.12.6|     +--------------------------------+
   |   DNS privatelink.azurecr.|
   |   7 images: controller,   |     +--------------------------------+
   |   server, execd, ingress, |     | Workload Identity              |
   |   code-interp-base,       |     | id-kimi-demo-dev               |
   |   code-interp, sandbox    |     |   federated to demo/sa         |
   +-------------+-------------+     +--------------------------------+
                 |
                 v  kubelet pull via PE
              AKS nodes


   Audit lane
   ----------
                                                                  +-----+
   sandbox pod  -->  Fluent Bit DS  -->  Event Hubs  -->  Stream  | blob|
   (execd logs)      (on Kata pool)      evhns-opensand   Analytics|stas |
                                         box-dev          asa-...  |...  |
                                         hub sandbox-     Running   audit|
                                         audit-fast       MI: EH    -fast|
                                         (4 partitions,   Receiver +     |
                                         LocalAuth        Blob Data      |
                                         Disabled)        Contributor    |
                                                                  +-----+
```

The eleven components from the operator's mental model:

1. AKS cluster `aks-opensandbox-dev`
2. Kata sandbox node pool + `kata-vm-isolation` runtime class
3. Sandbox control plane (controller, server, `execd`) — vendored runtime
4. ACR Premium `acropensandboxdemo7075` + private endpoint
5. Azure Firewall Premium `afw-opensandbox-dev` + UDR
6. Cilium dataplane + ACNS (Hubble UI for network observability, L7 FQDN policies). Note: Hubble UI is Cilium's network flow viewer — it is *not* a sandbox fleet console. A user-facing console for sandbox lifecycle (list/create/delete) is design-only, tracking upstream OSEP-0006.
7. Event Hubs `evhns-opensandbox-dev` + Stream Analytics + blob output
8. ACA environment `acaenv-opensandbox-dev` (FINISH-7 in progress)
9. Foundry `aihubeastus26267492086` with Kimi-K2.5 / K2.6
10. Workload Identity `id-kimi-demo-dev` federated to the `demo` SA
11. Key Vault `kv-opensandbox-dev` + private endpoint

## VNet layout

`vnet-opensandbox-dev` is `10.10.0.0/16` in `eastus2`.

| Subnet | CIDR | Purpose | NSG | UDR |
|---|---|---|---|---|
| snet-system | 10.10.1.0/24 | AKS system nodepool (controller, server, ingress) | nsg-snet-system-dev | none (direct SLB egress) |
| snet-kata | 10.10.2.0/23 | Kata sandbox pool | nsg-snet-kata-dev | rt-snet-kata-dev → Firewall |
| snet-aca | 10.10.4.0/23 | ACA infrastructure (control-plane apps) | nsg-snet-aca-dev | none |
| AzureFirewallSubnet | 10.10.10.0/26 | Azure Firewall data plane | none (required by Azure) | none |
| snet-appgw | 10.10.11.0/26 | App Gateway (Layer 7 ingress) | nsg-snet-appgw-dev | none |
| snet-pe | 10.10.12.0/24 | Private endpoints (ACR, KV, EH, ...) | nsg-snet-pe-dev | none |

The system pool sits in `snet-system` and uses direct outbound type for control-plane traffic;
only the Kata pool is forced through the firewall.

## Identity flow

Two distinct flows. The Foundry / Kimi flow is end-to-end Workload Identity Federation; the
laptop SDK flow currently uses a plain API key stored in `kv-opensandbox-dev`.

```
  Workload Identity flow (in-cluster Kimi demo, kimi-demo-success.log)
  --------------------------------------------------------------------

  +-----------------+        +-------------------------+
  | kimi-demo pod   |        | OIDC issuer of cluster  |
  | namespace: demo |---(1)->| (oidc-discovery URL on  |
  | SA: kimi-demo   |        |  AKS, public)           |
  | SA token        |        +-----------+-------------+
  | (projected,     |                    |
  | audience=AzureAD|                    | (2) verify signature, claim issuer
  +--------+--------+                    v
           |                  +-------------------------+
           |                  | Microsoft Entra ID      |
           +------------(3)-->| federated credential on |
                   exchange   | id-kimi-demo-dev:       |
                              |   subject=system:       |
                              |     serviceaccount:     |
                              |     demo:kimi-demo      |
                              +-----------+-------------+
                                          |
                                          | (4) issue AAD access token
                                          v   audience=cognitiveservices.azure.com
                              +-------------------------+
                              | Foundry Kimi endpoint   |
                              | aihubeastus26267492086  |
                              +-------------------------+


  Laptop SDK flow (sdk_e2e.py)
  ----------------------------

  developer az login --> az get-access-token --resource=cognitiveservices  (for Kimi only)
                                       |
                                       v
            opensandbox SDK -- api-key (kv-backed) --> opensandbox-server
                                       |
                                       v
                              same cluster path
```

Federated credential definition: an Entra federated identity credential tied to the
cluster's OIDC issuer, with subject `system:serviceaccount:demo:kimi-demo`. The matching
Azure role assignment grants the UAMI `Cognitive Services User` on the Foundry resource.

## Egress flow for a sandbox pod

```
  user container                       inner Kata VM                       Azure node
  +-----------------+                  +-----------------+                 +--------------+
  | python:3.12     |   syscall to     | guest kernel    |   virtio-net    | kata-shim    |
  | pip install ... |--socket------>   | 6.6.130.1-azl3  |---------------->| veth on host |
  +-----------------+                  +-----------------+                 +------+-------+
                                                                                  |
                                                                                  v
                                                                          +---------------+
                                                                          | Cilium ENI    |
                                                                          | overlay       |
                                                                          | Hubble can    |
                                                                          | observe L7    |
                                                                          | FQDN policy   |
                                                                          +-------+-------+
                                                                                  |
                                                                                  | UDR override
                                                                                  v rt-snet-kata-dev
                                                                          +---------------+
                                                                          | Azure FW Prem |
                                                                          | DNS proxy ON  |
                                                                          | TLS inspect   |
                                                                          | FQDN tag      |
                                                                          | AzureKuber-   |
                                                                          | netesService  |
                                                                          | + pypi/npm/   |
                                                                          | golang allow- |
                                                                          | list          |
                                                                          +-------+-------+
                                                                                  |
                                                                                  v
                                                                            internet
```

Two layers of enforcement: Cilium L7 FQDN policy applies first on egress from the pod;
Azure Firewall is the network-layer backstop. The deny-all rule collection at priority 300 in
`afwp-opensandbox-dev` ensures anything not explicitly allowed is dropped.

## Image supply chain

```
   developer / CI  --(az acr build)-->  ACR Premium
                                          acropensandboxdemo7075
                                          (public access DISABLED)
                                                |
                                                | private endpoint
                                                v
                                          snet-pe (10.10.12.6)
                                                |
                                                | privatelink.azurecr.io
                                                | linked to vnet
                                                v
                                          kubelet on AKS node
                                                |
                                                | OCI pull
                                                v
                                          containerd
                                                |
                                                v
                                          Kata runtime  --launches-->  inner VM with
                                                                       OCI image rootfs
```

Tagged images currently in the registry (7 total): `controller v0.1.14`, `server v0.1.14`,
`execd v1.0.8`, `ingress`, `code-interpreter-base v1.0.0`, `code-interpreter v1.0.0`,
`sandbox/base/python`. Pull failures on a brand-new node manifest as `ImagePullBackOff` with a
TLS handshake error — re-check the private DNS zone linkage first.

## Failure modes & recovery

| Failure | What it looks like | How we recover |
|---|---|---|
| `bootstrap.sh` has CRLF line endings | Sandbox pod restarts in a tight loop; `kubectl logs <pod> -c execd-init` shows `bash: bootstrap.sh: cannot execute: required file not found` even though the file is present | `.gitattributes` enforces LF on `*.sh`; re-build the execd image (`infra/helm/opensandbox/...`). The teaching story is below. |
| Firewall in `Failed` provisioning state | `az network firewall show ... --query provisioningState` returns `Failed`; sandbox pods can't pull from pypi | Patch the policy with `az network firewall policy update --idle-timeout 60`, then `az network firewall show ... --query 'sku'` to confirm Premium SKU intact. |
| ACR private endpoint DNS drift | `kubectl describe pod` shows `failed to resolve reference ... acropensandboxdemo7075.azurecr.io: no such host` | Verify `privatelink.azurecr.io` is linked to `vnet-opensandbox-dev`; flush kubelet DNS cache by restarting the node pool one node at a time. |
| Stream Analytics job stops on storage 401 | ASA shows `Stopped` with `Authorization` errors in diagnostic logs | Re-assert `Storage Blob Data Contributor` on the ASA system-assigned MI; restart the job. |
| Cluster autoscaler stuck on Kata pool | Pods Pending with `0/3 nodes are available: 3 node(s) didn't match node selector` | `az aks nodepool update -g rg-opensandbox-dev --cluster-name aks-opensandbox-dev -n kata --update-cluster-autoscaler --min-count 1 --max-count 4` (current state, may drift) |
| Event Hubs LocalAuth attempted | App logs show `401 Unauthorized` against EH endpoint when an older Fluent Bit shipped a SAS key | Confirm `evhns-opensandbox-dev` has `disableLocalAuth: true`; switch Fluent Bit auth plugin to AAD. |

## The CRLF bootstrap.sh story

When we first stood up the sandbox image on a Windows developer workstation, every pod entered
`CrashLoopBackOff` with the same misleading error: `cannot execute: required file not found`,
pointing at a `bootstrap.sh` that was clearly present in the layer. The file looked fine in `cat`
output. It only became visible with `od -c` — the shebang line ended in `\r\n` instead of `\n`,
and the kernel was looking for an interpreter named `/bin/bash\r`, which of course did not exist.

The fix is two-fold and both halves matter:

1. Repository-level: a `.gitattributes` entry forces `*.sh` to LF on checkout. Without this, the
   next developer cloning on Windows would re-introduce the bug invisibly.
2. Image-level: the `Dockerfile` for `execd` runs `sed -i 's/\r$//' bootstrap.sh` as a belt-and-
   braces guard against any other tool inserting CRs.

This is also why `third_party/opensandbox/sandboxes/base/bootstrap.sh` is one of the two
patches we carry against the vendored tree — that codebase assumes Linux-only contributors,
which is no longer true.

## References

- [Plan: ralplan](../.omc/plans/ralplan-implement-opensandbox-in-azure.md)
- [Acceptance checklist](acceptance-checklist.md)
- [AKS Pod Sandboxing (Kata)](https://learn.microsoft.com/azure/aks/use-pod-sandboxing)
- [AKS Workload Identity overview](https://learn.microsoft.com/azure/aks/workload-identity-overview)
- [Azure Firewall Premium](https://learn.microsoft.com/azure/firewall/premium-features)
- [Vendored sandbox runtime attribution](../THIRD_PARTY_LICENSES.md)
