# Documentation Index

Top-level entry point for the OpenBox-on-Azure documentation.

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
- [DOC-REFRESH-LOG.md](DOC-REFRESH-LOG.md) — changelog for the most recent doc refresh.

## Evidence and per-slice runbooks

- [evidence/runs/finish/](../evidence/runs/finish/) — recorded E2E runs (`sdk_e2e.log`,
  `kimi-via-osb.log`, `kimi-demo-success.log`) and per-FINISH-slice runbooks.
- [runbooks/](../runbooks/) — generic ops runbooks (IR, onboarding, CVE response, DR drill).

## Vendored runtime

- [third_party/opensandbox/](../third_party/opensandbox/) — vendored sandbox runtime
  (see [`../THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md) for attribution).
  Treat as read-only; sync via the upstream-sync workflow.
- Patches against the vendored tree are limited to:
  1. `goproxy.cn` → `proxy.golang.org` (build-time)
  2. CRLF protection on shell scripts (`.gitattributes` + `sed` in Dockerfile)
