# Documentation Refresh Log — 2026-05-20

Single-page changelog for the documentation refresh pass that aligns the top-level docs with
the deployed reality of `rg-opensandbox-dev` and `rg-opensandbox-demo` as of 2026-05-20.

## Scope

Rewrite or create the operator- and developer-facing docs so that every claim is reproducible
against the live cluster. Out of scope (owned by other workstreams or refreshed separately):

- `evidence/runs/finish/*.md` — runbooks owned by FINISH-4/5/6/7/8 streams.
- Anything under `third_party/opensandbox/` — vendored upstream tree.
- `infra/bicep/*` — owned by the infra workstream.
- `docs/acceptance-checklist.md` (a.k.a. AC-CHECKLIST.md) — being refreshed separately.

## Files changed

| File | Change | Why |
|---|---|---|
| `README.md` | Full rewrite | Old README described the v1 scaffold ("Phase 0 spike required before infra deploy") and pre-deployment shape. The cluster is live, both demos pass, and the README needed to lead with what's actually running, the reproducible quickstart, and an honest resource inventory. |
| `docs/ARCHITECTURE.md` | Full rewrite | Old doc described the hybrid ACA + AKS scaffold as a forward-looking design. Now reflects the deployed eleven-component topology, includes a full ASCII component map, VNet/subnet table, Workload Identity flow diagram, sandbox egress data path through Cilium → UDR → Firewall, image supply chain through the ACR private endpoint, a failure-modes table covering the issues we actually hit, and a write-up of the CRLF `bootstrap.sh` root cause as a teaching story. |
| `docs/OPERATIONS.md` | New file | There was no consolidated ops doc. This is the runbook index (one row per FINISH slice), a 60-second cluster-health checklist, plus three walkthroughs requested in the brief: add a sandbox image, rotate `OPENSANDBOX_SERVER_API_KEY`, rebuild and roll out `execd`. |
| `docs/index.md` | New file | Top-of-`docs/` entry point that points at README, ARCHITECTURE, OPERATIONS, ROADMAP, the acceptance checklist, and evidence. |
| `ROADMAP.md` | New file | The repo had no roadmap. This one is short and operator-facing: what's done (with evidence links per slice), what's in progress (FINISH-7 + Fluent Bit DS), what's deferred (Notation/Ratify, App Gateway WAF, multi-region, etc.), and what's next in priority order. |
| `docs/DOC-REFRESH-LOG.md` | New file (this one) | Required by the refresh brief. |
| `docs/DEVELOPER-EXPERIENCE.md` | New file | Developer-facing DX guide: 30-second mental model, reproducible quickstart against `evidence/runs/finish/sdk_e2e.py`, lifecycle, commands, image catalog, agentic patterns (Kimi via OSB), performance/limits, network/isolation, secrets, observability, prod checklist, troubleshooting matrix, and a practical SDK API reference. |

## Files deliberately left alone

- `docs/acceptance-checklist.md` — being refreshed by a separate stream.
- `docs/mission-and-architecture.md` — narrative original design; preserved for history. The new
  `docs/ARCHITECTURE.md` supersedes it operationally.
- `evidence/runs/finish/*` — runbooks and recorded artefacts owned by FINISH-4/5/6/7/8.
- `third_party/opensandbox/**` — vendored upstream.
- `infra/bicep/**` — infra workstream owns this.
- `runbooks/*.md` — pre-existing generic runbooks (IR, onboarding, CVE, DR drill); the new
  `docs/OPERATIONS.md` references them rather than rewriting them.

## Verification notes

Every command embedded in the new docs is either:

- Reproducible against the live cluster today (e.g. `kubectl get runtimeclass kata-vm-isolation`,
  `az network firewall show`).
- A standard Azure CLI / `kubectl` / `helm` invocation that the team has already used during
  FINISH-4 through FINISH-8.
- Footnoted as "current state, may drift" where it depends on values that operators might
  legitimately have changed since 2026-05-20 (e.g. autoscaler min/max).

ASCII diagrams were authored in monospace and rely only on `+ - | v ^` characters so they render
consistently on GitHub web, VS Code preview, and `cat` in a terminal.

## Open follow-ups

- Once FINISH-7 lands, the ACA section of `docs/ARCHITECTURE.md` should be promoted from "wiring
  in progress" to a full component callout (control plane app, portal API, portal frontend).
- When Notation+Ratify lands (FINISH-9), add a new section to `docs/ARCHITECTURE.md#image-supply-chain`
  covering signing keys, TrustPolicy, and admission denial behaviour.
- The 60-second health checklist in `docs/OPERATIONS.md` should be automated as a single
  `scripts/healthcheck.sh` and referenced from the README quickstart.
