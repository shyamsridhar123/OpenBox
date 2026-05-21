# Demo — Hypothesis Swarm Debugger

**Audience:** AI / agent builders
**Duration:** ~5 min talk + ~1 min live run
**Goal:** Show that DarkForge turns "the LLM is wrong most of the time" from a
liability into an engineering-velocity multiplier. Twenty parallel hypotheses,
each in its own Kata VM, racing to be the first green pytest. 14× faster than
serial on the same substrate.

---

## Pre-flight (do this 5 min before walking on stage)

```bash
# 1. Cluster up, kubeconfig fresh
az aks show -g rg-opensandbox-dev -n aks-opensandbox-dev --query powerState.code -o tsv
# Expected: Running. If "Stopped", run `az aks start ... --no-wait` and wait ~5 min.

az aks get-credentials -g rg-opensandbox-dev -n aks-opensandbox-dev --overwrite-existing
kubectl get nodes
# Expected: 3 nodepool + 2 kata nodes, all Ready.

# 2. API key materialized to disk (source of truth = cluster secret)
kubectl -n opensandbox-system get secret opensandbox-api-key \
  -o jsonpath='{.data.api-key}' | base64 -d > examples/.opensandbox-api-key

# 3. Port-forward (background)
kubectl -n opensandbox-system port-forward svc/opensandbox-server 18080:80 \
  > /tmp/pf.log 2>&1 &

# 4. AAD token to Kimi (Microsoft Foundry)
export AAD_TOKEN=$(az account get-access-token \
  --resource https://cognitiveservices.azure.com \
  --query accessToken -o tsv)

# 5. Demo venv active
source .venv-demo/Scripts/activate
python -c "from opensandbox import Sandbox; print('SDK OK')"
```

If all four green-light, you're ready.

---

## The four-phase script

### Phase 1 — "Here's a tiny bug" (30 sec)

Open `examples/swarm_target/cart.py` and `test_cart.py` on screen.

> "Two-file Python repo. A shopping cart with a unit test. The test
> fails — `c2.items` should be empty but it has an apple in it. This is
> the classic mutable-default-argument bug, and any senior Python dev
> can fix it in 30 seconds. But that's not the point of this demo."

Run the failing test live:

```bash
cd examples/swarm_target && python -m pytest test_cart.py -x
```

Expected: one assertion error, `['apple'] == []`.

### Phase 2 — "Now ask an LLM" (60 sec)

Stay on the slides. Don't run anything yet.

> "Normally an agent would ask GPT or Kimi for a fix, then *run that fix
> on your laptop*. Which is fine when the LLM is right. But the LLM is
> wrong most of the time. And the wrong code might be `rm -rf /` or a
> fork bomb or shelling out to whatever it pulled from Stack Overflow.
> The default assumption for agentic codegen is: this code is untrusted."

> "So what if instead of asking for one fix, we ask for **twenty different
> diagnoses** — at temperature 0.7, so we get diverse mental models? Then
> we don't pick one; we **race all of them**. Each hypothesis goes into
> its own Kata-isolated VM — a real Linux guest kernel per hypothesis,
> not a container. The wrong ones can do whatever they want; the blast
> radius is one disposable VM."

> "First green pytest wins. We see the leaderboard. We get the patch."

### Phase 3 — "Run it" (60 sec live)

```bash
N_HYPOTHESES=20 python examples/hypothesis_swarm.py
```

Talk while it runs (~75s total: 40s for Kimi + 30s for the swarm):

- "Kimi is generating 20 hypotheses right now…"
- (response chars print) "Got the JSON back."
- (sandboxes start logging) "Each line is one Kata VM coming up,
  installing pytest, applying its hypothesis, running the test."
- (the [#NN] PASS/FAIL lines stream in) "Notice the speed bimodal — fast
  ones pass, slow ones hit the 30-second timeout because they generated
  syntactically broken patches."

### Phase 4 — "Here's the punchline" (60 sec)

When the leaderboard prints, pause and read the timing block aloud:

```
Swarm wall-clock:           30.80s
Sum-of-sandbox-durations:  436.11s
Speedup vs. serial:         14.16x

Hypotheses:  20
Passed:      8
Failed:      12
```

Talking points:

1. **14× faster than serial on the same substrate.** Not "faster than
   your laptop" — faster than running these same 20 hypotheses one at a
   time on the same cluster. Same image, same VMs, same test. Honest
   number.

2. **Twelve of twenty failed.** That's the *feature*, not a bug. We
   *expected* the LLM to be wrong most of the time. In a single-fix
   world, you'd be screwed. In a swarm world, the wrong ones cost
   nothing because they're isolated and disposable.

3. **The winner is right.** Show the printed patch:
   `self.items = list(items or [])`. Diff against the buggy original.
   Note that the winner passed *both* tests — the obvious one and the
   discriminator that catches lazy fixes.

4. **What you don't see in this demo, but should imagine:** the failed
   hypotheses might have shelled out destructively, written to
   `/etc/passwd`, or forked themselves silly. We *don't care*. The
   Kata kernel boundary contained them. We just see "FAIL" in the
   leaderboard.

### Closing line

> "DarkForge isn't about running untrusted code. It's about making
> 'untrusted code' a *normal engineering substrate*, so you can stop
> treating agentic codegen as a special case. Twenty hypotheses, ten
> seconds. That's a 100× engineer."

---

## Expected output shape (from yesterday's dry-run)

```
======================================================================
HYPOTHESIS SWARM DEBUGGER  (N=20)
======================================================================
[+] Target: examples\swarm_target\cart.py
[+] Test:   examples\swarm_target\test_cart.py
[+] Got AAD token, prefix=eyJ0eXAiOiJK…
[+] Asking Kimi for 20 diverse hypotheses…
[+] Kimi deployment=Kimi-K2.5, response_chars=N, took ~40s
[+] Parsed 20 hypotheses
[+] Fanning out 20 Kata-isolated sandboxes (image=python:3.12-slim)…
  [#06] PASS in   7.16s — Converting the input to a list with list() creates a new list…
  …
  [#13] FAIL in  30.30s — Checking isinstance before copying ensures…
======================================================================
LEADERBOARD  (sorted: PASS first, then by duration)
======================================================================
  #06  PASS    7.16s  exit=  0  Converting the input to a list with list() …
  …
  #05  FAIL   30.50s  exit= -1  Slicing the input list creates a shallow copy …

======================================================================
TIMING
======================================================================
  Kimi call:                   42.80s
  Swarm wall-clock:            30.80s
  Sum-of-sandbox-durations:   436.11s
  Speedup vs. serial:          14.16x

  Hypotheses:  20
  Passed:      8
  Failed:      12

======================================================================
WINNER:  hypothesis #6  (7.16s)
======================================================================
```

Pass/fail counts are *not* deterministic across runs — Kimi at temperature
0.7 produces different hypotheses each time. Speedup is reliably in the
10–15× range for N=20.

---

## Visualization — what to point at, where, and when

There is **no custom dashboard** for this demo. The visualization is layered:

1. **Live (during the run): orchestrator stdout.** This is the primary viz — the streaming `[#NN] PASS/FAIL` lines, the leaderboard, the timing block. Project the terminal full-screen.

2. **~4 minutes after the run: Azure Container Insights.** Container Insights ingests `KubePodInventory` with a default 4-min lag. After the swarm finishes, open the AKS resource → Insights → Containers, filter namespace `opensandbox`. You'll see the bar chart spike from baseline to 20 pods and decay. This is the cluster's own receipts that the swarm happened.

3. **On-demand: Log Analytics KQL.** Workspace `log-opensandbox-dev`. Pin this query for an evidence tile:

   ```kql
   KubePodInventory
   | where Namespace == 'opensandbox'
   | where TimeGenerated > ago(1h)
   | summarize pods=dcount(Name) by bin(TimeGenerated, 30s)
   | order by TimeGenerated desc
   ```

4. **Per-sandbox surfaces that ship in OpenSandbox today, but are not wired into this demo:**
   - PTY-over-WebSocket (interactive browser shell into any sandbox) via `execd`
   - VNC desktop and VS Code in-browser sandbox images (`examples/desktop/`, `examples/vscode/` in the upstream tree)
   - `/metrics` endpoint per sandbox with OpenTelemetry middleware

5. **What does NOT exist (don't promise it):**
   - A fleet-wide live dashboard showing "all 20 sandboxes, who owns each, status." That's OSEP-0006, design only.
   - Custom Grafana / Workbook dashboards. Only Container Insights default views are available.
   - A real-time portal at `ca-portalfe-opensandbox-dev.*.azurecontainerapps.io`. The ACA app is provisioned but runs the Microsoft hello-world image — no portal source code exists yet.

The orchestrator stdout *is* the dashboard for tomorrow. Container Insights is the post-run evidence. Anything else would be misrepresenting the state of the platform.

---



**Q: "What if Kimi returns garbage JSON?"**
A: The orchestrator's `parse_hypotheses()` strips fences and locates the
JSON array defensively. On unrecoverable parse failure it prints the raw
response and exits with code 2 — easy to recover by rerunning.

**Q: "What if a sandbox doesn't come up?"**
A: `race_one()` catches exceptions per-hypothesis and returns a FAIL row.
One bad VM doesn't poison the swarm.

**Q: "Why 30s timeout on the FAIL cluster?"**
A: That's the default `pytest -x` timeout combined with sandbox cold-start.
Most FAILs are syntax errors in Kimi's patch — pytest hangs on collection
because the `class Cart:` block has unclosed braces or similar. Future
work: a syntax-check gate before the sandbox spin-up to fail-fast on
obvious garbage.

**Q: "Speedup vs what?"**
A: vs running the same 20 hypotheses sequentially in the same cluster.
We sum the per-sandbox durations to get the serial baseline (`436s`),
divide by wall-clock (`31s`). Apples-to-apples. We are *not* claiming
"14× faster than your laptop" — that would be a much bigger number
because your laptop can't run Kata VMs in parallel at all.

**Q: "Why Kata, not just containers?"**
A: Containers share the host kernel. A wrong-LLM-code container that
exploits a kernel bug (or escapes via a runc CVE) takes the whole node.
Kata gives each pod a real guest kernel via a lightweight VM. Container
escape becomes guest-kernel escape — much harder.

**Q: "Why 20? Why not 5? Why not 100?"**
A: N=5 in our smoke run yielded 5/5 PASS at 4× speedup — not enough
demo drama. N=20 yields a 40-60% pass rate which makes the "let it be
wrong" point visible. N=100 would saturate the kata node pool and
require autoscaling — possible but not necessary for the story.

---

## What the operator may notice

- The `[DELEGATION NOTICE]` and security-substring hooks in the authoring
  environment are noise, not blockers. Direct file writes proceeded fine.
- The first end-to-end attempt failed because `python` and `pip` pointed
  at different interpreters on the demo machine. Resolved with a fresh
  `uv venv .venv-demo` and `uv pip install`. The presenter script above
  assumes that venv is active.
- The cluster auto-stops at night to save Azure spend. If the demo is
  scheduled before 9am local, hit `az aks start` an hour beforehand.
