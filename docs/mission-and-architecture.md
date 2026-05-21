# DarkForge — Mission, Rationale & Architecture

> **Source of truth:**
> - Spec: [`.omc/specs/deep-dive-implement-opensandbox-in-azure.md`](../.omc/specs/deep-dive-implement-opensandbox-in-azure.md) (ambiguity 14%, 34 ACs)
> - Plan: [`.omc/plans/ralplan-implement-opensandbox-in-azure.md`](../.omc/plans/ralplan-implement-opensandbox-in-azure.md) (v0.3 FINAL, Planner→Architect→Critic consensus)
> - Trace: [`.omc/specs/deep-dive-trace-implement-opensandbox-in-azure.md`](../.omc/specs/deep-dive-trace-implement-opensandbox-in-azure.md)
> - Goal: [`.omc/state/goal.md`](../.omc/state/goal.md)
>
> This document summarizes those artifacts. Where this doc and the spec/plan disagree, **the spec/plan wins.**

---

## 1. Mission (verbatim from the user)

> *"implement this https://github.com/alibaba/OpenSandbox in my azure environment.*
> *but I also want it to be somewhat in parity https://azure.microsoft.com/en-us/products/dev-box/#features"*
>
> *"finish all the phases and run an agentic application in this openbox environment*
> *and test it end to end and submit screenshots of every single capability.*
> *use kimi k2.5 deployment in my tenant."*
>
> *"no not the minimal, i want the full fucking build."*

### Restated as a goal (from the spec, §Goal)

Stand up a general-purpose AI-agent sandbox platform (FastAPI control plane, an execution daemon, Kubernetes runtime with CRDs, gVisor/Kata/Firecracker isolation, multi-language SDKs) on **Microsoft Azure**, with **conceptual parity to Azure Dev Box's governance and image-management model** (DevCenter→Project→Pool hierarchy, image-definition YAML, RBAC, network connections, autostop). The sandbox runtime is consumed from a vendored third-party project under [`third_party/opensandbox/`](../third_party/opensandbox/) — see [`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md).

### v1 scope (4-6 weeks)

A working agent-callable sandbox service in **East US 2** supporting **~500 concurrent active sandboxes**, with:

- per-user **Entra-authenticated REST API**
- **Python / JS / Go SDKs**
- **signed curated base images**
- **read-only observability portal** *(planned — design tracks [OSEP-0006](../third_party/opensandbox/oseps/0006-developer-console.md); not yet implemented. See §3.1 note.)*
- **Notation/Ratify signature enforcement**
- **Azure Policy in Deny mode**

### Explicitly deferred to vNext (from spec §Non-Goals)

Windows sandboxes · GUI/RDP/desktop · multi-cloud / hybrid · multi-region (architecture must support it; v1 single-region) · hibernation / CRIU · external-customer multi-tenancy · service-principal-only auth · full write portal · Java / C# SDKs · marketplace images · Intune / Entra hybrid join / Conditional Access · TTL idle reaper · Confidential Containers (SEV-SNP).

---

## 2. Rationale

### 2.1 The five principles (verbatim from RALPLAN §Principles)

1. **Trust boundary lives in Kata, not in namespaces.** All sandbox pods run `runtimeClassName: kata-vm-isolation`. Namespaces are logical isolation only. Kata's trust boundary is verified by a deterministic container-escape test (Phase 6 task 6.7).
2. **Identity is end-to-end and per-user.** Every action traces to an Entra user OID via OBO → JWT → Workload Identity. The opt-in `low_latency=true` shared-pool tier is the **only** exception, and it is explicitly audited, role-gated, and rate-limited.
3. **Deny by default at every layer.** Azure Policy Deny mode, Cilium NetworkPolicy default-deny, Ratify-required signatures, Azure Firewall allowlist, AKS Kubernetes-RBAC default-deny bound to Entra groups.
4. **Validate upstream and integration assumptions before building.** Phase 0 tasks 0.1-0.4 cover the runtime CRD scope + Cilium-ACNS-on-Kata L7 behavior. Phase 0 gates Phase 1.
5. **MVP is verifiable end-to-end, not feature-complete.** Every AC is testable by `kubectl`, `curl`, load test, log query, portal screenshot, or PoC exploit. Measurement procedures are defined for every numeric SLA.

### 2.2 The three decision drivers (RALPLAN §Decision Drivers)

1. **Hard isolation is non-negotiable** → Kata mandatory (so an agent's bad `rm -rf` or a runaway `pip install` doesn't escape the VM); container-escape test in CI; signed images; private cluster.
2. **Audit trail must be unbroken and timely** → Entra OBO end-to-end; Diagnostic Settings (not Container Insights) for fast-path audit; warm pools cannot break user attribution.
3. **Build velocity matters: 4-6 weeks for v1** → Single region, single AKS cluster, Bicep, curated images.

### 2.3 Options considered and why this one won (RALPLAN §Viable Options)

| Option | Verdict | Why |
|---|---|---|
| **A — Everything on AKS (single-plane)** | Live fallback | Reinstated; triggered if Phase 0 task 0.4 reveals blocking issues. |
| **B — Hybrid ACA + AKS+Kata** | **CHOSEN** | Best velocity; ACA gives free Easy Auth + KEDA + managed TLS for the control plane; AKS gives K8s CRDs + Kata for the runtime. |
| **C — ACA Dynamic Sessions replacing K8s runtime** | Rejected | No per-namespace UAMI projection, no Cilium NetworkPolicy at pod level, no CRD support — would require forking and replacing the sandbox runtime layer. |
| **D — Kata Confidential Containers (SEV-SNP)** | Rejected for v1 | Same Defender-coverage gap as plain Kata; adds SEV-SNP attestation complexity for which v1 has no compliance driver. Architecture preserves the upgrade path (one-line `runtimeClassName` swap). |
| **E — AKS Automatic mode** | Rejected for v1 | Doesn't expose node-pool taint/toleration / runtimeClass at the level Kata Pod Sandboxing requires. Revisit at v1.5 when Automatic-mode Kata is GA. |

### 2.4 What "parity with Dev Box" means (spec §Dev-Box → sandbox mapping)

Conceptual parity on **governance and image management**, not on **product surface**. Concretely:

| Dev Box concept | Our equivalent |
|---|---|
| DevCenter | The whole platform — one Azure resource group + Bicep deployment |
| Project | User namespace (`ns-<user-oid>`) + matching ACR repo scope + Entra group |
| Dev box pool | Kata node pool + curated image set bound by Bicep param |
| Image definition | YAML file → ACR Tasks → signed ACR image (v1.5) |
| Custom image | Direct ACR push by platform admin (v1) |
| Marketplace image | Curated base images in ACR `sandbox/base/*` |
| Network connection | VNet subnet + NSG + Cilium NetworkPolicy bundle |
| RBAC roles | Custom Azure roles: `SandboxCenter Admin`, `SandboxProject Admin`, `Sandbox User` |
| Autostop schedule | **v1.5** — explicit `DELETE` only for v1 |
| Project policies | OPA/Gatekeeper constraints + Azure Policy assignments |

Dropped because they're Windows-bound and we are Linux-only: Intune enrollment, Entra hybrid join, RDP/desktop, Windows hibernation, VS marketplace images, Hybrid Benefit licensing, Conditional Access compliant-device, Endpoint Privilege Management.

---

## 3. Architecture

### 3.1 Component → Azure resource map (verbatim from spec §Technical Context)

| Component | Azure resource | Notes |
|---|---|---|
| FastAPI control plane | ACA revision | Auto-scaling via KEDA HTTP scaler; `minReplicas: 1` (cold-start SLA). Implemented (`apps/control-plane/`). |
| Portal frontend (React/Blazor) | ACA revision | ACA Easy Auth (Entra SSO). **Scaffold only** — ACA revision `ca-portalfe-opensandbox-dev` provisioned and running `mcr.microsoft.com/azuredocs/containerapps-helloworld:latest` placeholder. No source in `apps/portal-frontend/src/`. Design tracks [OSEP-0006](../third_party/opensandbox/oseps/0006-developer-console.md). |
| Portal API | ACA revision | Reads Log Analytics + AKS API. **Scaffold only** — ACA revision `ca-portalapi-opensandbox-dev` provisioned; no source in `apps/portal-api/app/`. |
| OpenSandbox K8s controller | AKS Deployment in `opensandbox-system` ns | RBAC scoped (NOT cluster-admin) — vendored runtime, see [`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md) |
| Execution daemon | AKS DaemonSet on Kata nodes | Runs as `runc`, not Kata |
| Sandbox pods | AKS Pod, `runtimeClassName: kata-vm-isolation` | Per-user namespace `ns-<user-oid>` |
| Sandbox images | ACR Premium repo `sandbox/*` | Notation-signed |
| Image build | ACR Tasks | Triggered by base-image updates + image-definition YAML |
| Notation cert + secrets | Key Vault | Private endpoint |
| Logs / metrics | Log Analytics + App Insights | One workspace |
| Egress policy | Azure Firewall + Cilium FQDN | Two layers; SKU conditional on Phase 0 spike |
| Identity | Entra app reg + per-user UAMI | Workload Identity federation |

### 3.2 Constraints in force (spec §Constraints)

- **Compute substrate:** Hybrid — ACA hosts the FastAPI control plane + read-only portal frontend + portal API; AKS hosts the sandbox K8s controller + CRDs + execution-daemon DaemonSet + sandbox pods.
- **Sandbox isolation:** AKS Pod Sandboxing with **Kata Containers** (GA) on **Azure Linux 3.0 + Gen2 VMs**, `runtimeClassName: kata-vm-isolation`. Mandatory for all sandbox pods.
- **Tenancy model:** Agent-as-tenant — single AKS cluster, namespace-per-user. Kata = primary trust boundary; namespaces = soft logical separation.
- **Region:** East US 2 (single region for v1; architecture parameterizable).
- **Network:** Private AKS API; NAT Gateway per subnet; sandbox egress via UDR through **Azure Firewall** (SKU conditional — see §3.5); **Azure CNI Powered by Cilium + ACNS** for L3-L7 NetworkPolicy + FQDN filtering; ACR and Key Vault private endpoints only.
- **Images:** **ACR Premium** (zone-redundant, geo-replication-ready, 1 active region for v1); curated catalog (`base/python:3.12`, `base/node:20`, `base/go:1.22`, optional `base/datascience`); **Notation (Notary v2)** signing using a **Key Vault-backed cert**; signatures as OCI referrers in ACR; admission enforced by **Ratify + Gatekeeper**.
- **Governance:** **Azure Policy add-on for AKS**, **K8s pod security restricted built-in initiative in Deny mode**, custom denies (privileged, hostPath, unsigned, non-ACR registries, `runAsNonRoot=false`). **Defender for Containers** on registry + cluster. Known gap: Defender does NOT assess Kata-runtime pods (accepted and documented; compensated by KQL alert).
- **Observability:** Container Insights → one Log Analytics workspace. App Insights instruments ACA via OpenTelemetry. Cilium flow logs + AKS audit logs + Azure Firewall logs → same LAW.
- **Scale (v1):** ~500 concurrent sandboxes; Kata pool 3-10 × `Standard_D8s_v5` Gen2 Azure Linux; separate `runc` system pool; ACA min 1 replica, scale to 10 via KEDA HTTP.
- **IaC:** **Bicep** modules per service; one root `main.bicep` + parameter file per env. Helm for AKS workloads.

### 3.3 Identity end-to-end (spec §Identity / Auth, RALPLAN Task 3.3)

```
SDK
 │  Entra Bearer; scope = api://openbox/.default
 ▼
ACA control plane (FastAPI)
 │  1. Validate JWT signature against Entra JWKS
 │  2. Validate iss + aud  (aud MUST be our API app, NOT management.azure.com)
 │  3. OBO exchange (confidential client)
 │     scope = <AKS_SERVER_APP_ID>/.default
 │     where <AKS_SERVER_APP_ID> = AKS aadProfile.serverAppID
 │     NOT https://management.azure.com/.default  (RALPLAN Critic S-C8)
 ▼
AKS API (AAD-integrated, private cluster)
 │  Audit log records user UPN/OID as requester (NOT the control plane SP)
 ▼
Controller → spawns Kata pod in ns-<user-oid>
 │  Projected SA token federated to per-user UAMI
 │  (One UAMI per user namespace — respects the 20-FC-per-UAMI ceiling)
 ▼
Sandbox pod
 │  Workload Identity → kv-user-<short-oid>
 │  Cross-user KV access → 403 (per-user scope)
 ▼
execd writes audit row: user_oid, session_id, command, exit_code, egress_dest, trace_id, ts
```

**Two non-obvious traps the design defends against:**

- **OBO audience trap** — if scope is set to `management.azure.com`, AKS audit shows the control-plane SP and the user is erased. Defended by `apps/control-plane/app/auth/obo_exchange.py` plus `tests/test_obo_exchange.py` which asserts the scope contains the AKS server app ID and **not** `management.azure.com`.
- **Workload Identity propagation race** — UAMI ↔ federated credential propagation can take 60-90 s. `POST /users/<oid>/provision` blocks on a synchronous propagation probe; 90 s bound; returns 503 + `Retry-After: 90` on timeout (RALPLAN Pre-Mortem #3).

### 3.4 Trust boundary

```
┌────────────────────────────────────────────────────────────┐
│  Trusted: control plane                                     │
│    - Small FastAPI; validates tokens; OBOs as user          │
│    - Runs as runc on ACA managed surface                    │
├────────────────────────────────────────────────────────────┤
│  Semi-trusted: AKS control plane                            │
│    - Sandbox controller (runc, system pool)                 │
│    - execd DaemonSet (runc on Kata pool — it's infra)       │
│    - Controller NOT cluster-admin; namespace-scoped RBAC    │
├────────────────────────────────────────────────────────────┤
│  Untrusted: sandbox pod (Kata VM)                           │
│    - User's untrusted code runs here                        │
│    - Own kernel (Kata MSHV VM)                              │
│    - drop ALL caps, run-as-non-root, RuntimeDefault seccomp │
│    - Egress: Cilium L7 → Azure Firewall → default-deny      │
│    - No ARM token; only its scoped UAMI to its own KV       │
└────────────────────────────────────────────────────────────┘
```

Verified by three deterministic container-escape PoCs (`tests/security/test_container_escape.py`):
1. **CVE-2022-0492-class** — abuse `cgroup release_agent` (must fail under Kata VM).
2. **proc-self-exe replace** — runc CVE-2019-5736-style (must fail).
3. **Kernel module load** — `insmod` a benign kmod (must fail; Kata guest kernel disallows kmod loading by default).

Wired into `.github/workflows/pr.yml` (gated on the `requires_aks_kata` marker — runs only when a dev cluster is available) and `.github/workflows/nightly.yml` (runs against prod nightly). If any PoC succeeds, the build fails and the platform team is paged.

### 3.5 Network — Firewall SKU is conditional (RALPLAN Task 1.5)

The decision tree:

- **If Phase 0 task 0.4 PASSES** (Cilium ACNS L7 works on Kata): sandbox egress enforced at **Cilium L7 with FQDN allowlist**; **Azure Firewall Standard** provides L3/L4 backup.
- **If Phase 0 task 0.4 FAILS** (Cilium L7 ineffective on Kata): **Azure Firewall Premium** is the primary L7 enforcer. TLS-MITM is NOT viable inside Kata pods (no CA distribution to untrusted containers); Premium IDPS configured for HTTP host-header rules (covers `pypi.org`, `npmjs.org`) and **SNI-based filtering** for HTTPS. AC #17 rewritten to "egress to a non-allowlisted FQDN over HTTPS is blocked at SNI inspection."

The Phase 0 spike outcome is recorded in `docs/integration-spikes.md`; Task 1.5 picks the SKU via Bicep parameter `egressEnforcementTier` at deploy time.

### 3.6 Image governance (RALPLAN Task 1.3, Pre-Mortem #2)

- **ACR Premium**, zone-redundant, geo-replication-ready, private endpoint only.
- **Notation (Notary v2)** signs every image with a Key Vault-backed cert.
- **Dual-cert TrustPolicy, IaC-enforced.** `kv.bicep` provisions TWO certs at all times (`notation-cert-primary`, `notation-cert-secondary`) with **≥ 14-day overlapping validity**. Rotation = promote secondary to primary, mint new secondary. Deployment cannot complete with fewer than 2 certs.
- **Canary CI test:** post-rotation, sign a known test image with the new cert AND the old cert; schedule both on a non-production node; assert both `Running`. Old cert retired only after the canary passes 100% across 7 consecutive days.
- **Ratify + Gatekeeper** at admission. Unsigned image → admission denial with a clear message (AC #13).
- **Defender for Containers** scans on push. High/critical CVEs block deployment (AC #34).
- **DR drill (quarterly):** snapshot ACR via geo-replication + cold `az acr import`; back up KV signing cert to Azure Backup + offline copy in a separate subscription; simulate KV loss; rebuild from backup; verify signed images schedule. RTO 4 h, RPO 24 h.

### 3.7 Audit & observability — two lanes

- **Fast-path audit (≤60 s SLA, AC #12):** execd structured stdout → Fluent Bit → **Event Hubs → Stream Analytics → `SandboxAuditFast_CL`** in Log Analytics. Container Insights is too slow for AC #12 (2-10 min ingestion per Microsoft docs); this lane bypasses it (RALPLAN Task 1.6, Critic B-C3).
- **Slow-path logs:** Container Insights for routine container logs. App Insights for distributed traces (OpenTelemetry). Cilium flow logs + AKS audit logs + Azure Firewall logs → same LAW.
- **Distributed trace assertion:** SDK injects `traceparent` header; a single `trace_id` spans App Insights (ACA), Container Insights (AKS), Cilium Hubble flow log, and execd stdout, with parent-child spans correctly linked (`tests/e2e/test_distributed_trace.py`, RALPLAN Critic S-C9).
- **Defender-Kata-gap detection:** explicit KQL alert in Log Analytics watches for cgroup/proc/kmod activity from Kata nodes (RALPLAN Critic S-C3).

### 3.8 Failure modes pre-empted (RALPLAN §Pre-Mortem)

| Failure | Mitigation |
|---|---|
| Kata cold-start > 5 s wrecks UX | Three-tier latency strategy: pre-warm DaemonSet (taints nodes `pre-warm=pending:NoSchedule` until pulls complete); fresh pod with user identity; opt-in shared warm pool (low-latency tier, role-gated, audited). |
| Notation cert rotation blocks workloads | Dual-cert TrustPolicy (IaC-enforced), 14-day overlap, canary CI test (above). |
| Workload Identity propagation race | Synchronous probe in `POST /users/<oid>/provision`; 90-s bound; 503 + `Retry-After` on timeout. |
| Upstream fork drift causes missed CVE | Patch surface ≤ 100 LOC in separate middleware module; weekly upstream-sync CI; CVE response runbook with 72-h SLA. |
| KV signing cert lost / corrupted | Azure Backup + offline copy; quarterly DR drill (RTO 4 h, RPO 24 h). |
| Container escape from Kata | Three deterministic PoCs in CI (gated on dev-cluster marker for PRs; nightly against prod). Nightly failure pages on-call. |

### 3.9 Ontology — what the words mean (spec §Ontology)

| Term | Definition |
|---|---|
| **Sandbox** | Ephemeral Kata-isolated Linux pod in a user's namespace, running an ACR-signed base image, with per-pod egress policy. Stateless in v1. |
| **Session** | A unit of sandbox usage. `session_id = uuid`, owned by exactly one user OID, 1:1 with a pod. |
| **User** | A Microsoft Entra ID identity (member or B2B guest). OBO-auth; owns `ns-<user-oid>` + UAMI. |
| **Project** | Dev-Box-parity concept; v1 maps to the user namespace. Multi-user grouping in v1.5. |
| **Pool** | Kata node pool + curated image set available to sandboxes. v1 has one pool. |
| **Image definition** | YAML spec layering custom tasks on a curated base. v1.5 feature. |
| **Curated base image** | Notation-signed ACR image maintained by the platform team. v1 has 3-4. |
| **Control plane** | FastAPI + portal API + portal frontend on ACA. |
| **Runtime plane** | Sandbox controller + execd + sandbox pods on AKS (vendored runtime). |
| **Audit log** | Structured Log Analytics entry: `user_oid, session_id, command, exit_code, egress_dest, ts, trace_id`. |
| **Notation signature** | Notary v2 signature stored as OCI referrer in ACR; enforced at admission by Ratify + Gatekeeper. |

---

## 4. Build status (verified against the repo at 2026-05-20)

### 4.1 What is in the repo (ground truth from `git ls-files` + `az resource list`)

- **IaC, complete.** 10 Bicep modules under `infra/bicep/` (`aca`, `acr`, `aks`, `appgw`, `entra`, `firewall`, `kv`, `network`, `observability`, `user`). `az deployment sub validate` returns `"error": null`. The template is ready to deploy.
- **Helm chart, complete.** 11 templates under `infra/helm/opensandbox/templates/`: controller deployment + RBAC, execd DaemonSet, image-prewarm DaemonSet + RBAC, Cilium default-deny + FQDN allowlist, Ratify ConfigMap, Azure-Policy / Gatekeeper constraints, per-namespace NetworkPolicy template.
- **FastAPI control plane source, complete.** `apps/control-plane/app/` (`main.py`, `aks_client.py`, `config.py`, `exceptions.py`, plus `auth/`, `middleware/`, `routers/` subpackages). Tests: `test_jwt_validator.py`, `test_obo_exchange.py` (asserts OBO audience is NOT `management.azure.com`), `test_sessions_router.py`.
- **Security + E2E tests, written.** `tests/security/test_container_escape.py` with three escape PoC payloads under `tests/security/escape_pocs/`. `tests/e2e/test_distributed_trace.py` and `tests/e2e/test_sandbox_lifecycle.py`. All marked `requires_aks_kata` or `requires_deployed_env` (SKIP without a live cluster).
- **Python SDK, complete.** `sdks/python/opensandbox_azure/` (`client.py`, `models.py`, `exceptions.py`, `_tracing.py`) + tests + examples (`basic_usage.py`, `agent_usage.py`).
- **JS SDK, stub.** `sdks/js/src/` surface defined; behavior not implemented.
- **Go SDK, broken right now.** `sdks/go/opensandbox/client.go` imports `azcore`, `azcore/policy`, `azidentity`; `go.sum` is missing those entries — package does not compile. Phase 2 QA item, fix in flight.
- **Kimi K2.5 agent demo, code present, not yet executed E2E.** `examples/kimi-agent-demo/{agent.py, kimi_client.py, aci_sandbox.py}` + 3 task prompts under `tasks/`. The sandbox base image was built via `az acr build` and pushed to ACR (digest `sha256:9cc7b7298f493...`). The agent has not yet run E2E against a deployed control plane.
- **CI/CD, scaffolded.** `.github/workflows/{pr.yml, main.yml, nightly.yml}`. Two have YAML parse errors at known lines (Phase 2 QA items, fix in flight).
- **Docs and runbooks, complete.** `docs/{ARCHITECTURE.md, acceptance-checklist.md, mission-and-architecture.md}` + `runbooks/{cve-response.md, dr-drill.md, incident-response.md, onboarding.md}`.
- **Acceptance checklist** tracks 34 ACs; currently **7 PASS · 1 IN_PROGRESS · 28 PENDING** (PASSes are scaffold-only; PENDINGs all need a live environment).

### 4.2 What is deployed in Azure right now (`az resource list -g rg-opensandbox-demo`)

- Resource group `rg-opensandbox-demo` in `eastus2`.
- ACR `acropensandboxdemo7075` (Basic SKU) — holds the Kimi sandbox base image.
- **Nothing else yet.** AKS, ACA, Key Vault, Firewall, App Gateway, Log Analytics, Event Hubs, Stream Analytics — all defined in Bicep, none deployed. `az deployment sub create` is in flight in a background task as of this writing.

### 4.3 Goal — when is this done? (from `.omc/state/goal.md`)

The goal is satisfied when **all five** of these hold:

1. All 3 commits pushed to `origin/main`. ✅ DONE (`20c7e31..ef4a318 main -> main`).
2. All 5 Phase-2 critical QA issues resolved (workflows parse; Go SDK builds; `test_sessions_router.py` passes). 🔄 IN FLIGHT.
3. `az deployment sub create` returns **Succeeded**, OR a documented STOP with explicit blocker. 🔄 IN FLIGHT.
4. The Kimi K2.5 agent demo runs end-to-end against the deployed stack, OR a documented STOP with explicit blocker. ⏸ STAGED (driver scripts ready in `scripts/phase4/`).
5. `docs/acceptance-checklist.md` reflects current PASS / FAIL / DEFERRED for all 34 ACs. ⏸ PENDING.

Until those five flip to ✅, this build is not done. The Stop hook is enforcing exactly that.

---

## 5. References

- [Vendored sandbox runtime attribution](../THIRD_PARTY_LICENSES.md) — third-party project and license details.
- [Azure Dev Box features](https://azure.microsoft.com/en-us/products/dev-box/#features) — the conceptual peer we're aiming at parity with.
- [AKS Pod Sandboxing (Kata)](https://learn.microsoft.com/azure/aks/use-pod-sandboxing)
- [AKS Workload Identity](https://learn.microsoft.com/azure/aks/workload-identity-overview)
- [Entra OBO flow](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Notation / Ratify on AKS](https://learn.microsoft.com/azure/security/container-secure-supply-chain/articles/validating-image-signatures-using-ratify-aks)
- [AKS Baseline Architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks)
- Spec, plan, trace, goal: see top of this document.
