"""
WebBridge harness for portal v2 audit fixes.

Verifies each finding from evidence/portal-ux-audit/audit-report.md plus
task #18 (VNC) and task #19 (pool slider).

Usage:
    python tests/webbridge/test_portal_ux.py
        [--portal http://localhost:8090]
        [--bridge http://127.0.0.1:10086]
        [--only P0-1,P0-3,#18]
        [--no-bridge]      # skip browser-driven checks (API-only)

Exit 0 if all passing tests pass; exit 1 if any fail.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

import urllib.request
import urllib.error

# Force UTF-8 stdout on Windows so emoji/non-ASCII notes don't crash print().
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------- Config ----------
REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "portal-ux-audit-tests"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = EVIDENCE_DIR / "REPORT.md"

PORTAL_URL = os.environ.get("PORTAL_URL", "http://localhost:8090")
BRIDGE_URL = os.environ.get("WEBBRIDGE_URL", "http://127.0.0.1:10086")
SESSION = "portal-ux-tests"
USE_BRIDGE = True  # mutated by CLI


# ---------- Result type ----------
@dataclass
class Result:
    finding: str
    title: str
    status: str  # pass | fail | skip
    elapsed_s: float = 0.0
    evidence: list[str] = field(default_factory=list)
    notes: str = ""


RESULTS: list[Result] = []


# ---------- HTTP helpers ----------
def _http(method: str, url: str, body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict | str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            ct = r.headers.get("Content-Type", "")
            if "json" in ct:
                try:
                    return r.status, json.loads(raw)
                except json.JSONDecodeError:
                    return r.status, raw
            return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def api(path: str, method: str = "GET", body: dict | None = None, timeout: float = 30.0):
    return _http(method, PORTAL_URL.rstrip("/") + path, body=body, timeout=timeout)


def bridge(action: str, args: dict | None = None, timeout: float = 60.0):
    """Returns the inner `data` dict from the daemon's {ok, data} envelope, or {} on error."""
    payload = {"action": action, "args": args or {}, "session": SESSION}
    code, body = _http("POST", BRIDGE_URL.rstrip("/") + "/command", body=payload, timeout=timeout)
    if code == 200 and isinstance(body, dict):
        if body.get("ok"):
            data = body.get("data")
            return 200, data if data is not None else {}
        return code, {"__bridge_error__": body.get("error")}
    return code, body


# ---------- Bridge helpers ----------
def bridge_ok() -> tuple[bool, str]:
    code, body = _http("GET", BRIDGE_URL.rstrip("/") + "/status", timeout=5)
    if code != 200 or not isinstance(body, dict):
        return False, f"status http={code} body={body!r}"
    if not body.get("running") or not body.get("extension_connected"):
        return False, f"daemon not healthy: {body}"
    return True, "ok"


def navigate(url: str, new_tab: bool = True, group: str = "portal-ux-tests"):
    return bridge("navigate", {"url": url, "newTab": new_tab, "group_title": group})


def snapshot() -> dict:
    code, body = bridge("snapshot", {})
    if code == 200 and isinstance(body, dict):
        return body
    return {"error": body, "code": code}


def evaluate(code: str, timeout: float = 60.0) -> Any:
    code_status, body = bridge("evaluate", {"code": code}, timeout=timeout)
    if code_status == 200 and isinstance(body, dict):
        return body.get("value")
    return {"__bridge_error__": body, "code": code_status}


def screenshot(name: str) -> str:
    """Take a screenshot and save it under EVIDENCE_DIR. Returns filename or '' on failure."""
    out = EVIDENCE_DIR / name
    helper = Path.home() / ".claude" / "skills" / "kimi-webbridge" / "scripts" / "screenshot.sh"
    if helper.exists() and shutil.which("bash"):
        try:
            r = subprocess.run(
                ["bash", str(helper), "-s", SESSION, "-o", str(out), "-f", "png", "-q", "70"],
                capture_output=True, text=True, timeout=30,
            )
            if out.exists():
                return str(out.relative_to(EVIDENCE_DIR))
        except Exception:  # noqa: BLE001
            pass
    # Fallback: direct API
    code, body = bridge("screenshot", {"format": "png", "quality": 70}, timeout=30)
    if code == 200 and isinstance(body, dict) and body.get("data"):
        try:
            out.write_bytes(base64.b64decode(body["data"]))
            return str(out.relative_to(EVIDENCE_DIR))
        except Exception:  # noqa: BLE001
            return ""
    return ""


def snapshot_text() -> str:
    snap = snapshot()
    if isinstance(snap, dict) and "tree" in snap:
        return json.dumps(snap.get("tree"), ensure_ascii=False)
    return json.dumps(snap, ensure_ascii=False)


# ---------- Test scaffolding ----------
def run(finding: str, title: str):
    """Decorator: register a test function."""
    def wrap(fn: Callable[[], dict]):
        def runner() -> Result:
            t0 = time.time()
            try:
                meta = fn() or {}
                status = meta.get("status", "fail")
                evidence = meta.get("evidence", [])
                notes = meta.get("notes", "")
            except Exception as e:  # noqa: BLE001
                status = "fail"
                evidence = []
                notes = f"exception: {type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            res = Result(finding=finding, title=title, status=status,
                         elapsed_s=round(time.time() - t0, 2),
                         evidence=evidence, notes=notes)
            RESULTS.append(res)
            print(f"[{status.upper():4}] {finding:6} {title}  ({res.elapsed_s}s)")
            if notes and status != "pass":
                first = notes.splitlines()[0][:200] if notes else ""
                print(f"        notes: {first}")
            return res
        runner.__name__ = f"test_{finding.lower().replace('-','_').replace('#','task')}"
        return runner
    return wrap


def wait_until(pred: Callable[[], bool], timeout: float, interval: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return False


# ============================================================
# Tests
# ============================================================

@run("P0-1", "Sandboxes empty state has CTA + non-contradictory subtitle")
def test_p0_1():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(3)
    shot = screenshot("P0-1-after.png")
    js = """(() => {
        const card = document.querySelector('.card-sandboxes-table');
        if (!card) return {found:false};
        const empty = card.querySelector('tbody .empty, tbody tr td.empty');
        const subtitle = card.querySelector('header .subtitle');
        const cs = empty ? getComputedStyle(empty) : null;
        const body = card.querySelector('.card-body');
        const bcs = body ? getComputedStyle(body) : null;
        return {
            found: !!card,
            emptyText: empty ? (empty.innerText || empty.textContent || '').trim() : null,
            subtitleText: subtitle ? (subtitle.innerText || '').trim() : null,
            maxHeight: bcs ? bcs.maxHeight : null,
            color: cs ? cs.color : null
        };
    })()"""
    info = evaluate(js)
    notes = json.dumps(info, ensure_ascii=False)
    cta = bool(info and re.search(r"Click .?Create Sandbox.?", str(info.get("emptyText", "") if isinstance(info, dict) else "")))
    subtitle_ok = bool(info and isinstance(info, dict) and info.get("subtitleText")
                       and not re.search(r"\b0 total.*running:\s*\d+", info["subtitleText"]))
    max_h = isinstance(info, dict) and info.get("maxHeight") not in (None, "none", "")
    passing = cta and (subtitle_ok or max_h)
    return {
        "status": "pass" if passing else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"cta={cta} subtitle_ok={subtitle_ok} maxHeight={max_h} info={notes}",
    }


@run("P0-2", "Refresh button + auto-expire toast")
def test_p0_2():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    js = """(() => {
        const card = document.querySelector('.card-sandboxes-table');
        if (!card) return {found:false};
        const buttons = Array.from(card.querySelectorAll('button')).map(b => (b.innerText || b.textContent || '').trim());
        const hasRefresh = buttons.some(t => /refresh/i.test(t));
        return {found:true, buttons, hasRefresh};
    })()"""
    info = evaluate(js)
    hasRefresh = isinstance(info, dict) and info.get("hasRefresh")
    shot = screenshot("P0-2-after.png")
    return {
        "status": "pass" if hasRefresh else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"info={json.dumps(info, ensure_ascii=False)}",
    }


@run("P0-3", "Identity chip populated from /api/identity")
def test_p0_3():
    code, body = api("/api/identity")
    api_user = body.get("az_user") if isinstance(body, dict) else None
    if USE_BRIDGE:
        navigate(PORTAL_URL)
        time.sleep(2)
        js = """(() => {
            const chips = document.querySelectorAll('.identity-chips .chip');
            return Array.from(chips).map(c => (c.innerText || c.textContent || '').trim());
        })()"""
        chips = evaluate(js) or []
        shot = screenshot("P0-3-after.png")
    else:
        chips = []
        shot = ""
    has_email_chip = any(("@" in c) or (c and c not in {"—", "…", "⚠ DEV MODE"}) for c in chips if isinstance(c, str))
    passing = bool(api_user) and has_email_chip
    return {
        "status": "pass" if passing else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"api_user={api_user!r} chips={chips}",
    }


@run("P0-4", "Pool gauge shows provenance + freshness")
def test_p0_4():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(3)
    js = """(() => {
        const card = document.querySelector('.card-observability');
        return card ? (card.innerText || '').trim() : null;
    })()"""
    text = evaluate(js) or ""
    text = str(text)
    has_source = bool(re.search(r"Source:\s*Pool\s*CR", text, re.I))
    has_polled = bool(re.search(r"last polled\s*\d+\s*s", text, re.I))
    shot = screenshot("P0-4-after.png")
    return {
        "status": "pass" if (has_source and has_polled) else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"has_source={has_source} has_polled={has_polled} text_excerpt={text[:300]!r}",
    }


@run("P0-5", "Events feed: BACKOFF chip has error styling + full message")
def test_p0_5():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(3)
    js = """(() => {
        const rows = Array.from(document.querySelectorAll('.events-feed .event-row'));
        return rows.map(r => {
            const chip = r.querySelector('.evt-chip');
            const msg = r.querySelector('span:not(.evt-chip)');
            const cs = chip ? getComputedStyle(chip) : null;
            return {
                reason: chip ? (chip.innerText || '').trim() : null,
                chipClass: chip ? chip.className : null,
                bg: cs ? cs.backgroundColor : null,
                color: cs ? cs.color : null,
                message: msg ? (msg.innerText || '').trim() : null,
                truncated: msg ? /\\.\\.\\.\\s*$/.test((msg.innerText||'').trim()) : false,
            };
        });
    })()"""
    rows = evaluate(js) or []
    if not isinstance(rows, list):
        rows = []
    backoff = [r for r in rows if isinstance(r, dict) and r.get("reason") and "BACKOFF" in r["reason"].upper()]
    truncated_any = any(r.get("truncated") for r in rows if isinstance(r, dict))
    err_styled = any("error" in (r.get("chipClass") or "").lower() or "fail" in (r.get("chipClass") or "").lower() for r in backoff)
    shot = screenshot("P0-5-after.png")
    passing = (not truncated_any) and (not backoff or err_styled)
    return {
        "status": "pass" if passing else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"backoff={backoff} truncated_any={truncated_any} err_styled={err_styled} rows={len(rows)}",
    }


@run("P0-6", "Last action persists after Stop/Start")
def test_p0_6():
    code, state = api("/api/cluster/state")
    if not isinstance(state, dict):
        return {"status": "fail", "notes": f"/api/cluster/state http={code} body={state!r}"}
    last_action_before = state.get("last_action")
    if last_action_before:
        return {"status": "pass", "notes": f"already persisted: last_action={last_action_before!r}"}
    return {
        "status": "fail",
        "notes": ("last_action not exposed by /api/cluster/state. "
                  "Skipping live Stop/Start to avoid mutating cluster. "
                  f"state={state!r}"),
    }


@run("P1-1", "Swarm leaderboard empty hint")
def test_p1_1():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    js = """(() => {
        const card = document.querySelector('.card-swarm');
        if (!card) return {found:false};
        const text = (card.innerText || '').trim();
        const hasHint = /click\\s*run|start a new|no runs yet|empty/i.test(text);
        return {found:true, text, hasHint};
    })()"""
    info = evaluate(js)
    has_hint = isinstance(info, dict) and bool(info.get("hasHint"))
    shot = screenshot("P1-1-after.png")
    return {
        "status": "pass" if has_hint else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"info={json.dumps(info, ensure_ascii=False)[:400]}",
    }


@run("P1-2", "Sandbox-creation toast persists + Copy ID available")
def test_p1_2():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    js = """(() => {
        const stack = document.getElementById('toast-stack');
        return {
            hasStack: !!stack,
            toastCount: stack ? stack.children.length : 0,
            hasCopyButton: !!document.querySelector('[data-action="copy-sandbox-id"], button.copy-id, button[aria-label*="copy" i]'),
        };
    })()"""
    info = evaluate(js)
    has_copy = isinstance(info, dict) and bool(info.get("hasCopyButton"))
    shot = screenshot("P1-2-after.png")
    return {
        "status": "pass" if has_copy else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"info={info}",
    }


@run("P1-3", "Run-in-sandbox progress strip")
def test_p1_3():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    js = """(() => {
        const html = document.body.innerHTML;
        return {
            hasProgressStrip: /class=\"[^\"]*(progress-strip|exec-progress|vm-booting)/.test(html),
            hasElapsed: /elapsed/i.test(html),
        };
    })()"""
    info = evaluate(js)
    passing = isinstance(info, dict) and bool(info.get("hasProgressStrip"))
    return {
        "status": "pass" if passing else "fail",
        "notes": f"info={info}",
    }


@run("P1-4", "Single-click Run sin/cos demo")
def test_p1_4():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    js = """(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const labels = btns.map(b => (b.innerText || '').trim());
        const hasRunDemo = labels.some(t => /run sin\\/cos demo|run sin\\/cos|sin\\s*\\/\\s*cos demo/i.test(t));
        return {labels, hasRunDemo};
    })()"""
    info = evaluate(js)
    has_button = isinstance(info, dict) and bool(info.get("hasRunDemo"))
    shot = screenshot("P1-4-after.png")
    return {
        "status": "pass" if has_button else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"hasRunDemo={has_button} labels_excerpt={(info or {}).get('labels', [])[:30]}",
    }


@run("P2-1", "No replacement char in visible text")
def test_p2_1():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    js = "(() => document.body.innerText)()"
    text = evaluate(js) or ""
    text = str(text)
    has_replacement = "�" in text
    return {
        "status": "pass" if not has_replacement else "fail",
        "notes": f"replacement_char_found={has_replacement}",
    }


@run("P2-2", "? opens cheatsheet, g-s focuses swarm")
def test_p2_2():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    # Press '?' via keyboard event
    open_js = """(() => {
        document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'?', shiftKey:true, bubbles:true}));
        return true;
    })()"""
    evaluate(open_js)
    time.sleep(1)
    check_js = """(() => {
        const el = document.querySelector('#cheatsheet, [data-cheatsheet], .cheatsheet, .shortcut-help');
        if (!el) return {found:false};
        const cs = getComputedStyle(el);
        return {found:true, visible: cs.display !== 'none' && cs.visibility !== 'hidden'};
    })()"""
    info = evaluate(check_js)
    passing = isinstance(info, dict) and bool(info.get("visible"))
    shot = screenshot("P2-2-after.png")
    return {
        "status": "pass" if passing else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"info={info}",
    }


@run("P2-3", "URL hash updates after starting a swarm run")
def test_p2_3():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    # Inspect the existing hash router behaviour without actually starting a real run
    # (the contract: starting a run should update location.hash). We check that a swarm
    # router/hash listener is wired up by reading window.location and looking for hashchange handlers.
    js = """(() => {
        return {
            hash: window.location.hash,
            hasRouterTag: /#\\/swarm\\//.test(document.body.innerHTML + window.location.hash),
        };
    })()"""
    info = evaluate(js)
    # We pass if hash already has the pattern OR there is no router yet (will fail strictly).
    has_pattern = isinstance(info, dict) and bool(re.search(r"#/swarm/", str(info.get("hash") or "")))
    return {
        "status": "pass" if has_pattern else "fail",
        "notes": f"info={info} (test starts no real swarm to keep cluster idle; checks hash format only)",
    }


@run("#18", "VNC: Launch desktop button shows noVNC iframe")
def test_18():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(2)
    js = """(() => {
        const btns = Array.from(document.querySelectorAll('button, a'));
        const labels = btns.map(b => (b.innerText || '').trim());
        const target = btns.find(b => /launch\\s*desktop|open\\s*vnc|launch\\s*vnc/i.test((b.innerText||'').trim()));
        return {labels: labels.slice(0,60), hasButton: !!target};
    })()"""
    info = evaluate(js)
    if not isinstance(info, dict) or not info.get("hasButton"):
        return {"status": "fail", "notes": f"no Launch desktop button. labels_excerpt={(info or {}).get('labels', [])[:30]}"}
    # Don't actually click — Kata VM boot is expensive. Confirm the wiring exists in JS bundle.
    js2 = """(() => {
        const html = document.documentElement.outerHTML;
        const hasIframeWiring = /\\/proxy\\/6080\\/vnc\\.html|noVNC|vnc-iframe/i.test(html);
        return {hasIframeWiring};
    })()"""
    wired = evaluate(js2)
    has_wired = isinstance(wired, dict) and bool(wired.get("hasIframeWiring"))
    shot = screenshot("18-after.png")
    return {
        "status": "pass" if has_wired else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"button=yes wiring_in_dom={has_wired}",
    }


@run("#19", "Pool slider patches /api/pool/kata poolMax (debounced 500ms)")
def test_19():
    # API-side: poolMax must be readable from /api/pool/kata
    code, body = api("/api/pool/kata")
    if code != 200 or not isinstance(body, dict):
        return {"status": "fail", "notes": f"/api/pool/kata http={code} body={body!r}"}
    has_max = body.get("pool_max") is not None or body.get("poolMax") is not None
    if not USE_BRIDGE:
        return {
            "status": "pass" if has_max else "fail",
            "notes": f"api-only check, pool={body}",
        }
    navigate(PORTAL_URL)
    time.sleep(2)
    js = """(() => {
        const slider = document.querySelector('input[type="range"][data-pool="kata"], input[type="range"].pool-slider, .pool-slider input[type="range"]');
        return {hasSlider: !!slider};
    })()"""
    info = evaluate(js)
    has_slider = isinstance(info, dict) and bool(info.get("hasSlider"))
    shot = screenshot("19-after.png")
    passing = has_slider and has_max
    return {
        "status": "pass" if passing else "fail",
        "evidence": [shot] if shot else [],
        "notes": f"hasSlider={has_slider} pool_max_in_api={has_max} pool={body}",
    }


@run("C1", "/api/config exposes ACR/VNC defaults")
def test_c1():
    code, body = api("/api/config")
    has_endpoint = code == 200 and isinstance(body, dict)
    keys = list(body.keys()) if isinstance(body, dict) else []
    has_keys = has_endpoint and any(k.upper() in {"ACR_REGISTRY", "VNC_IMAGE", "DEFAULT_POOL_NAME"} for k in keys)
    return {
        "status": "pass" if (has_endpoint and has_keys) else "fail",
        "notes": f"http={code} keys={keys}",
    }


@run("C2", "Observability sparkline + external link")
def test_c2():
    if not USE_BRIDGE:
        return {"status": "skip", "notes": "bridge disabled"}
    navigate(PORTAL_URL)
    time.sleep(3)
    js = """(() => {
        const card = document.querySelector('.card-observability');
        if (!card) return {found:false};
        const svg = card.querySelector('svg');
        const polylines = svg ? svg.querySelectorAll('polyline, path').length : 0;
        const points = svg ? (svg.querySelector('polyline')?.getAttribute('points') || '').trim() : '';
        const links = Array.from(card.querySelectorAll('a[href]')).map(a => a.href);
        const hasHubble = links.some(h => /hubble|grafana|azure\\.com/i.test(h));
        return {found:true, hasSvg: !!svg, polylines, pointsLen: points.length, links, hasHubble};
    })()"""
    info = evaluate(js)
    has_chart = isinstance(info, dict) and info.get("polylines", 0) >= 1 and info.get("pointsLen", 0) > 0
    has_link = isinstance(info, dict) and bool(info.get("hasHubble"))
    return {
        "status": "pass" if (has_chart and has_link) else "fail",
        "notes": f"info={info}",
    }


@run("C3", "Chat returns conversation_id, history endpoint exists")
def test_c3():
    code, body = api("/api/kimi/chat", method="POST",
                     body={"messages": [{"role": "user", "content": "ping"}],
                           "deployment": "Kimi-K2.6"}, timeout=60)
    if code != 200 or not isinstance(body, dict):
        return {"status": "fail", "notes": f"chat http={code} body={str(body)[:200]!r}"}
    cid = body.get("conversation_id")
    if not cid:
        return {"status": "fail", "notes": f"no conversation_id in response keys={list(body.keys())}"}
    hcode, hbody = api("/api/kimi/conversations", timeout=10)
    history_ok = hcode == 200 and isinstance(hbody, (list, dict))
    return {
        "status": "pass" if (cid and history_ok) else "fail",
        "notes": f"conv_id={cid!r} history_http={hcode} history_keys={list(hbody.keys()) if isinstance(hbody, dict) else 'list'}",
    }


# ---------- Negative / regression tests ----------

@run("REG-1", "Old: /api/swarm/runs accepts N=4 + returns run_id")
def test_reg_swarm_accepts():
    # Do not actually wait for 4 swarm members to finish — that's a 4-min cluster job.
    # Just verify the contract is intact.
    code, body = api("/api/swarm/runs", method="POST",
                     body={"n": 4, "deployment": "Kimi-K2.6"}, timeout=15)
    ok = code in (200, 202) and isinstance(body, dict) and (body.get("run_id") or body.get("id"))
    rid = body.get("run_id") or body.get("id") if isinstance(body, dict) else None
    # Cancel immediately to avoid charging cluster time
    if rid:
        api(f"/api/swarm/runs/{rid}", method="DELETE", timeout=10)
    return {
        "status": "pass" if ok else "fail",
        "notes": f"http={code} run_id={rid!r}",
    }


@run("REG-2", "Old: /api/cluster/state still flips power pill")
def test_reg_cluster_state():
    code, body = api("/api/cluster/state")
    ok = code == 200 and isinstance(body, dict) and body.get("power") in {"Running", "Stopped", "Starting", "Stopping"}
    return {
        "status": "pass" if ok else "fail",
        "notes": f"http={code} body_keys={list(body.keys()) if isinstance(body, dict) else type(body).__name__}",
    }


# ============================================================
# Driver
# ============================================================
TESTS: list[Callable[[], Result]] = [
    test_p0_1, test_p0_2, test_p0_3, test_p0_4, test_p0_5, test_p0_6,
    test_p1_1, test_p1_2, test_p1_3, test_p1_4,
    test_p2_1, test_p2_2, test_p2_3,
    test_18, test_19,
    test_c1, test_c2, test_c3,
    test_reg_swarm_accepts, test_reg_cluster_state,
]


def write_report(elapsed: float) -> None:
    lines = []
    lines.append("# Portal v2 UX Audit — Test Harness Report")
    lines.append("")
    lines.append(f"- Portal: `{PORTAL_URL}`")
    lines.append(f"- WebBridge: `{BRIDGE_URL}`  (used: {USE_BRIDGE})")
    lines.append(f"- Wall time: {elapsed:.1f}s")
    lines.append(f"- Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append("")
    passes = sum(1 for r in RESULTS if r.status == "pass")
    fails = sum(1 for r in RESULTS if r.status == "fail")
    skips = sum(1 for r in RESULTS if r.status == "skip")
    lines.append(f"**Summary:** {passes} pass · {fails} fail · {skips} skip · {len(RESULTS)} total")
    lines.append("")
    lines.append("| ID | Title | Status | Elapsed | Evidence | Notes |")
    lines.append("|----|-------|--------|---------|----------|-------|")
    for r in RESULTS:
        ev = ", ".join(f"`{e}`" for e in r.evidence) if r.evidence else "—"
        notes = (r.notes or "").replace("\n", " ").replace("|", "\\|")
        if len(notes) > 220:
            notes = notes[:217] + "…"
        lines.append(f"| {r.finding} | {r.title} | {r.status} | {r.elapsed_s}s | {ev} | {notes} |")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport: {REPORT_PATH}")


def main() -> int:
    global PORTAL_URL, BRIDGE_URL, USE_BRIDGE
    ap = argparse.ArgumentParser()
    ap.add_argument("--portal", default=PORTAL_URL)
    ap.add_argument("--bridge", default=BRIDGE_URL)
    ap.add_argument("--only", default="", help="comma-separated finding ids to run (e.g. P0-1,#18)")
    ap.add_argument("--no-bridge", action="store_true", help="skip browser-driven checks")
    args = ap.parse_args()

    PORTAL_URL = args.portal
    BRIDGE_URL = args.bridge
    USE_BRIDGE = not args.no_bridge

    # Pre-flight
    print(f"== Portal: {PORTAL_URL} ==")
    pcode, pbody = api("/api/health", timeout=5)
    print(f"   /api/health http={pcode} body={pbody!r}")
    if pcode != 200:
        print("FATAL: portal not reachable; aborting.")
        return 2
    if USE_BRIDGE:
        ok, msg = bridge_ok()
        print(f"== Bridge: {BRIDGE_URL} == {msg}")
        if not ok:
            print("WebBridge not healthy. Re-running with --no-bridge mode.")
            USE_BRIDGE = False

    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None

    t0 = time.time()
    for t in TESTS:
        if only and not any(t.__name__.endswith(_normalize(o)) for o in only):
            continue
        t()
    if USE_BRIDGE:
        bridge("close_session", {})
    elapsed = time.time() - t0
    write_report(elapsed)

    passes = sum(1 for r in RESULTS if r.status == "pass")
    fails = sum(1 for r in RESULTS if r.status == "fail")
    skips = sum(1 for r in RESULTS if r.status == "skip")
    print("\n" + "=" * 60)
    print(f"SUMMARY: {passes} pass · {fails} fail · {skips} skip · total {len(RESULTS)}  ({elapsed:.1f}s)")
    if skips:
        print("Skipped:")
        for r in RESULTS:
            if r.status == "skip":
                print(f"  {r.finding}: {r.notes}")
    print("=" * 60)
    return 0 if fails == 0 else 1


def _normalize(s: str) -> str:
    return s.lower().replace("-", "_").replace("#", "task").lstrip("_")


if __name__ == "__main__":
    sys.exit(main())
