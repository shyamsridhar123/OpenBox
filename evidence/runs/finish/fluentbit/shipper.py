"""
Audit log shipper: tails /var/log/containers/*opensandbox*.log and sends each
line as an Event Hubs event using Workload Identity Federation.

Picks WIF up automatically via DefaultAzureCredential -> WorkloadIdentityCredential
(env vars AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_FEDERATED_TOKEN_FILE,
AZURE_AUTHORITY_HOST are injected by the azure-workload-identity webhook when
the pod carries label azure.workload.identity/use=true and uses a labelled SA).
"""
import glob
import json
import os
import socket
import time
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.eventhub import EventData, EventHubProducerClient

EH_FQDN = os.environ["EH_FQDN"]            # evhns-opensandbox-dev.servicebus.windows.net
EH_NAME = os.environ["EH_NAME"]            # sandbox-audit-fast
LOG_GLOB = os.environ.get("LOG_GLOB", "/var/log/containers/*opensandbox*.log")
NODE_NAME = os.environ.get("NODE_NAME", socket.gethostname())
POLL_SEC = float(os.environ.get("POLL_SEC", "1.0"))
BATCH_MAX = int(os.environ.get("BATCH_MAX", "50"))

cred = DefaultAzureCredential()
producer = EventHubProducerClient(
    fully_qualified_namespace=EH_FQDN,
    eventhub_name=EH_NAME,
    credential=cred,
)


def parse_container_filename(path):
    """K8s container log filename: <pod>_<ns>_<container>-<id>.log"""
    base = os.path.basename(path)
    if base.endswith(".log"):
        base = base[:-4]
    parts = base.split("_", 2)
    if len(parts) != 3:
        return {"pod": "", "namespace": "", "container": ""}
    pod, ns, rest = parts
    container = rest.rsplit("-", 1)[0]
    return {"pod": pod, "namespace": ns, "container": container}


def emit(producer, records):
    if not records:
        return
    batch = producer.create_batch()
    for rec in records:
        ev = EventData(json.dumps(rec, separators=(",", ":")))
        try:
            batch.add(ev)
        except ValueError:
            producer.send_batch(batch)
            batch = producer.create_batch()
            batch.add(ev)
    if len(batch) > 0:
        producer.send_batch(batch)


def main():
    print(f"[shipper] starting. fqdn={EH_FQDN} eh={EH_NAME} glob={LOG_GLOB} node={NODE_NAME}", flush=True)
    offsets = {}  # path -> (inode, byte_offset)
    while True:
        records = []
        files = glob.glob(LOG_GLOB)
        for path in files:
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue
            inode = st.st_ino
            prev = offsets.get(path)
            if prev is None or prev[0] != inode:
                # New or rotated file -> start at current end for new files, or 0 if first sight
                start_off = 0 if prev is None else 0
                offsets[path] = (inode, start_off)
            inode, off = offsets[path]
            if off > st.st_size:
                off = 0
            meta = parse_container_filename(path)
            try:
                with open(path, "rb") as f:
                    f.seek(off)
                    chunk = f.read(1024 * 1024)
                    new_off = f.tell()
            except FileNotFoundError:
                continue
            offsets[path] = (inode, new_off)
            if not chunk:
                continue
            for raw in chunk.splitlines():
                try:
                    line = raw.decode("utf-8", errors="replace")
                except Exception:
                    continue
                if not line.strip():
                    continue
                # CRI log format: "<ts> <stream> <P|F> <message>"
                ts = ""
                stream = ""
                msg = line
                sp = line.split(" ", 3)
                if len(sp) == 4 and sp[1] in ("stdout", "stderr"):
                    ts, stream, _, msg = sp
                rec = {
                    "shipper_ts": datetime.now(timezone.utc).isoformat(),
                    "node": NODE_NAME,
                    "pod": meta["pod"],
                    "namespace": meta["namespace"],
                    "container": meta["container"],
                    "stream": stream,
                    "log_ts": ts,
                    "message": msg,
                }
                records.append(rec)
                if len(records) >= BATCH_MAX:
                    emit(producer, records)
                    records = []
        if records:
            emit(producer, records)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            producer.close()
        except Exception:
            pass
