# Architecture

## TL;DR

OpenSandbox on Azure = **Hybrid (ACA control plane + AKS+Kata sandbox runtime)**.

- The control plane is stateless HTTP and benefits from ACA's managed surface (Easy Auth, KEDA, private ingress).
- The sandbox runtime requires Kubernetes CRDs (OpenSandbox upstream design), so it must live on AKS.
- Sandbox pods use Kata Containers (`runtimeClassName: kata-vm-isolation`) for VM-grade isolation of untrusted code.
- Trust boundary is **Kata**, not namespaces. Namespaces are logical separation per user.
- Identity is **end-to-end** — every action ties to an Entra user OID via OBO → JWT → Workload Identity.

## Why this shape

See the [consensus ADR](../.omc/plans/ralplan-implement-opensandbox-in-azure.md#adr-final) for full reasoning.

The short version:

- **AKS for the runtime, not ACA.** OpenSandbox uses K8s CRDs and a controller, which ACA cannot host. ACA Dynamic Sessions was considered and rejected for the same reason.
- **ACA for the control plane, not AKS.** ACA gives Easy Auth (free Entra SSO for the portal), KEDA HTTP scaling, and managed TLS. AKS gives none of those for HTTP workloads without significant boilerplate (cert-manager, ingress-nginx, oauth2-proxy).
- **Hybrid means two IaC stacks and two observability lanes.** We accept this cost in exchange for not building a portal auth proxy ourselves.

## Components

### Control plane (ACA)
- `apps/control-plane/` — FastAPI service: `POST /sessions`, `POST /sessions/{id}/run`, `DELETE /sessions/{id}`, `POST /users/{oid}/provision`. Validates Entra OBO bearer tokens against JWKS; performs OBO exchange to obtain a downstream token targeted at the AAD-integrated AKS server app; calls AKS API as the user. `minReplicas=1` (scale-to-zero is incompatible with the 5-s cold-start SLA per the consensus plan).
- `apps/portal-api/` — Read-only FastAPI service backing the portal: `GET /me/sessions`, `GET /me/audit`, `GET /me/quota`. Uses control-plane API + Log Analytics direct queries via Managed Identity.
- `apps/portal-frontend/` — React on ACA with Easy Auth (Entra SSO). Read-only views: Sessions, Audit Log, Quota.

### Sandbox runtime (AKS)
- **Controller + CRDs:** the OpenSandbox upstream controller, deployed via Helm in namespace `opensandbox-system`, scheduled to the `system` node pool.
- **execd DaemonSet:** the OpenSandbox execution daemon, deployed as a DaemonSet on the `kata` node pool. Runs as `runc` (not Kata) since it's infrastructure, not user code.
- **Image pre-warm DaemonSet:** pulls all curated base images on node startup. Nodes are tainted `pre-warm=pending:NoSchedule` until pulls complete. Sandbox pods cannot land on a not-yet-warm node.
- **Sandbox pods:** one Kata pod per session, in the user's namespace `ns-<user-oid>`. Mounted projected SA token federated to the user's UAMI.

### Identity
- **User → control plane:** Entra ID bearer token (OBO). Audience = OpenSandbox API app reg.
- **Control plane → AKS API:** confidential-client OBO exchange. Audience = AKS server app ID (from the cluster's AAD profile). NOT `management.azure.com`. AKS audit log records the user's OID as the requester.
- **Sandbox pod → Azure resources:** Entra Workload Identity. One UAMI per user namespace (respects the 20-federated-credentials-per-UAMI ceiling).
- **Shared warm-pool tier (opt-in only):** uses a `tier=shared` UAMI with read-only KV scope, 5-minute token lifetime, IP-bound CA policy, rate limits. Audit log records `identity_tier=shared_warm_pool`.

### Network
- **AKS:** private cluster (API server on private endpoint), Cilium + ACNS for L7 NetworkPolicy.
- **External access:** Internet → Application Gateway (WAF) → ACA private ingress.
- **Sandbox egress:** UDR from `snet-kata` forces `0.0.0.0/0` through Azure Firewall. Firewall SKU is **conditional** on Phase 0 spike result (see `Task 1.5` of plan):
  - If Cilium L7 works on Kata pods → Standard SKU + Cilium L7 as primary enforcement.
  - If Cilium L7 fails on Kata pods → Premium SKU with SNI-based filtering.
- **Service endpoints:** ACR, Key Vault, Log Analytics, Event Hubs — all on private endpoints; no public access.

### Image supply chain
- **ACR Premium**, geo-replication-capable (1 region active in v1).
- **Notation (Notary v2)** signing with Key Vault-backed cert. ALWAYS two certs (primary + secondary) with 14-day minimum overlap, IaC-enforced.
- **Ratify + Gatekeeper** at AKS admission. Pods whose image is not signed by a trusted cert are rejected with a clear error.
- **ACR Tasks** build pipeline. Defender for Containers scans on push; high/critical CVEs block deployment.

### Audit & observability
- **Fast-path audit (≤60s SLA):** `execd` → Fluent Bit → **Event Hubs → Stream Analytics → Log Analytics**. Container Insights (2-10 min ingestion) is too slow for AC #12.
- **Slow-path logs:** Container Insights for routine container logs.
- **Traces:** `traceparent` header propagated SDK → ACA → AKS API → kubelet → execd; a single trace_id spans all four.
- **Defender for Containers:** enabled, with the known gap that Kata pods aren't assessed. KQL alert compensates.

## Failure modes & mitigations

| Failure | Mitigation |
|---|---|
| Kata cold start > 5 s | Image pre-warm DaemonSet + node-readiness taint. Default tier targets 5 s p95; opt-in shared-pool tier targets 500 ms. |
| Notation cert rotation downtime | Dual-cert TrustPolicy, 14-day overlap, canary CI test. |
| Workload Identity propagation race | `POST /users/<oid>/provision` doesn't return 200 until a throwaway pod successfully acquires a token. 90-s bound. |
| Upstream CVE we miss | Minimal patch surface (≤100 LOC), weekly upstream-sync CI, 72-h critical-CVE SLA. |
| KV signing cert loss | Backed up to Azure Backup for KV + offline copy. Quarterly DR drill. |
| Container escape from Kata | 3 deterministic PoCs run in CI on every PR. Failure pages on-call. |

## What this scaffold does NOT do

This codebase is **the scaffold**. It does NOT include:

- A fully-tested, production-deployed AKS cluster — Phase 0 spikes must run first.
- An actually-built control plane Docker image — Dockerfile is present; image build requires a real ACR.
- A populated curated image catalog — `infra/images/<lang>/Dockerfile` are stubs for the platform team to extend.
- Real OpenSandbox upstream code — the Helm chart references the upstream Docker image; we have not forked yet.

The next session(s) — by a human team, or by extending this scaffold via `/oh-my-claudecode:ralph` against `.omc/plans/ralplan-implement-opensandbox-in-azure.md` — should:

1. Run the Phase 0 spikes against a real Azure dev subscription.
2. Fill out Bicep module bodies based on spike results.
3. Implement the FastAPI handlers (skeleton present; business logic stubbed).
4. Build the OpenSandbox fork with minimal Entra patches.
5. Implement the JS and Go SDKs against the Python reference.

## References

- Plan: `.omc/plans/ralplan-implement-opensandbox-in-azure.md`
- Spec: `.omc/specs/deep-dive-implement-opensandbox-in-azure.md`
- [AKS Pod Sandboxing (Kata)](https://learn.microsoft.com/azure/aks/use-pod-sandboxing)
- [AKS Workload Identity](https://learn.microsoft.com/azure/aks/workload-identity-overview)
- [Entra OBO flow](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Notation / Ratify on AKS](https://learn.microsoft.com/azure/security/container-secure-supply-chain/articles/validating-image-signatures-using-ratify-aks)
- [AKS Baseline Architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks)
- [Alibaba OpenSandbox](https://github.com/alibaba/OpenSandbox)
