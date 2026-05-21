# Roadmap

Snapshot of what is complete, what is deferred, and what is queued next for OpenBox on Azure.
Updated 2026-05-20.

## Done

| Slice | Description | Evidence |
|---|---|---|
| Phase 0 | Kata Pod Sandboxing on AKS validated; Cilium L7 on Kata pods works | [evidence/runs/finish/kata-smoke-evidence.txt](evidence/runs/finish/kata-smoke-evidence.txt), [kata-runtimeclass.yaml](evidence/runs/finish/kata-runtimeclass.yaml) |
| Phase 1 | AKS cluster `aks-opensandbox-dev` (1.34.7) + Kata node pool + Azure CNI Overlay + Cilium | [cluster-state.json](evidence/runs/finish/cluster-state.json), [kata-nodes.txt](evidence/runs/finish/kata-nodes.txt) |
| Phase 2 | Vendored sandbox runtime (controller, server, execd v1.0.8 with CRLF fix) deployed via Helm | `infra/helm/opensandbox/`, `third_party/opensandbox/` |
| Phase 3 | End-to-end laptop SDK demo: `Sandbox.create` → Kata pod → command exec → result | [sdk_e2e.py](evidence/runs/finish/sdk_e2e.py) / [sdk_e2e.log](evidence/runs/finish/sdk_e2e.log) — RUN-4 SUCCESS |
| Phase 3 | Kimi K2.5 agentic demo through the sandbox SDK | [kimi_via_osb.py](evidence/runs/finish/kimi_via_osb.py) / [kimi-via-osb.log](evidence/runs/finish/kimi-via-osb.log) — PASS, SUM=88 |
| Phase 3 | In-cluster Workload Identity variant of the Kimi demo | [kimi-demo.yaml](evidence/runs/finish/kimi-demo.yaml) / [kimi-demo-success.log](evidence/runs/finish/kimi-demo-success.log), [wi-federated-credential.json](evidence/runs/finish/wi-federated-credential.json) |
| FINISH-4 | Azure Firewall Premium + policy + UDR for Kata egress | [FINISH-4-fw-runbook.md](evidence/runs/finish/FINISH-4-fw-runbook.md), [fw-failure-trace.md](evidence/runs/finish/fw-failure-trace.md) |
| FINISH-5 | ACR Premium private endpoint, public access disabled, DNS linked | [FINISH-5-acr-pe-runbook.md](evidence/runs/finish/FINISH-5-acr-pe-runbook.md) |
| FINISH-6 | Cilium ACNS, Hubble UI, L7 FQDN policies on Kata pods | [FINISH-6-acns-runbook.md](evidence/runs/finish/FINISH-6-acns-runbook.md) |
| FINISH-8 | Event Hubs (LocalAuthDisabled) + Stream Analytics audit pipeline to blob | [FINISH-8-audit-runbook.md](evidence/runs/finish/FINISH-8-audit-runbook.md) |

## In progress

| Slice | Description | Status |
|---|---|---|
| FINISH-7 | ACA environment + control-plane container apps (FastAPI control plane, portal API, portal frontend) | Partial — `acaenv-opensandbox-dev` and three apps deployed in `snet-aca`; wiring to AKS via OBO/private ingress under active development. See [FINISH-7-aca-runbook.md](evidence/runs/finish/FINISH-7-aca-runbook.md). |
| Audit DS | Fluent Bit DaemonSet on AKS shipping `execd` logs to Event Hubs | Being deployed by a parallel workstream; pipeline already validated end-to-end with a synthetic producer. |

## Deferred

- **Notation / Ratify image signing.** Plan called for dual-cert TrustPolicy and admission
  enforcement; we have ACR Premium and Key Vault in place, but Ratify+Gatekeeper are not yet
  deployed. Tracking under a future FINISH-9.
- **Application Gateway WAF in front of ACA.** App Gateway subnet (`snet-appgw`) exists but no
  gateway resource is provisioned.
- **Multi-region.** Architecture supports it; v1 deploys to `eastus2` only.
- **Hibernation / CRIU for warm sandbox pools.** Out of scope for v1.
- **Defender for Containers Kata-pod assessment.** Known platform gap; compensating KQL alerts
  not yet authored.
- **TTL idle reaper for stale sandboxes.** Manual cleanup until v1.5.
- **Read-write portal.** Portal frontend is read-only by design through v1.
- **External-customer multi-tenancy.** v1 is internal-tenant only.
- **Windows sandboxes, GUI/RDP, multi-cloud.** Out of charter.

## Next

In rough priority order:

1. Land FINISH-7 (ACA control plane wired to AKS via OBO, App Gateway in front).
2. FINISH-9: Notation signing + Ratify admission enforcement.
3. End-to-end trace test: SDK call → control plane → AKS → execd, single `trace_id` across all
   hops, asserted in CI.
4. CVE-response drill: pull a known-bad image, verify it is rejected.
5. DR drill: restore the cluster + ACR + Key Vault in a second resource group from infra code
   only.

Refer to [docs/acceptance-checklist.md](docs/acceptance-checklist.md) for the full 34-item
acceptance list; this roadmap is the operator-facing summary.
