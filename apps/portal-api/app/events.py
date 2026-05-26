"""Events feed enrichment for /api/events.

Audit P0-5: the raw k8s event feed gave the loudest signal (red BACKOFF chip)
and explained nothing. The fix:

1. Stop truncating messages; the UI does its own wrapping.
2. Classify severity (info | warning | error) so the UI can colour-code.
3. Add a `human_message` — a natural-language translation of common k8s
   reasons so non-ops users understand what they're seeing.
4. Flag events that point at sandboxes the control-plane no longer knows
   about, so stale errors can be greyed out in the UI.

The translation table is data-driven — adding a new reason is one line.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# --- severity classification -------------------------------------------------

_ERROR_REASONS = frozenset({
    "BackOff",
    "FailedScheduling",
    "FailedCreatePodSandBox",
    "FailedAttachVolume",
    "FailedMount",
    "Failed",
    "Evicted",
    "OOMKilled",
})

_INFO_REASONS = frozenset({
    "Pulled",
    "Pulling",
    "Scheduled",
    "Started",
    "Created",
    "SuccessfulCreate",
    "SuccessfulDelete",
    "Killing",
})


def _severity_for(reason: str, ev_type: str) -> str:
    """Map (reason, type) → 'info' | 'warning' | 'error'."""
    if reason in _ERROR_REASONS:
        return "error"
    if reason in _INFO_REASONS:
        return "info"
    # Default by k8s event type: Warning → warning, Normal → info.
    return "warning" if ev_type.lower() == "warning" else "info"


# --- human-language translation ---------------------------------------------

# Each entry is keyed by event.reason. Templates support {pod} and {message}
# placeholders. Keep entries TERSE — the UI shows them next to the raw text.
_TRANSLATIONS: dict[str, str] = {
    "BackOff": (
        "Sandbox container exited (this is normal for /bin/bash entrypoints "
        "with no TTY)."
    ),
    "FailedScheduling": (
        "Kubernetes could not place this pod — likely no nodes match the "
        "required runtimeClass or resources."
    ),
    "Pulled": "Container image pulled successfully.",
    "Pulling": "Pulling container image from registry.",
    "Scheduled": "Pod placed onto a node.",
    "Started": "Container started.",
    "Created": "Container created.",
    "Killing": "Stopping container.",
    "Evicted": "Pod evicted by the kubelet (usually node pressure).",
    "OOMKilled": "Container killed: out of memory.",
    "FailedCreatePodSandBox": (
        "Pod sandbox creation failed — typically a container runtime or "
        "image-pull problem."
    ),
    "FailedAttachVolume": "Failed to attach a persistent volume to this pod.",
    "FailedMount": "Failed to mount a volume into the container.",
    "SuccessfulCreate": "Controller created a child resource.",
    "SuccessfulDelete": "Controller deleted a child resource.",
}


def translate(reason: str, message: str) -> str:
    """Return the human translation if known, else the raw message verbatim.

    Callers should always include the raw message somewhere in the UI as
    well — translation is best-effort and not authoritative.
    """
    return _TRANSLATIONS.get(reason, message)


# --- sandbox-id extraction --------------------------------------------------

# OpenSandbox names pods "{sandbox-uuid}-0". Pull the UUID prefix so we can
# cross-check against /api/sandboxes.
_POD_BASE_RE = re.compile(r"^([0-9a-f-]{8,})-\d+$")


def _sandbox_id_from(involved_object: dict[str, Any]) -> str | None:
    """Best-effort: extract the OpenSandbox UUID from a pod name."""
    if not isinstance(involved_object, dict):
        return None
    if involved_object.get("kind") != "Pod":
        return None
    name = str(involved_object.get("name") or "")
    m = _POD_BASE_RE.match(name)
    return m.group(1) if m else None


# --- public API -------------------------------------------------------------


def enrich_events(
    events: list[dict[str, Any]],
    live_sandbox_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Take raw events from K8sClient.list_events and return enriched copies.

    Args:
        events: list of {ts, reason, type, message, involved_object, count}.
        live_sandbox_ids: ids currently in /api/sandboxes. When provided,
            any event whose involved pod refers to a missing sandbox is
            tagged is_for_deleted_sandbox=True so the UI can grey it out.
    """
    live = live_sandbox_ids or set()
    out: list[dict[str, Any]] = []
    for ev in events:
        reason: str = str(ev.get("reason") or "")
        ev_type: str = str(ev.get("type") or "Normal")
        message: str = str(ev.get("message") or "")
        involved = ev.get("involved_object") or {}
        sb_id = _sandbox_id_from(involved) if isinstance(involved, dict) else None

        enriched = dict(ev)  # shallow copy preserves ts/count/etc.
        enriched["type"] = ev_type
        enriched["severity_class"] = _severity_for(reason, ev_type)
        enriched["human_message"] = translate(reason, message)
        # Surface pod/namespace explicitly so the UI doesn't have to dig.
        enriched["involved_object"] = {
            "kind": involved.get("kind", "") if isinstance(involved, dict) else "",
            "name": involved.get("name", "") if isinstance(involved, dict) else "",
            "namespace": involved.get("namespace", "") if isinstance(involved, dict) else "",
            "sandbox_id": sb_id,
        }
        # If we have a live-set and this event points at a sandbox NOT in it,
        # mark it stale. When live_sandbox_ids is None we can't tell, so
        # default to False (don't lie to the UI).
        if live_sandbox_ids is not None and sb_id is not None:
            enriched["is_for_deleted_sandbox"] = sb_id not in live
        else:
            enriched["is_for_deleted_sandbox"] = False
        out.append(enriched)
    return out
