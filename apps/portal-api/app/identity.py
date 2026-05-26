from __future__ import annotations

import subprocess
from typing import Any

from .config import settings


def _run(cmd: list[str], *, shell: bool = False) -> str | None:
    """Run a CLI command and return stdout (stripped) on rc=0, else None.

    Windows quirk: the `az` CLI is shipped as `az.cmd`, a batch shim. When
    spawned via subprocess WITHOUT shell=True it fails to launch (Win32 cannot
    exec a .cmd directly), which is why /api/identity used to render the user
    as `—`. The fix mirrors apps/portal-api/app/clients.py:_run_az. Kubectl is
    a real .exe so we leave shell=False for it to avoid the usual shell-quoting
    footgun.
    """
    try:
        if shell:
            result = subprocess.run(
                " ".join(cmd),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                shell=True,
            )
        else:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except Exception:
        return None


def resolve_identity() -> dict[str, Any]:
    az_user = _run(["az", "account", "show", "--query", "user.name", "-o", "tsv"], shell=True)
    az_subscription_id = _run(["az", "account", "show", "--query", "id", "-o", "tsv"], shell=True)
    az_subscription_name = _run(["az", "account", "show", "--query", "name", "-o", "tsv"], shell=True)
    kubectx = _run(["kubectl", "config", "current-context"])

    key_file = settings.REPO_ROOT / "examples" / ".opensandbox-api-key"
    try:
        key_file_exists = key_file.exists()
    except Exception:
        key_file_exists = False

    return {
        "az_user": az_user,
        "az_subscription_id": az_subscription_id,
        "az_subscription_name": az_subscription_name,
        "kubectx": kubectx,
        "cluster_namespace": settings.OPENSANDBOX_NAMESPACE,
        "key_file_exists": key_file_exists,
        "repo_root": str(settings.REPO_ROOT),
    }
