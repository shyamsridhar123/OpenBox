# Acceptance Criteria Checklist

> 34 ACs from `.omc/specs/deep-dive-implement-opensandbox-in-azure.md`, **amended per the consensus plan** (`.omc/plans/ralplan-implement-opensandbox-in-azure.md`):
> - **AC #4** reworded — Entra OBO + AAD-integrated AKS API
> - **AC #6** measurement protocol defined — 100 samples, ≥120s node readiness, cold-cache reset
> - **AC #11** scope locked — per-user KV `kv-user-<short-oid>`
> - **AC #12** path changed — Diagnostic Settings → Event Hubs → Stream Analytics → LAW (sub-60s)
> - **AC #17** conditional — Cilium L7 on Kata if Phase 0 task 0.4 passes, else Azure Firewall Premium SNI

Status legend: `⬜ PENDING` · `🟡 IN_PROGRESS` · `🟢 PASS` · `🔴 FAIL` · `⏭️ DEFERRED to v1.5`

---

## A. Architecture deployed end-to-end

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 1 | `az deployment sub create` provisions the full stack (RG, AKS, ACA, ACR, KV, LAW, AI, Firewall, NAT, VNet ≥3 subnets, Entra app+UAMI, RBAC role assignments) | `scripts/phase0/spike-kind-local.sh` + `tests/integration/test_obo_flow.py` setup | ⬜ PENDING |
| 2 | `kubectl get runtimeclass kata-vm-isolation` shows the runtime class; sample Kata pod reaches Running | Verification step 5 in plan | ⬜ PENDING |
| 3 | ACA control plane responds `200` to `GET /healthz` over private FQDN | `tests/integration/` smoke | ⬜ PENDING |
| 4 | **(REWORDED)** Request without Entra Bearer → 401. Token with wrong `aud` → 401 (`WrongAudienceError`). Valid token triggers OBO; AKS audit log shows `User.Username == <upn>`, NOT control-plane app ID | `apps/control-plane/tests/test_jwt_validator.py` + `tests/integration/test_obo_flow.py` | ⬜ PENDING |

## B. Sandbox lifecycle via REST API

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 5 | `POST /sessions` creates Kata pod in `ns-<user-oid>` with Notation-signed ACR image | `apps/control-plane/tests/test_sessions_router.py` + `tests/e2e/test_sandbox_lifecycle.py` | ⬜ PENDING |
| 6 | **(MEASUREMENT PROTOCOL)** Default-tier p95 ≤ 5 s cold over 100 samples on nodes Ready ≥120 s with pre-warm DaemonSet Ready. Opt-in shared-tier p95 ≤ 500 ms over 100 samples at steady-state (≥5 idle pods per image). Reset: drain node, re-add, wait DaemonSet Ready, 100 cold samples. | `tests/e2e/test_sandbox_lifecycle.py` + `tests/load/concurrent_create.js` | ⬜ PENDING |
| 7 | `DELETE /sessions/<id>` terminates pod, cleans PVCs + NetworkPolicies | `tests/e2e/test_sandbox_lifecycle.py` | ⬜ PENDING |
| 8 | 100 simultaneous `POST /sessions` succeed at p95 < 10 s | `tests/load/concurrent_create.js` (k6) | ⬜ PENDING |

## C. Identity end-to-end

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 9 | Calling `/sessions` without Entra token → 401 | `apps/control-plane/tests/test_sessions_router.py` | ⬜ PENDING |
| 10 | User A trying to read User B's session → 403 | `tests/integration/test_obo_flow.py` | ⬜ PENDING |
| 11 | **(SCOPE LOCKED)** Each user has dedicated `kv-user-<short-oid>`. User A's UAMI succeeds reading User A's secret; cross-user attempt → 403. | `tests/integration/test_obo_flow.py` (cross-vault matrix) | ⬜ PENDING |
| 12 | **(FAST-PATH)** Audit log entry (user_oid → session_id → command → exit_code → egress_dest) appears in LAW within **60 s** via Event Hubs → Stream Analytics → `SandboxAuditFast_CL`. NOT via Container Insights. | `tests/observability/test_audit_60s.py` | ⬜ PENDING |

## D. Image governance

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 13 | Unsigned image push + pod schedule attempt → Ratify/Gatekeeper admission denial with clear message | `tests/integration/test_ratify_admission.py` | ⬜ PENDING |
| 14 | Signed image (Notation) → `Running` | `tests/integration/test_ratify_admission.py` | ⬜ PENDING |
| 15 | Defender for Containers shows CVE scan results for all 3-4 curated base images | Azure Portal verification (manual) + nightly workflow `nightly.yml` | ⬜ PENDING |
| 16 | Pod from non-ACR registry → denied by Azure Policy | `tests/integration/test_ratify_admission.py` (registry check) | ⬜ PENDING |

## E. Network isolation

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 17 | **(CONDITIONAL)** Sandbox pod `curl` to non-allowlisted FQDN → blocked. Mechanism: Cilium L7 if Phase 0 task 0.4 PASSes (recorded in `docs/integration-spikes.md`); Azure Firewall Premium SNI inspection otherwise. | `tests/integration/test_cilium_l7_kata.py` OR Firewall SNI test | ⬜ PENDING |
| 18 | Cross-namespace pod-to-pod HTTP blocked by Cilium NetworkPolicy | `tests/integration/test_obo_flow.py` (cross-user) | ⬜ PENDING |
| 19 | ACR / Key Vault DNS resolves to private endpoint IPs from inside the cluster | Manual `nslookup` from a debug pod + assertion in `tests/integration/` | ⬜ PENDING |

## F. SDKs

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 20 | Python: `pip install opensandbox-azure` → `client.create_session().run("echo hello").stdout == "hello\n"` | `sdks/python/tests/test_client.py` + `sdks/python/examples/basic_usage.py` | 🟡 IN_PROGRESS (SDK ready; E2E pending deploy) |
| 21 | JS SDK equivalent in Node.js | `sdks/js/tests/client.test.ts` | ⬜ PENDING (stub only — implementation needed) |
| 22 | Go SDK equivalent | `sdks/go/opensandbox/client_test.go` | ⬜ PENDING (stub only — implementation needed) |
| 23 | All 3 SDKs use platform identity SDK (azure-identity / @azure/identity / azidentity); no hardcoded credentials | Code review: see SDK READMEs | 🟢 PASS (scaffold-level) |

## G. Observability portal

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 24 | Portal at `https://<aca-fqdn>/` loads behind ACA Easy Auth (Entra SSO) | Manual browser test + `tests/e2e/` smoke | ⬜ PENDING |
| 25 | Portal lists active sessions for the logged-in user | Manual + portal-frontend test (TBD) | ⬜ PENDING |
| 26 | Portal shows audit-log entries for user's sessions (last 24 h, filter by session_id) | Manual + portal-api test (TBD) | ⬜ PENDING |
| 27 | Portal shows quota usage for user's namespace | Manual + portal-api test (TBD) | ⬜ PENDING |
| 28 | Portal does NOT expose write/destroy in v1 (REST API only) | Code review + portal-frontend route audit | ⬜ PENDING |

## H. Documentation & runbooks

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 29 | `README.md` covers architecture diagram, quickstart, IaC params, SDK examples | Existing `README.md` + `docs/ARCHITECTURE.md` | 🟢 PASS |
| 30 | `runbooks/incident-response.md` covers revoke / quarantine / rotate Notation cert | `runbooks/incident-response.md` | 🟢 PASS |
| 31 | `docs/threat-model.md` documents agent-as-tenant trust model, Kata's role, known gaps | `docs/threat-model.md` (TODO — not yet written, scaffold has ARCHITECTURE.md only) | ⬜ PENDING |

## I. CI/CD

| AC | Statement | Verifier | Status |
|---|---|---|---|
| 32 | PR triggers: Bicep what-if + ARM-TTK + Helm lint + 3 SDK unit tests | `.github/workflows/pr.yml` | 🟢 PASS (workflow scaffold) |
| 33 | Main-branch merge: image build → Notation sign → push → AKS Helm rollout → ACA revision update | `.github/workflows/main.yml` | 🟢 PASS (workflow scaffold) |
| 34 | Image build pipeline rejects images with high/critical CVEs from Defender | `.github/workflows/main.yml` `image-build-sign-push` job | 🟢 PASS (workflow scaffold) |

---

## NEW ACs from consensus plan (not in original spec — added by Critic B-C5 / B-C6 / S-C9)

| AC | Statement | Verifier | Status |
|---|---|---|---|
| **N1** | All 3 container-escape PoCs (cgroup release_agent, /proc/self/exe overwrite, kmod load) fail against a Kata pod | `tests/security/test_container_escape.py` | ⬜ PENDING |
| **N2** | Quarterly DR drill: KV signing cert restored from backup; canary signed image schedules within RTO=4 h | `tests/observability/` (DR drill script) + `runbooks/dr-drill.md` | ⬜ PENDING |
| **N3** | Single trace_id propagates SDK → ACA → AKS → execd; parent-child links correct | `tests/e2e/test_distributed_trace.py` | ⬜ PENDING |
| **N4** | Workload Identity propagation probe completes p95 < 60 s over 25 parallel provisions | `tests/integration/test_propagation_probe.py` | ⬜ PENDING |

---

## Summary

| Bucket | PASS | IN_PROGRESS | PENDING | DEFERRED |
|---|---:|---:|---:|---:|
| Scaffolding (docs, runbooks, workflows, SDK code) | 7 | 1 | 0 | 0 |
| Deployment-required (need Azure sub) | 0 | 0 | 28 | 0 |
| v1.5 features | 0 | 0 | 0 | 2 (JS/Go SDK fill-in) |

**Next step:** Run Phase 0 spikes against a real Azure dev subscription. Update this checklist as each AC moves through states. The 7 PASS items are scaffold-only — they exist as code/docs in the repo and pass static checks. The 28 PENDING items require a deployed environment to verify.
