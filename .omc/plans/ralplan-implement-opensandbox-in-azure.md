# Plan: implement-opensandbox-in-azure (RALPLAN-DR, deliberate mode) — FINAL

> **Mode:** consensus / deliberate
> **Spec:** `.omc/specs/deep-dive-implement-opensandbox-in-azure.md`
> **Trace:** `.omc/specs/deep-dive-trace-implement-opensandbox-in-azure.md`
> **Status:** v0.3 FINAL — Architect APPROVED, Critic APPROVED WITH IMPROVEMENTS (all BLOCKING resolved).
> **Consensus history:** v0.1 Planner → Architect REQUEST REVISION (4 BLOCKING, 4 STRONG, 2 NICE) → v0.2 Planner-revision → Architect APPROVED with 3 minor → Critic APPROVE WITH IMPROVEMENTS (7 BLOCKING, 10 STRONG, 4 NICE) → v0.3 Planner-revision (all 7 BLOCKING + 10 STRONG resolved; 4 NICE deferred to v1.5 RFC).

---

## RALPLAN-DR Summary

### Principles (5)

1. **Trust boundary lives in Kata, not in namespaces.** All sandbox pods run `runtimeClassName: kata-vm-isolation`. Namespaces are logical isolation only. **Kata's trust boundary is verified by a deterministic container-escape test (Phase 6 task 6.7).**
2. **Identity is end-to-end and per-user.** Every action traces to an Entra user OID via OBO → JWT → Workload Identity. The opt-in `low_latency=true` shared-pool tier is the **only** exception, and it is explicitly audited, role-gated, and rate-limited (Task 3.4).
3. **Deny by default at every layer.** Azure Policy Deny mode, Cilium NetworkPolicy default-deny, Ratify-required signatures, Azure Firewall allowlist, AKS Kubernetes-RBAC default-deny bound to Entra groups.
4. **Validate upstream and integration assumptions before building.** Phase 0 tasks 0.1-0.4 cover OpenSandbox CRD scope + Cilium-ACNS-on-Kata L7 behavior. Phase 0 gates Phase 1.
5. **MVP is verifiable end-to-end, not feature-complete.** Every AC is testable by `kubectl`, `curl`, load test, log query, portal screenshot, or PoC exploit. **Measurement procedures defined for every numeric SLA.**

### Decision Drivers (top 3)

1. **Hard isolation for untrusted code is non-negotiable.** → Kata mandatory; container-escape test in CI; signed images; private cluster.
2. **Audit trail must be unbroken and timely.** → Entra OBO end-to-end; Diagnostic Settings (not Container Insights) for fast-path audit; warm pools cannot break user attribution.
3. **Build velocity matters: 4-6 weeks for v1.** → Single region, single AKS cluster, Bicep, curated images.

### Viable Options Considered

#### Option A: Everything-on-AKS (single-plane)
Reinstated as live fallback. Triggered if Phase 0 task 0.4 or Phase 3 task 3.3 reveal blocking issues.

#### Option B: Hybrid ACA + AKS+Kata (CHOSEN)
With v0.2/v0.3 corrections to identity (no MI-OBO), latency tiers (image pre-warm + opt-in shared), and ACA `minReplicas: 1`.

#### Option C: ACA Dynamic Sessions replacing K8s runtime
**Rejected (one-sentence strengthening per Critic NICE-3):** No per-namespace UAMI projection, no Cilium NetworkPolicy granularity at the Pod level, no CRD support — would require forking and replacing OpenSandbox's runtime layer, losing upstream parity.

#### Option D — NEW (Critic S-C2 phantom): Kata Confidential Containers (Kata-CC / SEV-SNP) on AKS
**Rejected for v1:** Same Defender-coverage gap as plain Kata; adds SEV-SNP attestation flow complexity for which v1 has no compliance driver (trust model is agent-as-tenant, not external mutually-untrusted). Architecture preserves an upgrade path: switching `runtimeClassName: kata-vm-isolation` to `kata-cc-isolation` is a one-line per-pod change at v1.5+ if compliance requirements appear.

#### Option E — NEW (Critic S-C2 phantom): AKS Automatic mode
**Rejected for v1:** Would simplify Bicep (Cilium+ACNS, Workload Identity, image cleaner, defaults pre-configured), BUT does not expose node-pool taint/tolerations or runtimeClass configuration at the level Kata Pod Sandboxing requires (`kubernetes.azure.com/kata-mshv-vm-isolation` label; manual `runtimeClass` deployment is documented for Standard tier only). Revisit at v1.5 when Automatic-mode Kata support is GA.

**Invalidation rationale:** C (CRD/runtime model), D (no compliance driver, regression-able later), E (Kata controls insufficient in Automatic mode). A remains a documented fallback, not invalidated.

---

## Pre-Mortem (4 scenarios, deliberate)

### Failure #1: "Kata cold-start latency wrecks the agent UX"
Same as v0.2 — three tiers (image pre-warm DaemonSet, fresh pod with user identity, opt-in shared pool). AC #6 measurement now defined (see Acceptance Criteria addendum).

### Failure #2: "Notation+Ratify admission blocks legitimate workloads after a cert rotation" (REVISED per Critic B-C4)
**Mitigations (IaC-enforced, not just runbook):**
- **Ratify TrustPolicy is parameterized to accept TWO `trustedCerts` at all times** — one "primary", one "secondary". `kv.bicep` issues two certs concurrently; rotation = promote secondary to primary, mint new secondary. Bicep deployment cannot complete with fewer than 2 certs configured (validated by deployment script).
- **Overlap duration: 14 days minimum, IaC-enforced** via cert expiry dates that overlap by ��� 14 days. New cert minted at 21-day remaining lifetime; old cert removed only at 7-day remaining lifetime.
- **Canary CI test (concretized per Critic NICE-4):** post-rotation, CI signs a known test image with the NEW cert AND a known test image with the OLD cert (still trusted during overlap), schedules both on a non-production node, asserts both `Running`. Removes both test pods. Asserts the OLD cert can be safely retired only after the canary passes 100% across 7 consecutive days.

### Failure #3: "Workload Identity federated-credential propagation race"
Same as v0.2 — synchronous propagation probe in `POST /users/<oid>/provision`.

### Failure #4 — NEW (Critic S-C6): "OpenSandbox upstream fork drift causes us to miss a security patch"
**Scenario:** A CVE is filed against OpenSandbox upstream. Our fork (with Entra patches) has diverged enough that the upstream patch doesn't apply cleanly. We delay by 2-3 weeks while rebasing, during which sandboxes execute code on a known-vulnerable runtime.
**Probability:** Medium (open-source security patch cadence is variable; OpenSandbox is < 1 year old).
**Mitigations:**
- **Patch surface minimization:** ALL Entra-related patches go in a **separate FastAPI middleware module** (not modifications to upstream files). Diff against upstream is forced to ≤ 100 LOC.
- **Weekly upstream sync CI job:** `git fetch upstream && git rebase origin/upstream/main` on a dedicated branch; runs the full test suite; opens a draft PR if changes detected; alerts the platform team via Teams webhook.
- **CVE monitoring:** Dependabot + GitHub Security Advisories enabled on the fork repo; manual subscription to OpenSandbox's GitHub Security tab.
- **Incident SLA:** Critical CVE → patch deployed within 72 h. Documented in `runbooks/cve-response.md`.
**Early-warning signal:** First weekly upstream sync CI run that fails to rebase cleanly.

---

## Expanded Test Plan (REVISED per Critic B-C5, B-C6, S-C9, NICE)

### Unit tests
(Unchanged: Bicep ARM-TTK + `what-if`, FastAPI pytest ≥80% coverage with mocked JWKS, fork patches, 3 SDK suites, Helm lint + template snapshots.)

### Integration tests
(All v0.2 integration tests retained, plus:)
- **Cross-plane OBO audience verification (Critic S-C8):** assert the OBO-exchanged token issued by the control plane has `aud` claim equal to the AAD-integrated AKS server app ID (per [AKS managed-AAD docs](https://learn.microsoft.com/azure/aks/managed-azure-ad)), NOT `https://management.azure.com/`. Task 3.3 step 4 corrected accordingly.
- **Propagation-probe negative path (Critic NICE):** force a UAMI-FC creation race; assert `POST /users/<oid>/provision` returns 503 with `Retry-After: 90` on timeout AND that idempotent retry succeeds.

### E2E tests
(All v0.2 E2E retained, plus:)
- **Cold-path distributed trace assertion (Critic S-C9):** SDK injects `traceparent` header; assert a SINGLE trace_id spans App Insights (ACA), Container Insights (AKS), Cilium Hubble flow log, and execution-daemon stdout — with parent-child spans correctly linked end-to-end.
- **Container escape test (Critic B-C5):** in CI, schedule a Kata pod with an unprivileged image. Attempt three known container-escape PoCs inside the pod:
  1. **CVE-2022-0492-class:** abuse `cgroup release_agent` (must be unable to write to release_agent file from inside Kata VM).
  2. **proc-self-exe replace:** attempt to overwrite `/proc/self/exe` to gain runc-style escape (must fail under Kata's VM boundary).
  3. **Kernel module load:** `insmod` a benign kmod (must fail — Kata guest kernel disallows kmod loading by default).
  Assert all three PoCs fail with the expected denial. **Test runs on every PR and every nightly main build.** If any PoC succeeds, the build fails and the platform team is paged.
- **DR/restore test (Critic B-C6):** quarterly DR drill:
  1. Snapshot ACR (`az acr replication` to a backup region + `az acr import` for cold copy).
  2. Backup KV signing cert + private key into a sealed offline secret store (Azure Backup for KV is GA per [KV backup docs](https://learn.microsoft.com/azure/key-vault/general/backup); plus an offline copy in a separate subscription).
  3. Snapshot LAW retention to a backup storage account (Diagnostic Settings archive).
  4. Simulate disaster: delete the primary KV; rebuild from backup; verify Notation TrustPolicy refresh propagates and signed images schedule.
  5. RTO target: 4 hours. RPO target: 24 hours.

### Security tests
- Container escape (above).
- OBO-token-leak test: extract a downstream token from a Kata pod's mounted SA volume; assert token is bound to the pod's namespace (audience + caller IP) and rejected if used from a different namespace.
- Egress-bypass test: from inside a Kata pod, attempt to route around Cilium NetworkPolicy via `iproute2`; assert blocked.

### Observability tests
(Unchanged from v0.2, plus:)
- **Defender-Kata-gap detection (Critic S-C3):** KQL alert in Log Analytics:
  ```kql
  ContainerLog | where Computer startswith "aks-kata-" and (LogEntry contains "/proc/" or LogEntry contains "kmod" or LogEntry contains "release_agent")
  | summarize count() by Computer, _ResourceId, bin(TimeGenerated, 5m) | where count_ > 3
  ```
  Routes to action group with PagerDuty. Threshold tunable; baseline established in Phase 6.
- **Upgrade test (Critic S-C5b):** Bicep `what-if` from current to NEXT version of every module on a per-PR basis; CI fails on detected immutable-property drift (e.g., AKS network plugin change forces replace).

---

## Implementation Phases

### Phase 0 — Validate upstream + integration assumptions (2-3 days, BLOCKING)
(Tasks 0.1-0.4 unchanged from v0.2.)

### Phase 1 — Foundations: Bicep + AKS + ACR + Identity (Weeks 1-2)

**Task 1.1 — VNet + private endpoints + external ingress.**
(Same as v0.2 — explicit subnet CIDRs, App Gateway WAF on `snet-appgw`.)

**Task 1.2 — AKS cluster.**
(Same as v0.2 — private, AAD-integrated, Cilium-ACNS, 2 node pools, Workload Identity, ≥3 AZs.)
**Addition (Critic NICE-1):** AKS Kubernetes-RBAC bound to Entra groups via `--aad-admin-group-object-ids`; no user has cluster access without explicit group membership (default-deny at K8s API level, not just Azure RBAC).

**Task 1.3 — ACR Premium + Key Vault.**
**Addition (Critic B-C4):** `kv.bicep` provisions TWO certs at all times (`notation-cert-primary`, `notation-cert-secondary`) with overlapping validity windows ≥ 14 days. Deployment script asserts both exist before completion.

**Task 1.4 — Entra + UAMI baseline.**
(Same as v0.2; portal app reg configured for ACA Easy Auth.)

**Task 1.5 — Azure Firewall + L7 egress decision tree (REVISED per Critic B-C1).**
Resolves the v0.2 contradiction. Decision logic:
- **If Phase 0 task 0.4 passes (Cilium ACNS L7 works on Kata):** sandbox egress is enforced at Cilium L7 with FQDN allowlist; Azure Firewall Standard SKU provides L3/L4 backup (deny-all egress except via Cilium-allowed FQDNs after DNS resolution).
- **If Phase 0 task 0.4 fails (Cilium L7 ineffective on Kata):** Azure Firewall is **upgraded to Premium SKU** and acts as the primary L7 egress enforcer. We then must accept the TLS-inspection-CA caveat: TLS-MITM is not viable inside Kata pods (no CA distribution to untrusted containers), so Premium IDPS is configured for **HTTP application rules (host-header-based)** only — covers package managers (`pypi.org`, `npmjs.org`) but not arbitrary HTTPS. The remaining HTTPS traffic falls back to **SNI-based filtering** (which Premium supports without TLS termination). AC #17 is rewritten to "egress to a non-allowlisted FQDN over HTTPS is blocked at SNI inspection" — explicitly testable.
- **Either way:** AC #17 is testable. The decision is recorded in `docs/integration-spikes.md` after Phase 0; Task 1.5 picks the corresponding Firewall SKU at deploy time via Bicep parameter `egressEnforcementTier`.

**Task 1.6 — Observability.**
(Same as v0.2.)
**Addition (Critic B-C3):** audit log path uses **Diagnostic Settings → Event Hubs → Stream Analytics → Log Analytics** for sub-60-s ingestion of sandbox-execution events, INSTEAD of Container Insights (which has 2-10 min ingestion latency per [docs](https://learn.microsoft.com/azure/azure-monitor/logs/data-ingestion-time)). Routine non-audit logs continue via Container Insights. AC #12 is now feasible.

### Phase 2 — Sandbox runtime on AKS+Kata (Weeks 2-3)

(Tasks 2.1-2.6 same as v0.2.)

**Task 2.7 — Image pre-warm DaemonSet (REVISED per Architect improvement #2 + Critic S-C5):**
- DaemonSet on the Kata pool runs an `initContainer` that `crictl pull`s all curated images on node start.
- Node is **tainted `pre-warm=pending:NoSchedule`** at autoscale provisioning; taint is removed by a controller only after the DaemonSet's `livenessProbe` reports all pulls complete.
- Sandbox pods tolerate `runtime=kata` BUT NOT `pre-warm=pending` — so the autoscaler cannot schedule sandbox pods onto a not-yet-warm node.
- Bandwidth throttle on the pre-warm DaemonSet to avoid ACR PE saturation: max 2 parallel pulls per node, max 30 simultaneous pulls cluster-wide (configurable).

### Phase 3 — Control plane API on ACA + cross-plane auth (Week 3)

(Tasks 3.1-3.4 same as v0.2 with one addition.)

**Task 3.3 step 4 (REVISED per Critic S-C8):** OBO exchange specifies `scope=<aks-server-app-id>/.default` where `<aks-server-app-id>` is the AAD-integrated AKS server app's application ID (read from the AKS cluster's `aadProfile.serverAppID` output, parameterized in Bicep). NOT `https://management.azure.com/.default`.

**Task 3.4 shared-tier hardening (REVISED per Critic S-C1):**
- Shared-tier UAMI (`id-shared-warm-pool`) is scoped to **read-only** access to the `sandbox-shared` Key Vault (no write to any user-scoped resource).
- Token lifetime on the shared-tier SA reduced to **5 minutes** (default is 60+ min) via the FederatedIdentityCredential's `expiration` claim. Forces frequent re-issuance; reduces theft blast radius.
- Conditional Access policy: shared-tier tokens issued only when caller IP is the control-plane VNet range.
- Rate limit: shared-tier sessions capped at 100 concurrent platform-wide; per-user shared-tier sessions capped at 5 concurrent.

### Phase 4 — SDKs (Week 4)
(Unchanged from v0.2.)

### Phase 5 — Read-only observability portal (Week 5)
(Unchanged from v0.2.)

### Phase 6 — Hardening, docs, runbooks, CI/CD (Week 6)

(Tasks 6.1-6.6 same as v0.2.)

**Task 6.7 — NEW (Critic B-C5): Container escape test in CI.**
The 3 PoCs from the Expanded Test Plan run on every PR + nightly. Failure blocks merge; nightly failure pages on-call.

**Task 6.8 — NEW (Critic B-C6): DR/restore drill.**
Quarterly drill from the Expanded Test Plan; first drill in Phase 6 establishes baseline RTO/RPO.

**Task 6.9 — Document fallback to Option A (from v0.2).**

---

## Risks and Mitigations (FULL TABLE — Critic B-C7 housekeeping)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Kata cold-start > 5 s p95 | Medium | High | Image pre-warm DaemonSet + node-readiness gate (Task 2.7); opt-in shared-pool tier |
| OpenSandbox CRD scope requires cluster-admin | Medium | High | Phase 0 task 0.1; deltas in `upstream-delta.md` |
| Notation cert rotation downtime | Medium | High | IaC-enforced dual-cert + 14-day overlap + canary CI (pre-mortem #2) |
| Workload Identity FC propagation race | Medium-High | High | Synchronous propagation probe in `POST /users/<oid>/provision` |
| Defender doesn't assess Kata pods | High (known) | Medium | KQL detection alert (observability test); Cilium flow logs + execd audit |
| Azure Policy propagation lag | Medium | Low-Medium | Audit first; promote to Deny after 7-day clean baseline |
| Cilium ACNS L7 ineffective on Kata pods | Medium | Medium | Phase 0 task 0.4; conditional Firewall SKU per Task 1.5 |
| ACA cold-start adds to cold path | Low | Medium | `minReplicas: 1` on control plane |
| MI-OBO definitional bug | N/A (removed) | Critical | Confidential-client OBO via app reg + KV secret |
| Notation v1→v2 migration | Medium (over time) | Medium | Pin v1.16+; RFC at v1.5 |
| External SDK can't reach private ingress | Medium | High | App Gateway + WAF on `snet-appgw` |
| D8s_v5 + Gen2 + Kata capacity in East US 2 | Low-Medium | Medium | Capacity reservation; VMSS multi-SKU fallback (D-, E-, F-series v5 Gen2) |
| KEDA scale-down evicts in-flight sessions | Low | Medium | `cooldownPeriod` ≥ pod-lifetime cap (4 h hard cap, PreStop hook for graceful drain) |
| Cross-plane (ACA→AKS) latency | Low | Medium | Private endpoint + AAD-integrated AKS API; collapse to Option A if p95 > 200 ms |
| OpenSandbox upstream fork drift / CVE delay | Medium | High | Pre-mortem #4: minimal-patch middleware + weekly sync CI + 72-h SLA |
| Container escape from Kata pod | Low | Critical | Container-escape test in CI (Task 6.7) |
| KV signing cert / ACR / LAW loss | Low | Critical | Quarterly DR drill (Task 6.8); KV backup; ACR replication; LAW archive |
| ACR PE bandwidth saturation at autoscale | Medium | Medium | Pre-warm DaemonSet bandwidth throttle (Task 2.7) |
| Shared-tier UAMI token theft | Low | Medium | 5-min token lifetime + IP-bound CA policy + rate limits (Task 3.4) |

---

## Verification Steps (FULL LIST — Critic B-C7 housekeeping)

1. Run `az deployment sub what-if -f main.bicep -p main.dev.parameters.json` — clean output expected.
2. Deploy: `az deployment sub create -f main.bicep -p main.dev.parameters.json`. Time: 15-25 minutes.
3. From jump host: `az aks get-credentials --resource-group rg-opensandbox-dev --name aks-opensandbox-dev`.
4. `kubectl get nodes -L agentpool,kubernetes.azure.com/runtime` — verify Kata + system pools.
5. `kubectl get runtimeclass kata-vm-isolation` — exists.
6. Run integration test suite: `pytest tests/integration/` against deployed env.
7. Run E2E test suite: `pytest tests/e2e/` (creates real sessions, real users via test Entra tenant).
8. Run load test: `k6 run tests/load/concurrent-create.js`.
9. Open portal in browser; verify SSO and read-only flows.
10. Execute the 34-AC checklist in `docs/acceptance-checklist.md` (revised — see Acceptance Criteria Addendum).
11. Phase 0 task 0.4 spike result reviewed BEFORE Phase 1 Bicep deploy.
12. Provisioning probe latency benchmark: p95 < 60 s over 25 parallel provisions.
13. AKS audit log query: `az monitor log-analytics query -w <law-id> --analytics-query "AKSAudit | where ObjectRef.Name startswith 'sandbox-' | summarize count() by User.Username"` — assert User.Username is the user's Entra `upn`, NOT the control-plane app ID.
14. Container-escape PoC suite (Task 6.7): all 3 escape attempts blocked.
15. DR drill (Task 6.8): KV signing cert restored from backup; canary signed image schedules successfully within RTO target.
16. SLA measurement protocol (Acceptance Criteria Addendum): cold-path p95 measured on 100 samples per tier, with node-readiness ≥ 120 s post DaemonSet Ready signal.

---

## Acceptance Criteria Addendum (Critic B-C2, B-C3, S-C7, S-C10)

The 34 spec ACs are amended as follows:

- **AC #4 reworded:** "A request to ACA control plane with a missing or invalid Entra bearer token returns 401. A request with a valid token whose `aud` claim is not the OpenSandbox API app ID returns 401. A valid request triggers OBO exchange and reaches AKS API server with the user's identity, verified by `AKSAudit` log entry showing `User.Username == <user.upn>`."
- **AC #6 measurement protocol:**
  - Cold (default tier): p95 < 5 s over **100 samples**, sampled at random across 5 minutes, on nodes that have been Ready for ≥ 120 s with image pre-warm DaemonSet `livenessProbe` passing. Sample = wall-clock from `POST /sessions` to first byte of session response.
  - Warm (shared tier): p95 < 500 ms over **100 samples** with the shared pool at steady-state (≥ 5 idle pods per image).
  - Reset method: drain a single Kata node, re-add it, wait for DaemonSet Ready signal, run 100 cold samples.
- **AC #11 KV scope:** "Each user has a dedicated Key Vault `kv-user-<short-oid>` provisioned by `POST /users/<oid>/provision`. The user's UAMI has `Key Vault Secrets User` role at that vault's scope only. Cross-user secret access returns 403."
- **AC #12 audit-trail SLA:** "Audit log entry for a session command appears in Log Analytics queryable within **60 s** of command execution. Path: execd → Fluent Bit → Event Hubs → Stream Analytics → Log Analytics (NOT Container Insights). Verified by injecting a known UUID command and asserting the query returns within 60 s."
- **AC #17 egress:** "From inside a Kata sandbox pod, `curl https://pypi.org/` succeeds and `curl https://evil.example/` fails. Failure mode is documented per Phase 0 task 0.4 result: Cilium L7 deny if L7 works on Kata, otherwise Azure Firewall Premium SNI deny."

---

## ADR (FINAL)

- **Decision:** Implement OpenSandbox on Azure via Hybrid ACA (control plane + portal) + AKS+Kata (sandbox runtime), East US 2, ~500 concurrent sandbox target, Bicep IaC. Fallback to Option A (everything-on-AKS) on documented Phase 0 / Phase 3 triggers.

- **Drivers:** (1) Hard isolation for untrusted code execution; (2) Unbroken audit trail with sub-60s latency; (3) 4-6 week MVP velocity.

- **Alternatives considered:**
  - **Option A (everything-on-AKS):** Live fallback. Used if Phase 0 task 0.4 fails or Phase 3 cross-plane latency p95 > 200 ms.
  - **Option C (ACA Dynamic Sessions):** Rejected — no per-namespace UAMI, no Cilium Pod-level NetworkPolicy, no CRD support.
  - **Option D (Kata-CC SEV-SNP):** Rejected for v1 — no compliance driver; upgrade path preserved.
  - **Option E (AKS Automatic mode):** Rejected for v1 — Kata controls insufficient; revisit at v1.5.

- **Why chosen:** Option B preserves OpenSandbox's CRD-based K8s runtime while giving the portal a clean Easy Auth surface. The Architect+Critic loop corrected 4 design bugs (MI-OBO, warm-pool identity violation, KEDA scale-to-zero, missing Cilium-Kata spike) AND 7 measurement/test/IaC gaps (Firewall-SKU contradiction, audit-trail SLA infeasibility, AC #6 measurement protocol, Notation cert IaC enforcement, container-escape test, DR drill, verification housekeeping). The corrected v0.3 is materially safer than the original spec assumption set.

- **Consequences:**
  - Two IaC stacks (Bicep + Helm) and two observability lanes; correlated by `traceparent`.
  - Application Gateway + WAF terminates external SDK + portal traffic (~$300/month).
  - Cold-start SLA: 5 s p95 default tier / 500 ms p95 opt-in shared tier (measured on warm nodes).
  - Defender gap on Kata pods accepted; compensated by KQL alert + Cilium flow logs + execd audit + container-escape CI test.
  - Notation cert rotation requires IaC-enforced dual-cert state at all times.
  - Audit trail uses Diagnostic Settings → Event Hubs → Stream Analytics path (faster ingestion than Container Insights).
  - Quarterly DR drill for KV signing cert + ACR replication is operational requirement.

- **Follow-ups (v1.5+):**
  - Cilium ACNS L7 on Kata GA validation (if Phase 0 fallback was triggered).
  - Ratify v2 / image-integrity migration RFC.
  - Full self-service portal write actions.
  - Image-definition YAML pipeline (Dev-Box-style).
  - TTL idle reaper.
  - Multi-region (geo-replicated ACR active in 2+ regions).
  - Java + C# SDKs.
  - Revisit Kata-CC (SEV-SNP) if compliance scope expands.
  - Revisit AKS Automatic mode if Kata controls land.

- **Open items intentionally deferred (Critic NICE):**
  - N-C1: Azure Policy audit→Deny promotion gate — documented as runbook step, not gated in IaC for v1.
  - N-C2: Capacity fallback SKU enumeration — addressed in risk row, not yet in Bicep parameter.

---

## Changelog

- **v0.1** (2026-05-19): Initial planner draft.
- **v0.2** (2026-05-19): Architect review applied — fixed MI-OBO bug (B1), warm-pool identity violation (B2), KEDA scale-to-zero (B3); added Cilium-Kata Phase 0 spike (B4), synchronous propagation probe (S1), 7 new risk rows (S2), subnet sizing + App Gateway (S3), Firewall SKU re-scoping (S4); pinned Ratify version (N1); deferred portal role differentiation (N2). Architect APPROVED v0.2 with 3 minor non-blocking improvements.
- **v0.3 FINAL** (2026-05-19): Critic review applied —
  - **B-C1 fix:** Firewall SKU contradiction resolved via Phase 0-conditional decision tree (Task 1.5).
  - **B-C2 fix:** AC #6 measurement protocol defined (100 samples, ≥120s node readiness, cold-cache reset method).
  - **B-C3 fix:** Audit trail moved to Diagnostic Settings → Event Hubs → Stream Analytics → LAW path for sub-60s SLA (AC #12, Task 1.6).
  - **B-C4 fix:** IaC-enforced dual-cert Notation rotation with 14-day overlap (Task 1.3).
  - **B-C5 fix:** Container-escape test added to CI (Task 6.7) — 3 deterministic PoCs.
  - **B-C6 fix:** Quarterly DR drill added (Task 6.8); KV signing cert backup + ACR replication + LAW archive.
  - **B-C7 fix:** Full verification step list restated (16 steps), full risk table restated.
  - **S-C1 fix:** Shared-tier UAMI scope hardened — read-only KV, 5-min token, IP-bound CA policy, rate limits.
  - **S-C2 fix:** Kata-CC (Option D) and AKS Automatic mode (Option E) named-and-rejected with rationale.
  - **S-C3 fix:** Defender-Kata-gap detection — concrete KQL + alert + action group.
  - **S-C4 fix:** KEDA `cooldownPeriod` bounded by 4-h pod-lifetime cap + PreStop drain.
  - **S-C5 fix:** ACR pre-warm thundering-herd addressed — bandwidth throttle + node-readiness taint.
  - **S-C6 fix:** Pre-mortem #4 added — upstream-fork CVE drift, with minimal-patch + weekly sync CI + 72-h SLA.
  - **S-C7 fix:** AC #4 reworded to match v0.2 OBO + AAD-integrated AKS design.
  - **S-C8 fix:** OBO audience explicitly set to AKS server app ID (not management.azure.com).
  - **S-C9 fix:** Cold-path distributed trace assertion added to E2E tests.
  - **S-C10 fix:** Per-user KV (kv-user-<short-oid>) specified for AC #11.
  - **NICE-1:** AKS Kubernetes-RBAC bound to Entra groups added (Task 1.2).
  - **NICE-3:** ACA Dynamic Sessions rejection strengthened (one sentence).
  - **NICE-4:** Notation rotation canary assertion concretized.
  - **NICE-2 (capacity SKU enumeration):** Deferred to v1.5 — listed in Follow-ups.

**All Architect + Critic BLOCKING and STRONG items resolved.** Architect: APPROVED with 3 minor (all addressed). Critic: APPROVE WITH IMPROVEMENTS (all 7 BLOCKING + 10 STRONG addressed; 2 NICE applied, 2 NICE deferred to v1.5 RFC).
