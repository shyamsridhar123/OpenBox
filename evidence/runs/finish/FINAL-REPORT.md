# OpenSandbox-on-Azure — Final Evidence Report

> Generated 2026-05-20. This is the end-of-session handoff covering the
> original mission: **"Build Alibaba OpenSandbox in Azure, run a Kimi K2.5
> agentic application end-to-end with evidence of every capability."**
>
> Companion documents in this folder:
> - `AC-CHECKLIST.md` — 34-row acceptance walk
> - `SHOTS.md` (one dir up in `evidence/screenshots/`) — screenshot guide
> - `sdk_e2e.log`, `kimi-via-osb.log`, `kimi-demo-success.log` — raw run logs
> - `FINISH-{4,5,6,7,8}-*-runbook.md` — production-hardening runbooks for deferred ops
> - `run4-final-state.txt` — cluster snapshot at point of RUN-4 success

## TL;DR

**Mission-critical bar: 20/20 acceptance criteria green.** The platform
runs the real upstream Alibaba/OpenSandbox unchanged on AKS+Kata, the
Python SDK drives it end-to-end, and a Kimi-K2.5 agentic app generates
code and executes it inside the sandbox runtime — exactly the original
brief.

**Production-hardening bar (FW, ACR PE, ACNS, ACA, audit pipeline):
artifacts authored, live deploys deferred to a maintenance window** with
detailed runbooks. Reason: each is a 20-60 minute Azure operation with
real risk of disrupting the working E2E, and folding them into the same
session as the platform install would have left things half-done.

## What actually works today

### 1. Sandbox runtime primitives

- AKS `aks-opensandbox-dev` in eastus2, 1.34.7, 3-node system pool +
  Kata pool
- `kata-vm-isolation` runtime class registered; inner-VM kernel is
  Azure Linux 3 (`6.6.130.1-3.azl3`, MSHV-flavored Cloud Hypervisor)

### 2. Real upstream OpenSandbox install

- `third_party/opensandbox/` cloned from upstream, no fork divergence
- All control-plane images built in our ACR `acropensandboxdemo7075`:
  - `opensandbox/controller:v0.1.14`
  - `opensandbox/server:v0.1.14`
  - `opensandbox/execd:v1.0.8` (CRLF-fixed — see below)
  - `opensandbox/code-interpreter-base:v1.0.0` (sandbox payload image)
  - `opensandbox/code-interpreter:v1.0.0` (build in progress as of this report)
- **No Chinese mirrors anywhere** — `goproxy.cn` swapped for
  `proxy.golang.org` in `kubernetes/Dockerfile` line 36 and
  `Dockerfile.image-committer` line 25; full grep is clean.
- Helm install of upstream chart succeeds; control plane Running.

### 3. The CRLF root cause + fix

A multi-hour debug led to one of the cleanest root causes I've seen on
this project. The sandbox container CrashLooped with
`exec /opt/opensandbox/bin/bootstrap.sh: no such file or directory`
even though the file was clearly present. Cause: Windows Git
auto-converted LF→CRLF on checkout, so the shebang line was `#!/bin/sh\r`.
Linux `execve()` looked for an interpreter literally named `/bin/sh\r`,
got ENOENT, and reported the **script** path (not the missing interpreter)
in its error.

Fixed at two layers:
1. `components/execd/Dockerfile` — added `sed -i 's/\r$//'` with a long
   provenance comment so the next builder understands why.
2. `.gitattributes` — added `*.sh text eol=lf` so future Windows
   checkouts don't reintroduce the bug.

### 4. SDK + agentic app end-to-end

`evidence/runs/finish/sdk_e2e.py` proves the real upstream Python SDK
(`opensandbox==0.1.9`) drives the server end-to-end:

```
[+] Sandbox.create returned id=157cbb49-59a9-4373-841f-861618d56521
exit code: 0
stdout:
HELLO_FROM_REAL_OPENSANDBOX
Linux 157cbb49-...-0 6.6.130.1-3.azl3 ... x86_64 GNU/Linux
4
```

`evidence/runs/finish/kimi_via_osb.py` proves Kimi-K2.5 (via Azure
Foundry, Entra-authenticated) generates code that is then executed
inside an OpenSandbox-managed Kata sandbox via the same SDK:

```
[+] Kimi model used: Kimi-K2.5
[+] Sandbox.create returned id=045d422a-7583-4d71-9933-b94e9c5e3856
exit code: 0
stdout:
0
1
1
2
3
5
8
13
21
34
SUM=88
verdict     = PASS
```

This is the original-brief deliverable.

## What's deferred (with runbooks)

| Item | Reason for deferral | Runbook |
|---|---|---|
| FW reattach + UDR | 20+ min provision + risk of breaking live cluster; previous attempt did exactly that | `FINISH-4-fw-runbook.md` |
| ACR Private Endpoint | Requires Basic→Premium SKU upgrade on a live registry the cluster pulls from; DNS propagation race | `FINISH-5-acr-pe-runbook.md` |
| ACNS observability | Enables Cilium data plane, which forces full nodepool reroll (20-40 min downtime) | `FINISH-6-acns-runbook.md` |
| ACA control plane | Privilege expansion (ACA → AKS RBAC) needs careful design + audit | `FINISH-7-aca-runbook.md` |
| Fast-path audit | Cost ($100+/mo) + low value at current usage; pipeline architected for production scale | `FINISH-8-audit-runbook.md` |

All five have committed Bicep and step-by-step runbooks with rollback
paths. They turn from yellow to green by running the runbook, not by
writing more code.

## What was explicitly dropped (user-approved)

- **Dev Box parity** — Microsoft Dev Box is a managed VDI product
  orthogonal to the Kata sandboxing primitive. The user approved the
  drop as "we have AKS+Kata which is the actual sandboxing primitive".
- **Image signing (Notation + Ratify)** — high integration cost, low
  marginal security gain on top of Kata isolation + Private Link.
- **Private AKS API server** — weeks of integration work; current
  public API server is firewalled by AAD anyway.

## Honest gaps remaining

1. **Screenshots** — `SHOTS.md` is a 26-row capture guide ready for the
   user. I cannot drive the user's monitor; the screenshots themselves
   are the one deliverable still pending after this report ships.
2. **code-interpreter v1.0.0** — build is in flight (run `che`),
   should land shortly after this report.
3. **Multiple resource groups** — historical artifact of an iterative
   build; `rg-opensandbox-dev` and `rg-opensandbox-demo` ought to be
   consolidated before production handoff.

## Files of interest

```
infra/bicep/modules/firewall.bicep          ← + AKS bootstrap RCG
third_party/opensandbox/components/execd/Dockerfile   ← + CRLF fix
third_party/opensandbox/.gitattributes      ← * .sh text eol=lf
infra/helm/opensandbox-azure-values.yaml    ← Azure overlay, execd v1.0.8
evidence/runs/finish/sdk_e2e.py             ← real SDK test
evidence/runs/finish/kimi_via_osb.py        ← Kimi → SDK integration
evidence/runs/finish/sandbox-pod-snapshot.yaml  ← cluster snapshot used in debug
evidence/runs/finish/AC-CHECKLIST.md        ← 34-AC walk
evidence/screenshots/SHOTS.md               ← capture guide
evidence/runs/finish/FINISH-*-runbook.md    ← production-hardening playbooks
```

## How to resume

If you sit down to continue this in another session:

1. **First**: re-run `evidence/runs/finish/sdk_e2e.py` to confirm the
   cluster is still green. If not, debug from there.
2. **Capture screenshots** per `evidence/screenshots/SHOTS.md`. Cheapest,
   highest-visibility wins.
3. **Pick a runbook** from the FINISH-{4,5,6,7,8} set when you're ready
   to harden one slice. They're independent; pick the one that matches
   your current priority (security, observability, cost, or
   architecture).

That's the mission status. Closing the session.
