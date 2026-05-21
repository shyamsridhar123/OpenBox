"""SDK end-to-end — drives the sandbox Python SDK against the in-cluster
sandbox server reachable on localhost:18080 via kubectl port-forward.
Creates a Kata-isolated sandbox, runs commands, prints results.

Success = a sandbox pod reaches Running, executes our shell command, and
returns stdout via the SDK transport. This validates the end-to-end stack:
  SDK -> server (FastAPI) -> BatchSandbox CRD -> controller -> Kata pod ->
  execd init container (CRLF-clean v1.0.8) -> bootstrap.sh -> execd daemon
  -> command exec -> response back through the chain.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox  # type: ignore[import-not-found]
from opensandbox.config import ConnectionConfig  # type: ignore[import-not-found]

API_KEY_FILE = Path(__file__).resolve().parent / ".opensandbox-api-key"
DOMAIN = "localhost:18080"
IMAGE = "python:3.12-slim"


async def main() -> int:
    api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    if not api_key:
        print("FATAL: api key file empty", file=sys.stderr)
        return 2

    config = ConnectionConfig(
        domain=DOMAIN,
        api_key=api_key,
        protocol="http",
        # The SDK on this laptop reaches the server via kubectl port-forward,
        # but it can't dial pod IPs directly. use_server_proxy=True routes the
        # exec/file calls through the server so we never need pod-network access.
        use_server_proxy=True,
    )
    print(f"[+] Connecting to OpenSandbox server at http://{DOMAIN}")
    print(f"[+] Image: {IMAGE}")

    sandbox = await Sandbox.create(
        IMAGE,
        connection_config=config,
        timeout=timedelta(minutes=5),
        ready_timeout=timedelta(minutes=3),
    )
    print(f"[+] Sandbox.create returned, id={sandbox.id}")

    async with sandbox:
        print("[+] Inside async with — sandbox should be Running")
        cmd = "echo HELLO_FROM_REAL_OPENSANDBOX && uname -a && python3 -c 'print(2+2)'"
        execution = await sandbox.commands.run(cmd)

        # logs.stdout / logs.stderr come back as List[OutputMessage(text, timestamp, is_error)]
        # — the upstream execd protocol streams events; we flatten by joining text fields.
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

        if execution.exit_code == 0 and "HELLO_FROM_REAL_OPENSANDBOX" in stdout:
            print("RUN-4 SUCCESS")
            return 0
        print("RUN-4 FAIL — command did not return expected output", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
