# Demo — Tomorrow

**Audience:** AI / agent builders
**Total time:** 8–10 min
**The pitch:** OpenSandbox-on-Azure (DarkForge) is a real API-first platform for AI agents to safely execute code at scale. Here's the API, here's the SDK, here's a 20-way parallel agentic pattern running on it.

**The visualization:** the **Swagger UI that ships with OpenSandbox itself** — at `http://localhost:18080/docs` once the port-forward is up. Not a custom dashboard, not slides, an interactive API explorer hitting the same endpoints the SDK uses.

---

## Pre-flight checklist (do at T-15 minutes)

```bash
# 1. Cluster up
az aks show -g rg-opensandbox-dev -n aks-opensandbox-dev --query powerState.code -o tsv
# If 'Stopped': az aks start -g rg-opensandbox-dev -n aks-opensandbox-dev --no-wait
#   then wait ~5 min. Re-check with `kubectl get nodes`.

az aks get-credentials -g rg-opensandbox-dev -n aks-opensandbox-dev --overwrite-existing
kubectl get nodes
# Expected: 3 system + 2 kata nodes Ready.

# 2. API key materialized
kubectl -n opensandbox-system get secret opensandbox-api-key \
  -o jsonpath='{.data.api-key}' | base64 -d > examples/.opensandbox-api-key
export OSB_KEY=$(cat examples/.opensandbox-api-key)
echo "${OSB_KEY:0:10}..."  # sanity check

# 3. Port-forward (background, leave running)
kubectl -n opensandbox-system port-forward svc/opensandbox-server 18080:80 \
  > /tmp/pf.log 2>&1 &

# 4. Reach the shipped UI
curl -s -o /dev/null -w "docs=%{http_code} redoc=%{http_code}\n" \
  http://localhost:18080/docs http://localhost:18080/redoc
# Expected: docs=200 redoc=200

# 5. AAD token for Kimi
export AAD_TOKEN=$(az account get-access-token \
  --resource https://cognitiveservices.azure.com \
  --query accessToken -o tsv)

# 6. Demo venv
source .venv-demo/Scripts/activate
python -c "from opensandbox import Sandbox; print('SDK OK')"
```

If all six pass — you're ready.

---

## Browser tabs to have open before you start

| Tab | URL | What you'll do with it |
|---|---|---|
| **1. Swagger UI** | `http://localhost:18080/docs` | The hero. Show the API surface, authenticate, fire a real request. |
| **2. ReDoc** | `http://localhost:18080/redoc` | Backup view; cleaner read-only reference. Open if someone asks "where's the full spec." |
| **3. AKS Container Insights** | (Azure Portal → AKS `aks-opensandbox-dev` → Insights → Containers, filter namespace `opensandbox`) | Cluster's own evidence of the swarm. ~4 min after the run completes. |
| **4. GitHub README** | `https://github.com/shyamsridhar123/DarkForge` | Closing slide. Logo, badges, the 100× thesis. |

Terminal layout:
- Left half: a clean terminal in `~/code/openbox` with `.venv-demo` activated.
- Right half: a second terminal running `kubectl get pods -n opensandbox -w` (you'll start this during Act 2).

---

## The script

### Act 1 — "Here is the API" (90 seconds)

**Switch to Tab 1 (Swagger UI).**

> "This is OpenSandbox. It's a CNCF Landscape project, Apache-2.0, that gives AI agents a sandbox API. What you're looking at right now is the actual server running on our AKS cluster in Azure — not a hosted demo, not slides. The Swagger UI ships with the server; we didn't build it."

**Scroll through the endpoint groups.** Point at:
- `POST /sandboxes` — "create a sandbox"
- `GET /sandboxes/{id}` — "inspect one"
- `POST /sandboxes/{id}/exec` — "run a command inside"
- `DELETE /sandboxes/{id}` — "destroy"

> "That's the contract. Any agent in any language can hit this. There are SDKs in Python, Java, JavaScript, .NET, Go — same calls, idiomatic for each language."

**Click "Authorize" in the top right.** Paste `$OSB_KEY` into the `OPEN-SANDBOX-API-KEY` field.

> "Auth is by API key for service principals, JWT for users. You can't reach any lifecycle endpoint without it — let me show you."

**Open a `GET /sandboxes` request.** Click "Try it out" → "Execute". You should see a 200 response with whatever sandboxes exist right now.

> "That just hit a real Kubernetes cluster, ran through a FastAPI server, listed live sandbox custom resources. The whole loop is real."

*(If the list is empty, that's also fine — say "no sandboxes right now, we're about to fix that.")*

---

### Act 2 — "Watch an agent use it" (3 minutes)

**Switch to the left terminal.**

> "Now I'm going to be an AI agent. Kimi K2.5 — running in Microsoft Foundry — is going to debug a failing test. Specifically: 20 hypotheses in parallel. Each gets its own Kata-isolated VM. First green test wins."

**Start the right-side pod watch:**

```bash
kubectl get pods -n opensandbox -w
```

**Run the demo from the left terminal:**

```bash
N_HYPOTHESES=20 python examples/hypothesis_swarm.py
```

**While Kimi thinks (~30-40s), narrate:**

> "Kimi just got the buggy source plus the failing test. We asked it for 20 different diagnoses at temperature 0.7 — so each one is a genuinely different mental model of the bug. Some will be right, some will be wrong, that's fine. We're about to race them all."

**When the swarm starts spawning pods (visible in the right terminal):**

> "Look right — every line that just appeared is one Kata VM coming up. Twenty real Linux guest kernels, each running pytest with a different patch. Not containers — VMs. A wrong hypothesis can do whatever it wants to its own kernel; the blast radius is one disposable VM."

**As `[#NN] PASS/FAIL` lines stream in on the left:**

> "The fast ones — about 9 to 10 seconds — are passing. The slow ones at the 30-second mark are syntax errors in Kimi's patch; pytest hung on collection. That's the bimodal distribution. We expected the LLM to be wrong most of the time. That's the entire point."

**When the leaderboard prints — pause and read the timing block:**

```
Swarm wall-clock:           30.80s
Sum-of-sandbox-durations:  436.11s
Speedup vs. serial:         14.16x
Hypotheses: 20   Passed: 8   Failed: 12
```

> "Fourteen times faster than running these same hypotheses one at a time on the same cluster — same image, same VMs, same test. Apples-to-apples. Twelve of twenty failed. The platform handled it. The wrong ones cost nothing."

**Scroll up to the winner block. Read the patched code aloud:**

```python
self.items = list(items or [])
```

> "That's the fix. Mutable-default-argument bug, classic Python. Notice it wasn't 'the first hypothesis' that won — it was hypothesis #6. The swarm doesn't care which idea is right; it cares that *some* idea is right and we find it fast."

---

### Act 3 — "And here are the receipts" (2 minutes)

**Switch back to Tab 1 (Swagger UI).** Hit `GET /sandboxes` again with "Try it out."

> "Same call as before. Now it returns the residual state — sandboxes that were spawned, did their job, and most are gone or being cleaned up. The API is the system of record."

**Switch to Tab 3 (Container Insights, namespace = `opensandbox`).**

> "And here's Azure's own view — the cluster's container insights. You can see the bar chart spike from baseline to 20 pods around the timestamp of the run, then decay back. The cluster recorded it. The whole observability story is plumbed in: Log Analytics, App Insights, Event Hubs audit pipeline, all wired."

*(Container Insights has a ~4 minute ingestion delay. If the spike isn't visible yet, say: "ingestion lag — give it a couple minutes and you'll see it." Don't pretend it's there if it isn't.)*

---

### Closing — "Why this matters" (60 seconds)

**Switch to Tab 4 (GitHub README).** Logo + badges on screen.

> "Two things to take away. First: the API you saw in Swagger is what you'd `pip install opensandbox` and start calling tomorrow. It's open source, CNCF Landscape, Apache-2.0. Second: the security and identity story under it — Kata VM isolation, Entra ID auth, Azure Firewall egress, signed images at admission, audit trail to Event Hubs — is what makes it safe to actually deploy this in an enterprise. Most agent frameworks hand-wave 'we'll figure out execution.' We didn't. We picked the strongest open-source primitive and put a real Azure landing zone underneath it."

> "Twenty hypotheses, thirty seconds, fourteen-x speedup, real Kata VMs, real API. That's DarkForge."

**Stop.** Take questions.

---

## Q&A prep

**Q: Why didn't you build your own UI?**
A: OpenSandbox ships a complete OpenAPI 3.1 spec and FastAPI's auto-generated Swagger UI. For an API-first product, that *is* the UI — and it stays in sync with the server automatically. A custom fleet dashboard is on our roadmap (tracking upstream OSEP-0006) but it's not the high-value missing piece right now; the missing piece is identity-aware orchestration on top of the API, which is what `apps/control-plane/` does.

**Q: Speedup vs. what?**
A: vs. running the same 20 hypotheses sequentially in the same cluster. Sum of per-sandbox durations is the serial baseline (~436s); wall-clock of the parallel race is ~31s. Same image, same VMs, same test. We are *not* claiming "14× faster than your laptop" — that would be a vastly bigger and dishonest number.

**Q: What about cold-start?**
A: Sandbox cold-start dominates the per-VM time today (~9s for fast paths, dominated by image pull and Kata boot). Upstream OpenSandbox ships a pre-warmed sandbox pool we haven't wired in yet; doing so would cut cold-start to ~50ms and push the speedup well past 30×. That's the next optimization.

**Q: Why Kata, not gVisor?**
A: Kata gives each sandbox a real guest Linux kernel via a lightweight VM. gVisor intercepts syscalls in user-space and has known compatibility gaps (no `/proc/self/exe`, partial syscall coverage). For arbitrary LLM-generated code, "behaves like a real Linux" matters more than gVisor's lower overhead.

**Q: What if the LLM does something destructive?**
A: It earns one disposable VM. Same answer as for any malicious code in a Kata sandbox — the kernel boundary contains it. We've never seen this happen in practice; the more common failure mode is syntactically broken patches that just timeout.

**Q: Could I run this against my own Kubernetes cluster?**
A: Yes. The runtime is upstream OpenSandbox; the chart is at `infra/helm/opensandbox/`. Our value-add is the Azure landing zone (Bicep IaC, Workload Identity wiring, Firewall egress policy, audit pipeline) plus the agentic patterns (hypothesis swarm being one). Pure-Kubernetes deployment works upstream-vanilla.

**Q: Is the portal that I see in your roadmap built?**
A: No. ACA revisions are provisioned, the source dirs are scaffolds. We're tracking upstream OSEP-0006 for the cross-fleet console design; we'll be one of the first to ship it. Today the visualization is what you just saw: Swagger UI + Container Insights.

---

## Demo failure modes and recovery

| Symptom | Cause | Recovery |
|---|---|---|
| `curl localhost:18080/docs` returns connection refused | Port-forward died | Re-run the `kubectl port-forward` line, wait 3 seconds |
| `Try it out` in Swagger returns 401 even after Authorize | Pasted wrong key or trailing newline | `OSB_KEY=$(cat examples/.opensandbox-api-key | tr -d '\n')` then re-paste |
| Swarm Kimi call hangs >90s | Foundry rate limit or token expired | Re-export `AAD_TOKEN`, rerun. Mention "live API, real rate limit" as a feature |
| Swarm prints "could not parse Kimi response" | Token budget too low for N | Drop to `N_HYPOTHESES=10` and rerun (still impressive, still parallel) |
| All 20 sandboxes FAIL | Image pull failure or cluster pressure | Show `kubectl describe pod` for one — point at the error. "The platform is honest about its failures." |
| `kubectl` returns `no such host` | AKS auto-stopped | `az aks start ...`, wait 5 minutes, restart everything from pre-flight step 1 |

---

## What NOT to claim

- We do **not** have a custom fleet console — that's OSEP-0006, design only.
- We do **not** have Grafana dashboards for the swarm — only Container Insights.
- We do **not** have a portal at `ca-portalfe-opensandbox-dev...` — the ACA app runs the Microsoft helloworld placeholder image; no source code exists in `apps/portal-frontend/`.
- We do **not** claim "faster than your laptop" — we claim "faster than serial on the same substrate." Important distinction.
