"""
Kata MutatingAdmissionWebhook — forces sandbox Pods onto kata-vm-isolation runtime.

Fail-open: all errors return allowed=true with no patch.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response

app = FastAPI(title="kata-webhook", version="0.1.0")
logger = logging.getLogger("kata-webhook")
logging.basicConfig(level=logging.INFO)

KATA_RUNTIME = "kata-vm-isolation"
KATA_TOLERATION = {
    "key": "runtime",
    "operator": "Equal",
    "value": "kata",
    "effect": "NoSchedule",
}
KATA_NODE_LABEL_KEY = "sandbox.io/runtime"
KATA_NODE_LABEL_VALUE = "kata"


def _allow(uid: str, patch: list[dict] | None = None) -> dict:
    """Build an AdmissionReview response."""
    resp: dict[str, Any] = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": uid,
            "allowed": True,
        },
    }
    if patch:
        patch_bytes = json.dumps(patch).encode()
        resp["response"]["patchType"] = "JSONPatch"
        resp["response"]["patch"] = base64.b64encode(patch_bytes).decode()
    return resp


def _build_patch(pod_spec: dict, pod_meta: dict) -> list[dict]:
    ops: list[dict] = []

    # --- runtimeClassName ---
    if not pod_spec.get("runtimeClassName"):
        ops.append({"op": "add", "path": "/spec/runtimeClassName", "value": KATA_RUNTIME})

    # --- tolerations ---
    tolerations: list[dict] = pod_spec.get("tolerations") or []
    already_tolerated = any(
        t.get("key") == KATA_TOLERATION["key"]
        and t.get("value") == KATA_TOLERATION["value"]
        and t.get("effect") == KATA_TOLERATION["effect"]
        for t in tolerations
    )
    if not already_tolerated:
        if not tolerations:
            # spec.tolerations key doesn't exist yet — add the array
            ops.append({"op": "add", "path": "/spec/tolerations", "value": [KATA_TOLERATION]})
        else:
            ops.append({"op": "add", "path": "/spec/tolerations/-", "value": KATA_TOLERATION})

    # --- nodeSelector ---
    node_selector: dict = pod_spec.get("nodeSelector") or {}
    if node_selector.get(KATA_NODE_LABEL_KEY) != KATA_NODE_LABEL_VALUE:
        if not node_selector:
            ops.append(
                {
                    "op": "add",
                    "path": "/spec/nodeSelector",
                    "value": {KATA_NODE_LABEL_KEY: KATA_NODE_LABEL_VALUE},
                }
            )
        else:
            # Escape ~ and / in JSON Pointer per RFC 6901
            escaped_key = KATA_NODE_LABEL_KEY.replace("~", "~0").replace("/", "~1")
            ops.append(
                {
                    "op": "add",
                    "path": f"/spec/nodeSelector/{escaped_key}",
                    "value": KATA_NODE_LABEL_VALUE,
                }
            )

    return ops


@app.post("/mutate")
async def mutate(request: Request) -> Response:
    uid = "<unknown>"
    try:
        body = await request.json()
        req = body.get("request", {})
        uid = req.get("uid", uid)
        pod_meta: dict = req.get("object", {}).get("metadata", {})
        pod_spec: dict = req.get("object", {}).get("spec", {})

        pod_name: str = pod_meta.get("name") or pod_meta.get("generateName", "")
        labels: dict = pod_meta.get("labels") or {}

        # Skip conditions — idempotent / pool-managed pods
        if pod_spec.get("runtimeClassName"):
            logger.info("skip uid=%s: runtimeClassName already set", uid)
            return Response(content=json.dumps(_allow(uid)), media_type="application/json")

        if "pool" in labels:
            logger.info("skip uid=%s: pool label present", uid)
            return Response(content=json.dumps(_allow(uid)), media_type="application/json")

        if pod_name.startswith("kata-"):
            logger.info("skip uid=%s: pod name starts with kata-", uid)
            return Response(content=json.dumps(_allow(uid)), media_type="application/json")

        patch = _build_patch(pod_spec, pod_meta)
        logger.info("mutating uid=%s pod=%s ops=%d", uid, pod_name, len(patch))
        return Response(
            content=json.dumps(_allow(uid, patch if patch else None)),
            media_type="application/json",
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled error uid=%s: %s", uid, exc)
        # Fail-open: allow without patch
        return Response(content=json.dumps(_allow(uid)), media_type="application/json")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
