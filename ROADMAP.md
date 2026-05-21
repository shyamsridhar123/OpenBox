# Roadmap

Snapshot of what is complete, what is deferred, and what is queued next for DarkForge.
Updated 2026-05-20.

## Done

| Slice | Description |
|---|---|
| Phase 0 | Kata Pod Sandboxing on AKS validated; Cilium L7 on Kata pods works |
| Phase 1 | AKS cluster `aks-opensandbox-dev` (1.34.7) + Kata node pool + Azure CNI Overlay + Cilium |
| Phase 2 | Sandbox runtime (controller, server, execd v1.0.8 with CRLF fix) deployed via Helm — chart at `infra/helm/opensandbox/`, source under `third_party/opensandbox/` |
| Phase 3 | End-to-end laptop SDK demo: `Sandbox.create` → Kata pod → command exec → result. Driver in `examples/sdk_e2e.py`. |
| Phase 3 | LLM-driven agentic demo through the sandbox SDK — Kimi K2.5 is the worked example; the path generalises to any LLM. Driver in `examples/kimi_via_osb.py`. |
| Phase 3 | In-cluster Workload Identity variant of the agentic demo. |
| Phase 3 | Hypothesis Swarm demo: Kimi → N diverse patches → N Kata sandboxes race in parallel → first green test wins. Driver in `examples/hypothesis_swarm.py`; presenter script in `docs/DEMO-HYPOTHESIS-SWARM.md`. Last measured N=20: 14.16× speedup vs serial on same substrate. |
| Firewall | Azure Firewall Premium + policy + UDR for Kata egress. |
| ACR PE | ACR Premium private endpoint, public access disabled, DNS linked. |
| ACNS | Cilium ACNS, Hubble UI, L7 FQDN policies on Kata pods. |
| Audit | Event Hubs (LocalAuthDisabled) + Stream Analytics audit pipeline to blob. |

## In progress

| Slice | Description | Status |
|---|---|---|
| ACA control plane | ACA environment + control-plane container apps (FastAPI control plane, portal API, portal frontend) | Partial — `acaenv-opensandbox-dev` and three apps deployed in `snet-aca`. `apps/control-plane/` has FastAPI source; `apps/portal-api/` and `apps/portal-frontend/` are empty scaffolds (the ACA revisions currently serve `mcr.microsoft.com/azuredocs/containerapps-helloworld:latest`). Wiring to AKS via OBO/private ingress under active development. |
| Portal console | Web UI for sandbox lifecycle operations (list/create/renew/delete/filter) with server-side RBAC. | Design only — tracks upstream OSEP-0006 (`third_party/opensandbox/oseps/0006-developer-console.md`, status `implementable`). Upstream has not implemented either; DarkForge would be the first deployment to ship it. No code in this repo or upstream. |
| Audit DS | Fluent Bit DaemonSet on AKS shipping `execd` logs to Event Hubs | Being deployed by a parallel workstream; pipeline already validated end-to-end with a synthetic producer. |

## Deferred

- **Notation / Ratify image signing.** Plan called for dual-cert TrustPolicy and admission
  enforcement; we have ACR Premium and Key Vault in place, but Ratify+Gatekeeper are not yet
  deployed.
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

1. Land the ACA control plane wired to AKS via OBO, App Gateway in front.
2. Notation signing + Ratify admission enforcement.
3. End-to-end trace test: SDK call → control plane → AKS → execd, single `trace_id` across all
   hops, asserted in CI.
4. CVE-response drill: pull a known-bad image, verify it is rejected.
5. DR drill: restore the cluster + ACR + Key Vault in a second resource group from infra code
   only.

Refer to [docs/acceptance-checklist.md](docs/acceptance-checklist.md) for the full 34-item
acceptance list; this roadmap is the operator-facing summary.
