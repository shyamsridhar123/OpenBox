"""Persist the last cluster start/stop action to a single-row JSON file.

The audit (P0-6) showed that the Cluster Lifecycle card always rendered
`Last action: —`. The portal had no audit trail for the most safety-
critical card (one click = $X/hr Azure compute). This module gives main.py
a tiny, atomic, single-row persistence layer so the UI can show
`Last action: Stop · 2h ago · shyamsridhar@microsoft.com`.

Atomicity: write to a tempfile in the same directory then os.replace().
That guarantees readers never see a half-written file even if the API is
killed mid-write.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path is set lazily by ensure_data_dir() so tests can override REPO_ROOT.
_HISTORY_FILE_NAME = "cluster-history.json"


def _history_path() -> Path:
    from .config import settings

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _HISTORY_FILE_NAME


def read_last_action() -> dict[str, Any] | None:
    """Return the persisted last-action record, or None if none yet."""
    path = _history_path()
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("cluster_history read failed: %s", exc)
        return None


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write `payload` to `path` atomically via tempfile + os.replace."""
    fd, tmp_name = tempfile.mkstemp(
        prefix=".cluster-history-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def record_action_started(
    action: str,
    actor: str | None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Record the *start* of a cluster action.

    Args:
        action: 'Start' or 'Stop' (capitalised — the UI renders verbatim).
        actor: az_user from /api/identity; may be None.
        started_at: ISO8601 timestamp. Defaults to now() in UTC.
    """
    if action not in ("Start", "Stop"):
        raise ValueError(f"action must be 'Start' or 'Stop', got {action!r}")

    payload = {
        "last_action": action,
        "last_action_at": started_at or datetime.now(timezone.utc).isoformat(),
        "last_actor": actor or "",
        "outcome": "in_progress",
        "duration_s": 0.0,
        # Internal: monotonic clock at start so the completer can compute
        # duration without trusting wall-clock timestamps.
        "_started_monotonic": time.monotonic(),
    }
    try:
        _atomic_write(_history_path(), payload)
    except Exception as exc:
        logger.warning("cluster_history write failed: %s", exc)
    return payload


def record_action_completed(
    outcome: str,
    duration_s: float | None = None,
) -> dict[str, Any] | None:
    """Update the existing in-progress record with outcome+duration."""
    if outcome not in ("success", "failed"):
        raise ValueError(f"outcome must be 'success' or 'failed', got {outcome!r}")

    current = read_last_action()
    if current is None:
        # Nothing to complete; ignore silently.
        return None

    if duration_s is None:
        started_mono = current.get("_started_monotonic")
        if isinstance(started_mono, (int, float)):
            duration_s = max(0.0, time.monotonic() - float(started_mono))
        else:
            duration_s = 0.0

    current["outcome"] = outcome
    current["duration_s"] = round(float(duration_s), 3)
    # Strip the internal sentinel — readers never need it.
    current.pop("_started_monotonic", None)

    try:
        _atomic_write(_history_path(), current)
    except Exception as exc:
        logger.warning("cluster_history complete write failed: %s", exc)
    return current
