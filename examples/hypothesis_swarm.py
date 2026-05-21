"""Hypothesis Swarm Debugger — agentic demo.

Premise:
    Give Kimi a failing test plus the buggy source. Ask it for N *diverse*
    candidate diagnoses + patches. Then fan all N out into their own
    Kata-isolated sandboxes in parallel — each one applies its patch,
    re-runs the test suite, and reports back. First green wins.

Why this is interesting:
    - N hypotheses, each in its own VM, each running concurrently. The
      Kata boundary means a wrong hypothesis (think: shell side effects,
      filesystem damage, fork bombs) only nukes its own disposable VM.
    - Wall-clock time is roughly the time of the *slowest* hypothesis,
      not the sum. We print both numbers so the speedup is honest.
    - The target bug is the Python mutable-default-argument footgun,
      with a second test that distinguishes a correct fix from a lazy
      "just reset .items in add()" patch.

Run:
    kubectl -n opensandbox-system port-forward svc/opensandbox-server 18080:8080 &
    az login                       # so we can get an AAD token
    export AAD_TOKEN=$(az account get-access-token \
        --resource https://cognitiveservices.azure.com \
        --query accessToken -o tsv)
    python examples/hypothesis_swarm.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from subprocess import check_output

from opensandbox import Sandbox  # type: ignore[import-not-found]
from opensandbox.config import ConnectionConfig  # type: ignore[import-not-found]

# ---- Config ---------------------------------------------------------------

N_HYPOTHESES = int(os.environ.get("N_HYPOTHESES", "20"))
SANDBOX_IMAGE = "python:3.12-slim"
DOMAIN = "localhost:18080"
KIMI_ENDPOINT = "https://aihubeastus26267492086.cognitiveservices.azure.com"
KIMI_DEPLOYMENTS = ["Kimi-K2.5", "Kimi-K2.6"]

THIS = Path(__file__).resolve().parent
API_KEY_FILE = THIS / ".opensandbox-api-key"
TARGET_DIR = THIS / "swarm_target"
CART_PATH = TARGET_DIR / "cart.py"
TEST_PATH = TARGET_DIR / "test_cart.py"


# ---- Kimi plumbing (copied/adapted from kimi_via_osb.py) ------------------

def get_aad_token() -> str:
    pre = os.environ.get("AAD_TOKEN")
    if pre:
        return pre.strip()
    out = check_output(
        "az account get-access-token "
        "--resource https://cognitiveservices.azure.com "
        "--query accessToken -o tsv",
        shell=True, text=True,
    )
    return out.strip()


def ask_kimi(token: str, prompt: str, max_tokens: int = 16000,
             temperature: float = 0.7) -> tuple[str, str]:
    """Hit Kimi with retry+fallback. Returns (deployment_used, raw_text)."""
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    raw = ""
    for dep in KIMI_DEPLOYMENTS:
        url = (
            f"{KIMI_ENDPOINT}/openai/deployments/{dep}"
            f"/chat/completions?api-version=2024-10-21"
        )
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                resp = json.loads(
                    urllib.request.urlopen(req, timeout=120).read()
                )
                msg = resp["choices"][0]["message"]
                raw = msg.get("content") or msg.get("reasoning_content") or ""
                if raw.strip():
                    return dep, raw
            except urllib.error.HTTPError as e:
                err = e.read().decode()[:200]
                print(f"[{dep}] attempt {attempt + 1} HTTP {e.code}: {err}")
                if e.code == 429:
                    time.sleep(2 ** attempt)
                else:
                    break
            except Exception as e:
                print(f"[{dep}] attempt {attempt + 1} error: {e}")
                time.sleep(2 ** attempt)
    return "<none>", raw


# ---- Prompt + parsing ------------------------------------------------------

HYPOTHESIS_PROMPT = """You are a debugger. The user has a failing pytest. You must produce {n} DIVERSE
hypotheses about the bug and {n} corresponding patched versions of the Cart class.

Below is the buggy source and the failing test.

=== cart.py (buggy) ===
{cart_src}

=== test_cart.py ===
{test_src}

=== failing-test output ===
{failure_tail}

Return ONLY a JSON array of exactly {n} objects. No prose, no markdown.
Each object MUST have these keys:
  "idx":             integer 1..{n}
  "diagnosis":       one short sentence describing what you think is wrong
  "patched_class":   ONLY the replacement `class Cart:` block as a string,
                     starting with `class Cart:` and ending after the last
                     method. Do NOT re-include the module docstring or any
                     imports — the orchestrator will splice your class in.

Make the hypotheses genuinely DIFFERENT from each other — different framings
of the bug, different fix strategies. Some may be wrong; that's fine. The
test suite will be the judge.

Do NOT modify test_cart.py. Only patched_class is yours to write.
"""


def parse_hypotheses(raw: str, n_expected: int) -> list[dict]:
    """Defensive JSON parse: strip ```json fences, locate the array, parse."""
    s = raw.strip()
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s, flags=re.M)
    s = s.replace("```", "").strip()
    i = s.find("[")
    j = s.rfind("]")
    if i < 0 or j < 0:
        raise ValueError("Kimi response had no JSON array")
    arr = json.loads(s[i:j + 1])
    if not isinstance(arr, list):
        raise ValueError("Kimi response was not a JSON array")
    return arr[:n_expected]


# ---- Sandbox race ----------------------------------------------------------

def splice_class(original_src: str, patched_class: str) -> str:
    """Replace the `class Cart:` block in original_src with patched_class.

    If patched_class doesn't start with `class Cart:` we fall back to using
    the LLM output verbatim (it may have decided to return the whole file
    anyway, which is also fine).
    """
    patched = patched_class.strip()
    if not patched.startswith("class Cart"):
        return patched if patched else original_src
    m = re.search(r"^class\s+Cart\b", original_src, flags=re.M)
    if not m:
        return patched
    prefix = original_src[: m.start()]
    return prefix.rstrip() + "\n\n\n" + patched + "\n"


def _flatten(events) -> str:
    if not events:
        return ""
    if isinstance(events, str):
        return events
    return "\n".join(getattr(e, "text", str(e)) for e in events)


async def race_one(idx: int, diagnosis: str, patched_cart: str,
                   test_source: str, config: ConnectionConfig) -> dict:
    """Run one hypothesis in its own Kata-isolated sandbox.

    Each sandbox is a fresh python:3.12-slim VM with no shared state.
    A wrong hypothesis can do arbitrarily silly things to its own
    filesystem — the Kata kernel boundary ensures the blast radius is
    one disposable VM, not the cluster.
    """
    t0 = time.monotonic()
    result = {
        "idx": idx,
        "diagnosis": diagnosis,
        "passed": False,
        "duration_s": 0.0,
        "exit_code": -1,
        "stdout_tail": "",
        "patched_cart": patched_cart,
    }
    try:
        sandbox = await Sandbox.create(
            SANDBOX_IMAGE,
            connection_config=config,
            timeout=timedelta(minutes=5),
            ready_timeout=timedelta(minutes=3),
        )
        async with sandbox:
            wrapped = (
                "set -e\n"
                "mkdir -p /work && cd /work\n"
                "pip install --quiet pytest >/dev/null 2>&1\n"
                "cat > /work/cart.py <<'OSBCART_EOF'\n"
                f"{patched_cart}\n"
                "OSBCART_EOF\n"
                "cat > /work/test_cart.py <<'OSBTEST_EOF'\n"
                f"{test_source}\n"
                "OSBTEST_EOF\n"
                "cd /work && python -m pytest -x test_cart.py "
                "; echo \"__EXIT__=$?\"\n"
            )
            execution = await sandbox.commands.run(wrapped)
            stdout = _flatten(
                execution.logs.stdout if execution.logs else None
            )
            stderr = _flatten(
                execution.logs.stderr if execution.logs else None
            )
            combined = stdout + ("\n" + stderr if stderr else "")
            # Parse the trailing __EXIT__= marker so we don't depend on
            # the SDK preserving pytest's real exit code.
            exit_code = execution.exit_code if execution.exit_code is not None else -1
            m = re.search(r"__EXIT__=(\d+)", combined)
            if m:
                exit_code = int(m.group(1))
            result["exit_code"] = exit_code
            result["passed"] = exit_code == 0
            tail = "\n".join(combined.splitlines()[-15:])
            result["stdout_tail"] = tail
    except Exception as e:
        result["stdout_tail"] = f"[orchestrator-error] {type(e).__name__}: {e}"
    result["duration_s"] = round(time.monotonic() - t0, 2)
    status = "PASS" if result["passed"] else "FAIL"
    print(f"  [#{idx:02d}] {status} in {result['duration_s']:6.2f}s — "
          f"{diagnosis[:80]}")
    return result


# ---- Orchestrator ----------------------------------------------------------

async def main() -> int:
    # Phase 1: capture the baseline failure (so we can show Kimi exactly
    # what pytest says, not just the source).
    print("=" * 70)
    print(f"HYPOTHESIS SWARM DEBUGGER  (N={N_HYPOTHESES})")
    print("=" * 70)

    cart_src = CART_PATH.read_text(encoding="utf-8")
    test_src = TEST_PATH.read_text(encoding="utf-8")
    print(f"[+] Target: {CART_PATH.relative_to(THIS.parent)}")
    print(f"[+] Test:   {TEST_PATH.relative_to(THIS.parent)}")

    # We embed a short hand-crafted "failure tail" so we don't need to
    # actually shell out to pytest before talking to Kimi. (We confirmed
    # the baseline failure separately during the smoke test.)
    failure_tail = (
        "    def test_carts_are_independent():\n"
        "        c1 = Cart()\n"
        "        c1.add(\"apple\")\n"
        "        c2 = Cart()\n"
        ">       assert c2.items == [], ...\n"
        "E       AssertionError: new cart should be empty, got ['apple'].\n"
        "E       assert ['apple'] == []"
    )

    # Phase 2: ask Kimi for N hypotheses.
    token = get_aad_token()
    print(f"[+] Got AAD token, prefix={token[:12]}…")
    prompt = HYPOTHESIS_PROMPT.format(
        n=N_HYPOTHESES,
        cart_src=cart_src,
        test_src=test_src,
        failure_tail=failure_tail,
    )
    print(f"[+] Asking Kimi for {N_HYPOTHESES} diverse hypotheses…")
    t_kimi_start = time.monotonic()
    dep_used, raw = ask_kimi(token, prompt)
    t_kimi = time.monotonic() - t_kimi_start
    print(f"[+] Kimi deployment={dep_used}, response_chars={len(raw)}, "
          f"took {t_kimi:.1f}s")

    try:
        hypotheses = parse_hypotheses(raw, N_HYPOTHESES)
    except Exception as e:
        print(f"FATAL: could not parse Kimi response: {e}", file=sys.stderr)
        print("---- raw (first 1000 chars) ----")
        print(raw[:1000])
        return 2
    print(f"[+] Parsed {len(hypotheses)} hypotheses")

    # Phase 3: fan out — one Kata sandbox per hypothesis, all concurrent.
    api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    config = ConnectionConfig(
        domain=DOMAIN,
        api_key=api_key,
        protocol="http",
        use_server_proxy=True,
    )
    print(f"[+] Fanning out {len(hypotheses)} Kata-isolated sandboxes "
          f"(image={SANDBOX_IMAGE})…")
    t_swarm_start = time.monotonic()
    tasks = [
        race_one(
            int(h.get("idx", i + 1)),
            str(h.get("diagnosis", "?")),
            splice_class(cart_src, str(h.get("patched_class", ""))),
            test_src,
            config,
        )
        for i, h in enumerate(hypotheses)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    t_swarm = time.monotonic() - t_swarm_start

    # Phase 4: leaderboard, winner, speedup.
    print()
    print("=" * 70)
    print("LEADERBOARD  (sorted: PASS first, then by duration)")
    print("=" * 70)
    results_sorted = sorted(
        results, key=lambda r: (not r["passed"], r["duration_s"])
    )
    for r in results_sorted:
        verdict = "PASS" if r["passed"] else "FAIL"
        print(f"  #{r['idx']:02d}  {verdict}  {r['duration_s']:6.2f}s  "
              f"exit={r['exit_code']:>3}  {r['diagnosis'][:70]}")

    passes = [r for r in results if r["passed"]]
    durations = [r["duration_s"] for r in results]
    serial_estimate = sum(durations)
    speedup = serial_estimate / t_swarm if t_swarm > 0 else 0.0

    print()
    print("=" * 70)
    print("TIMING")
    print("=" * 70)
    print(f"  Kimi call:                 {t_kimi:7.2f}s")
    print(f"  Swarm wall-clock:          {t_swarm:7.2f}s")
    print(f"  Sum-of-sandbox-durations:  {serial_estimate:7.2f}s "
          f"(what serial would have cost)")
    print(f"  Speedup vs. serial:        {speedup:7.2f}x")
    print()
    print(f"  Hypotheses:  {len(results)}")
    print(f"  Passed:      {len(passes)}")
    print(f"  Failed:      {len(results) - len(passes)}")

    if passes:
        winner = min(passes, key=lambda r: r["duration_s"])
        print()
        print("=" * 70)
        print(f"WINNER:  hypothesis #{winner['idx']}  "
              f"({winner['duration_s']:.2f}s)")
        print("=" * 70)
        print(f"diagnosis: {winner['diagnosis']}")
        print()
        print("patched cart.py:")
        print("-" * 70)
        print(winner["patched_cart"])
        print("-" * 70)
        return 0
    else:
        print()
        print("NO HYPOTHESIS PASSED. Leaderboard above shows why.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
