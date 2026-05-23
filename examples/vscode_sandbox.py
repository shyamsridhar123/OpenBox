"""Spin up a VS Code sandbox on the cluster and print the proxy URL.

This is a DarkForge-side adapter for the OpenSandbox vscode example. The
upstream example at third_party/opensandbox/examples/vscode/main.py does not
set use_server_proxy=True, so the SDK tries to dial the pod IP directly from
our laptop and times out. We use the same pattern as hypothesis_swarm.py:
ConnectionConfig(use_server_proxy=True), which routes exec calls through
the in-cluster server (reachable via our kubectl port-forward).

Run:
    kubectl -n opensandbox-system port-forward svc/opensandbox-server 18080:80 &
    source .venv-demo/Scripts/activate
    python examples/vscode_sandbox.py

When this prints "VS Code Web endpoint: http://...", open that URL in a
browser. The sandbox stays alive for 10 minutes (Ctrl+C to exit sooner).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox  # type: ignore[import-not-found]
from opensandbox.config import ConnectionConfig  # type: ignore[import-not-found]
from opensandbox.models.execd import RunCommandOpts  # type: ignore[import-not-found]


DOMAIN = "localhost:18080"
IMAGE = "acropensandboxdemo7075.azurecr.io/sandbox/vscode:latest"
CODE_PORT = 8443
KEEPALIVE_SECONDS = 600

API_KEY_FILE = Path(__file__).resolve().parent / ".opensandbox-api-key"


async def main() -> int:
    api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    config = ConnectionConfig(
        domain=DOMAIN,
        api_key=api_key,
        protocol="http",
        use_server_proxy=True,
        request_timeout=timedelta(seconds=60),
    )

    print(f"[+] Creating VS Code sandbox (image={IMAGE})…")
    sandbox = await Sandbox.create(
        IMAGE,
        connection_config=config,
        timeout=timedelta(minutes=20),
        ready_timeout=timedelta(minutes=5),
    )
    print(f"[+] Sandbox ready: id={sandbox.id}")

    async with sandbox:
        print(f"[+] Starting code-server inside the sandbox on port {CODE_PORT}…")
        start_exec = await sandbox.commands.run(
            f"code-server --bind-addr 0.0.0.0:{CODE_PORT} --auth none /workspace",
            opts=RunCommandOpts(background=True),
        )
        # Print any startup logs available immediately.
        if start_exec.logs and start_exec.logs.stdout:
            for msg in start_exec.logs.stdout:
                print(f"[code-server] {msg.text}")
        if start_exec.logs and start_exec.logs.stderr:
            for msg in start_exec.logs.stderr:
                print(f"[code-server err] {msg.text}")

        endpoint = await sandbox.get_endpoint(CODE_PORT)
        print()
        print("=" * 70)
        print("VS Code Web endpoint (open in browser):")
        print(f"  http://{endpoint.endpoint}/")
        print("=" * 70)
        print()
        print(f"Sandbox alive for {KEEPALIVE_SECONDS}s. Press Ctrl+C to exit sooner.")

        try:
            await asyncio.sleep(KEEPALIVE_SECONDS)
        except KeyboardInterrupt:
            print("\n[+] Caught Ctrl+C — tearing down sandbox.")
        finally:
            print("[+] Cleaning up…")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
