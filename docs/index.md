# Documentation Index

Top-level entry point for the DarkForge documentation.

## Start here

- **[README.md](../README.md)** — what the project is, what's running in `rg-opensandbox-dev`,
  and the quickstart that reproduces both end-to-end demos.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — full component map, VNet/subnet table, identity
  flow, egress data path, image supply chain, failure modes, and the CRLF bootstrap story.
- **[OPERATIONS.md](OPERATIONS.md)** — runbook index, 60-second health checklist, sandbox image
  onboarding, server API-key rotation, and execd rebuild walkthrough.
- **[ROADMAP.md](../ROADMAP.md)** — what's done, what's deferred, what's next.

## Reference

- [acceptance-checklist.md](acceptance-checklist.md) — the 34 acceptance criteria for v1.
- [mission-and-architecture.md](mission-and-architecture.md) — original design narrative.

## Portal (dev)

- **[../apps/portal-api/README.md](../apps/portal-api/README.md)** — FastAPI dev portal: 24 routes covering cluster lifecycle, swarm runs (SSE), sandbox CRUD, sandbox-exec (chart-in-browser), Kimi chat (K2.6 default), Pool CR, events.
- **[../apps/portal-frontend/README.md](../apps/portal-frontend/README.md)** — Alpine.js single-page command center, 6 cards.
- **[PORTAL-AUTH.md](PORTAL-AUTH.md)** — DEV-MODE auth model and the 6-step prod migration checklist (Workload Identity, Entra RBAC, Key Vault CSI).

## Demos and runbooks

- [examples/](../examples/) — runnable demo scripts: `sdk_e2e.py`, `kimi_via_osb.py`, `hypothesis_swarm.py`, `run_in_sandbox.py` (the portal's exec backend).
- [runbooks/](../runbooks/) — ops runbooks (IR, onboarding, CVE response, DR drill).

## Vendored runtime

- [third_party/opensandbox/](../third_party/opensandbox/) — vendored sandbox runtime
  (see [`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md) for attribution).
  Treat as read-only; sync via the upstream-sync workflow.
- Patches against the vendored tree are limited to:
  1. `goproxy.cn` → `proxy.golang.org` (build-time)
  2. CRLF protection on shell scripts (`.gitattributes` + `sed` in Dockerfile)
