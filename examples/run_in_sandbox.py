"""Run-in-Sandbox: execute one Python snippet in a fresh Kata VM, return JSON.

This is the portal's "Run in sandbox" backend. Spawned as a subprocess by
apps/portal-api so we can reuse `.venv-swarm` (the venv that has the
opensandbox SDK installed) without polluting portal-api's dependency graph.

Input (env vars):
    SNIPPET_PATH       absolute path to a file containing the raw Python snippet
    SANDBOX_IMAGE      OCI image URI (defaults to ACR-resident python:3.12-slim)
    AUTO_WRAP_CHART    "1" → auto-prepend matplotlib Agg backend + auto-append
                       savefig→base64→sentinel epilogue (only if the snippet
                       mentions matplotlib/pyplot/plt anywhere).
    EXEC_TIMEOUT_S     hard wall-clock cap (default 90)

Output (stdout, last line is JSON; everything else is logs to stderr):
    {
      "exit_code": int,
      "stdout":    str,    # snippet stdout (chart sentinel stripped out)
      "stderr":    str,
      "chart_b64": str|null,
      "duration_s": float,
      "sandbox_id": str|null,
      "image":      str
    }
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox  # type: ignore[import-not-found]
from opensandbox.config import ConnectionConfig  # type: ignore[import-not-found]


DOMAIN = os.environ.get("OPENSANDBOX_DOMAIN", "localhost:18080")
SANDBOX_IMAGE = os.environ.get(
    "SANDBOX_IMAGE",
    "acropensandboxdemo7075.azurecr.io/python:3.12-slim",
)
EXEC_TIMEOUT_S = int(os.environ.get("EXEC_TIMEOUT_S", "90"))
AUTO_WRAP_CHART = os.environ.get("AUTO_WRAP_CHART", "1") == "1"

THIS = Path(__file__).resolve().parent
DEFAULT_KEY_FILE = THIS / ".opensandbox-api-key"
API_KEY_FILE = Path(os.environ.get("OPENSANDBOX_API_KEY_FILE", str(DEFAULT_KEY_FILE)))

CHART_SENTINEL_OPEN = "<<<CHART:"
CHART_SENTINEL_CLOSE = ">>>"
CHART_RE = re.compile(
    re.escape(CHART_SENTINEL_OPEN) + r"([A-Za-z0-9+/=\n]+?)" + re.escape(CHART_SENTINEL_CLOSE),
    re.DOTALL,
)


def _log(msg: str) -> None:
    """Stderr-only log so we keep stdout clean for the final JSON line."""
    print(f"[run_in_sandbox] {msg}", file=sys.stderr, flush=True)


def _wants_chart(snippet: str) -> bool:
    """Crude but reliable: does the snippet touch matplotlib?"""
    needles = ("matplotlib", "pyplot", "plt.", "plt ", "import plt")
    return any(n in snippet for n in needles)


def wrap_for_chart(snippet: str) -> str:
    """Wrap the user's snippet so it produces a chart sentinel on stdout.

    Strategy:
      - Force matplotlib's Agg backend BEFORE any pyplot import. We do this
        defensively even if the snippet already does `matplotlib.use(...)` —
        a second call is harmless once Agg is active.
      - After the snippet runs, save the current figure (if any) to a BytesIO,
        base64-encode, print the sentinel. plt.gcf() always returns a figure
        even if the user never created one — we guard by checking axes.
    """
    prologue = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as _osb_plt\n"
        "import io as _osb_io, base64 as _osb_b64\n"
    )
    epilogue = (
        "\n"
        "try:\n"
        "    _osb_fig = _osb_plt.gcf()\n"
        "    if _osb_fig and _osb_fig.get_axes():\n"
        "        _osb_buf = _osb_io.BytesIO()\n"
        "        _osb_fig.savefig(_osb_buf, format='png', bbox_inches='tight', dpi=110)\n"
        "        print('"
        + CHART_SENTINEL_OPEN
        + "' + _osb_b64.b64encode(_osb_buf.getvalue()).decode() + '"
        + CHART_SENTINEL_CLOSE
        + "')\n"
        "except Exception as _osb_e:\n"
        "    import sys as _osb_sys\n"
        "    print('[run_in_sandbox] chart capture failed:', _osb_e, file=_osb_sys.stderr)\n"
    )
    return prologue + "\n" + snippet + epilogue


def extract_chart(stdout: str) -> tuple[str, str | None]:
    """Pull `<<<CHART:...>>>` markers out of stdout. Returns (clean_stdout, b64)."""
    m = CHART_RE.search(stdout)
    if not m:
        return stdout, None
    b64 = re.sub(r"\s+", "", m.group(1))
    clean = CHART_RE.sub("", stdout).rstrip()
    return clean, b64


def _flatten(events) -> str:
    if not events:
        return ""
    if isinstance(events, str):
        return events
    return "\n".join(getattr(e, "text", str(e)) for e in events)


async def run_once(snippet: str) -> dict:
    """Spawn one fresh Kata sandbox, run snippet, tear down."""
    t0 = time.monotonic()
    out: dict = {
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "chart_b64": None,
        "duration_s": 0.0,
        "sandbox_id": None,
        "image": SANDBOX_IMAGE,
    }

    if AUTO_WRAP_CHART and _wants_chart(snippet):
        _log("matplotlib detected; auto-wrapping with chart capture")
        snippet = wrap_for_chart(snippet)

    api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    config = ConnectionConfig(
        domain=DOMAIN,
        api_key=api_key,
        protocol="http",
        use_server_proxy=True,
    )

    sandbox = await Sandbox.create(
        SANDBOX_IMAGE,
        connection_config=config,
        timeout=timedelta(minutes=5),
        ready_timeout=timedelta(minutes=3),
    )
    # The SDK's Sandbox object exposes the id on .id (verified via swarm code path).
    out["sandbox_id"] = getattr(sandbox, "id", None) or getattr(sandbox, "sandbox_id", None)
    _log(f"sandbox ready: {out['sandbox_id']}")

    async with sandbox:
        # We write the snippet to a heredoc-protected file so quoting/escaping
        # in the user's code never collides with shell parsing. EOF sentinel
        # is chosen to be vanishingly unlikely to appear in real snippets.
        wrapped = (
            "set -e\n"
            "mkdir -p /work && cd /work\n"
            # matplotlib is the only non-stdlib dep we ever auto-inject; the
            # python:3.12-slim base doesn't ship it. Install quietly, suppress
            # pip's noisy output so it doesn't drown the user's stdout.
            "pip install --quiet --disable-pip-version-check matplotlib >/dev/null 2>&1 || true\n"
            "cat > /work/snippet.py <<'OSB_SNIPPET_EOF_9F2A'\n"
            f"{snippet}\n"
            "OSB_SNIPPET_EOF_9F2A\n"
            "cd /work && python snippet.py\n"
        )
        execution = await sandbox.commands.run(wrapped)
        stdout = _flatten(execution.logs.stdout if execution.logs else None)
        stderr = _flatten(execution.logs.stderr if execution.logs else None)
        exit_code = execution.exit_code if execution.exit_code is not None else -1

        clean_stdout, chart_b64 = extract_chart(stdout)
        # Trim to keep responses sane; the chart is the real payload.
        out["stdout"] = clean_stdout[-8000:]
        out["stderr"] = stderr[-4000:]
        out["chart_b64"] = chart_b64
        out["exit_code"] = exit_code

    out["duration_s"] = round(time.monotonic() - t0, 2)
    return out


async def main() -> int:
    snippet_path = os.environ.get("SNIPPET_PATH")
    if not snippet_path:
        print(json.dumps({"error": "SNIPPET_PATH env var is required"}))
        return 2
    snippet = Path(snippet_path).read_text(encoding="utf-8")
    if not snippet.strip():
        print(json.dumps({"error": "snippet is empty"}))
        return 2

    try:
        # Hard wall-clock cap — defensive in case the SDK hangs on a bad VM.
        result = await asyncio.wait_for(run_once(snippet), timeout=EXEC_TIMEOUT_S)
    except asyncio.TimeoutError:
        result = {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"exec timeout after {EXEC_TIMEOUT_S}s",
            "chart_b64": None,
            "duration_s": float(EXEC_TIMEOUT_S),
            "sandbox_id": None,
            "image": SANDBOX_IMAGE,
            "error": "timeout",
        }
    except Exception as exc:
        result = {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "chart_b64": None,
            "duration_s": 0.0,
            "sandbox_id": None,
            "image": SANDBOX_IMAGE,
            "error": str(exc),
        }

    # Final JSON line — portal parses the LAST line of stdout as the result.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
