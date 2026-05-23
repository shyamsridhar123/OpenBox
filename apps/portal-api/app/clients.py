from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ControlPlaneClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def list_sandboxes(self) -> list[dict[str, Any]] | dict[str, str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/sandboxes",
                    headers={"OPEN-SANDBOX-API-KEY": self._api_key},
                )
                if resp.status_code != 200:
                    return {"error": f"control-plane returned {resp.status_code}"}
                data = resp.json()
                if isinstance(data, list):
                    return data
                # some shapes wrap in a key
                if isinstance(data, dict):
                    for key in ("sandboxes", "items", "data"):
                        if key in data and isinstance(data[key], list):
                            return data[key]
                return {"error": "unexpected control-plane response shape"}
        except httpx.ConnectError as exc:
            return {"error": f"cannot reach control plane: {exc}"}
        except Exception as exc:
            logger.exception("control plane error")
            return {"error": str(exc)}


class K8sClient:
    def __init__(self, namespace: str) -> None:
        self._namespace = namespace

    async def list_pods(self) -> list[dict[str, Any]]:
        try:
            from kubernetes_asyncio import client, config  # type: ignore[import]
            await config.load_kube_config()
            v1 = client.CoreV1Api()
            pod_list = await v1.list_namespaced_pod(self._namespace)
            result: list[dict[str, Any]] = []
            for pod in pod_list.items:
                result.append({
                    "pod_name": pod.metadata.name if pod.metadata else "",
                    "node_name": pod.spec.node_name if pod.spec else None,
                    "runtime_class": (
                        pod.spec.runtime_class_name if pod.spec else None
                    ),
                    "phase": pod.status.phase if pod.status else None,
                    "labels": pod.metadata.labels or {} if pod.metadata else {},
                })
            await v1.api_client.close()
            return result
        except Exception as exc:
            logger.warning("k8s pod listing failed: %s", exc)
            return []

    async def list_nodes(self) -> list[dict[str, Any]]:
        try:
            from kubernetes_asyncio import client, config  # type: ignore[import]
            await config.load_kube_config()
            v1 = client.CoreV1Api()
            node_list = await v1.list_node()
            result: list[dict[str, Any]] = []
            for node in node_list.items:
                result.append({
                    "name": node.metadata.name if node.metadata else "",
                })
            await v1.api_client.close()
            return result
        except Exception as exc:
            logger.warning("k8s node listing failed: %s", exc)
            return []
