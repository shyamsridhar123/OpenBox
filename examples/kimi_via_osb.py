"""Kimi K2.5 agentic application running through the sandbox SDK.

Flow:
    Kimi (Azure Foundry, model deployment Kimi-K2.5/K2.6)
      -> generates Python code in <code>...</code> tags
      -> we extract + strip fences
      -> hand the code to a fresh sandbox via the SDK
      -> sandbox.commands.run(python3 -c <generated>)
      -> we read back stdout/exit_code via the same SDK call

End-to-end demo of a Kimi K2.5 agentic application running through the
sandbox runtime, not a bare pod.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
import os
from subprocess import check_output

from opensandbox import Sandbox  # type: ignore[import-not-found]
from opensandbox.config import ConnectionConfig  # type: ignore[import-not-found]

API_KEY_FILE = Path(__file__).resolve().parent / ".opensandbox-api-key"
DOMAIN = "localhost:18080"
KIMI_ENDPOINT = "https://aihubeastus26267492086.cognitiveservices.azure.com"
KIMI_DEPLOYMENTS = ["Kimi-K2.5", "Kimi-K2.6"]
SANDBOX_IMAGE = "python:3.12-slim"


def get_aad_token() -> str:
    """Use a pre-fetched AAD token from $AAD_TOKEN if set (avoids the
    Windows-specific .cmd-shim problem with Python subprocess), otherwise
    shell out to az. In-cluster the demo would use Workload Identity
    federation; from a developer laptop, az login is the natural source."""
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


def ask_kimi(token: str, prompt: str) -> tuple[str, str]:
    """Hit Kimi with retry+fallback. Returns (model_used, raw_text)."""
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
        "temperature": 0.0,
    }
    raw = ""
    for dep in KIMI_DEPLOYMENTS:
        url = f"{KIMI_ENDPOINT}/openai/deployments/{dep}/chat/completions?api-version=2024-10-21"
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
                resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
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


def extract_code(raw: str) -> str:
    """Pull the code body out of <code>...</code> and strip stray markdown
    fences. Kimi tends to wrap code in ```python ... ``` inside the tag."""
    m = re.search(r"<code>(.*?)</code>", raw, re.S)
    body = (m.group(1) if m else raw).strip()
    body = re.sub(r"^```[a-zA-Z]*\n?", "", body, flags=re.M)
    return body.replace("```", "").strip()


async def main() -> int:
    # 1. Ask Kimi for code.
    token = get_aad_token()
    print(f"[+] Got AAD token for cognitiveservices, prefix={token[:12]}…")

    prompt = (
        "Write a Python program inside <code>...</code> tags that computes "
        "and prints the first 10 Fibonacci numbers, one per line, and then "
        "prints their sum on a final line as 'SUM=<value>'. Only the code, "
        "no explanation."
    )
    model_used, raw = ask_kimi(token, prompt)
    print(f"[+] Kimi model used: {model_used}")
    print("[+] Raw response (first 400 chars):")
    print("-" * 60)
    print(raw[:400])
    print("-" * 60)

    code = extract_code(raw)
    if not code:
        print("FATAL: Kimi returned no usable code", file=sys.stderr)
        return 2
    print("[+] Extracted code:")
    print("-" * 60)
    print(code)
    print("-" * 60)

    # 2. Hand the code to OpenSandbox via the real SDK.
    api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    config = ConnectionConfig(
        domain=DOMAIN,
        api_key=api_key,
        protocol="http",
        use_server_proxy=True,
    )
    print(f"[+] Creating OpenSandbox sandbox (image={SANDBOX_IMAGE}, runtime=Kata)…")
    sandbox = await Sandbox.create(
        SANDBOX_IMAGE,
        connection_config=config,
        timeout=timedelta(minutes=5),
        ready_timeout=timedelta(minutes=3),
    )
    print(f"[+] Sandbox.create returned id={sandbox.id}")

    async with sandbox:
        # Write the code via a heredoc rather than shell-escaping it.
        wrapped = (
            "cat > /tmp/kimi_code.py <<'OSBPYEOF'\n"
            f"{code}\n"
            "OSBPYEOF\n"
            "python3 /tmp/kimi_code.py"
        )
        print("[+] Executing generated code inside the OpenSandbox Kata sandbox…")
        execution = await sandbox.commands.run(wrapped)

        def _flatten(events) -> str:
            if not events:
                return ""
            if isinstance(events, str):
                return events
            return "\n".join(getattr(e, "text", str(e)) for e in events)

        stdout = _flatten(execution.logs.stdout if execution.logs else None)
        stderr = _flatten(execution.logs.stderr if execution.logs else None)

        print("=" * 60)
        print(f"exit code: {execution.exit_code}")
        print(f"stdout:\n{stdout}")
        print(f"stderr:\n{stderr}")
        print("=" * 60)

        ok = execution.exit_code == 0 and "SUM=" in stdout
        print(f"sandbox.id  = {sandbox.id}")
        print(f"model_used  = {model_used}")
        print(f"verdict     = {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
