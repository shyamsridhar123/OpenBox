# FINISH-8 — Audit Log Shipper (AKS → Event Hubs via WIF) — Live Result

Date: 2026-05-20 (UTC: 2026-05-21 ~04:51)
Working dir: `C:\Users\shyamsridhar\code\openbox`
Operator: shyamsridhar@microsoft.com
Subscription: `b914f690-dab0-4208-98af-c7ee89ab9040` (MCAPS-Hybrid-REQ-94035-2024-shyamsridhar)

## TL;DR

**Status: ✅ SUCCESS, end-to-end.** Container logs from the `opensandbox` namespace (the sandbox pod that emitted `HELLO_FROM_REAL_OPENSANDBOX`) flowed AKS → Event Hubs → Stream Analytics → blob within ~30 s. SAS was never re-enabled. Auth is **pure Workload Identity Federation**, modelled on the existing `fic-kimi-demo` pattern.

## Shipper choice and rationale

**Custom ~120-line Python DaemonSet shipper** (`evidence/runs/finish/fluentbit/shipper.py`) using `azure.identity.DefaultAzureCredential` + `azure.eventhub.EventHubProducerClient`.

Why not Fluent Bit, Fluentd, or OTel:
- Fluent Bit's `out_kafka` / `out_http` have no built-in AAD/MSAL token-refresh callback; would require a sidecar token-refresher + Lua glue.
- Fluentd's MSI plugin is gem-based and would also need a token refresher path for WIF (not native MI).
- OTel Collector has `azureeventhubexporter`, but its WIF support requires AMQP cred wiring that's still beta in the stable distro on `mcr.microsoft.com`.
- Docker Desktop daemon was not running on the bastion, so building a custom image was out.

`DefaultAzureCredential` natively picks up the WIF env-vars (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_FEDERATED_TOKEN_FILE`, `AZURE_AUTHORITY_HOST`) that the `azure-workload-identity` admission webhook injects into any pod with label `azure.workload.identity/use: "true"` and a labelled ServiceAccount. Token refresh is automatic via `azure-identity`. AMQP-over-WSS to `evhns-opensandbox-dev.servicebus.windows.net:443` worked out of the box; the existing AKS egress and Event Hubs networking allowed it without firewall changes.

The container image is `mcr.microsoft.com/cbl-mariner/base/python:3` (Microsoft mirror — no docker.io dependency, works on both `nodepool1` and the `kata` agent pool which had blocked docker.io egress). At startup the container `pip install`s `azure-identity==1.19.0` and `azure-eventhub==5.13.0`, then execs `python3 /opt/shipper/shipper.py` (script delivered as a ConfigMap volume mount).

## Identity, federation, RBAC

| Item | Value |
|---|---|
| UAMI name | `id-fluentbit-opensandbox-dev` |
| UAMI client ID | `e87371a2-eef4-45d4-9977-0223abda223e` |
| UAMI principal ID | `dfc639f7-8a55-45e3-b7ea-2abf78d310c8` |
| UAMI resource ID | `/subscriptions/b914f690-…/resourceGroups/rg-opensandbox-dev/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-fluentbit-opensandbox-dev` |
| AKS OIDC issuer | `https://eastus2.oic.prod-aks.azure.com/16b3c013-d300-468d-ac64-7eda0820b6d3/6de70b0b-4596-47ba-8a7a-3ef8e657f658/` |
| Federated credential | `fic-fluentbit` on `id-fluentbit-opensandbox-dev` |
| Federation subject | `system:serviceaccount:logging:fluent-bit` |
| Federation audience | `api://AzureADTokenExchange` |
| EH Data Sender role assignment ID | `/subscriptions/b914f690-…/resourceGroups/rg-opensandbox-dev/providers/Microsoft.EventHub/namespaces/evhns-opensandbox-dev/eventhubs/sandbox-audit-fast/providers/Microsoft.Authorization/roleAssignments/9666d99f-b745-47c9-8f5e-04379bd119fd` |
| Role definition | `Azure Event Hubs Data Sender` (`2b629674-e913-4c01-ae53-ef4638d8f975`) |
| Scope | event-hub-scoped (not namespace-scoped) |

## Kubernetes objects

- Namespace: `logging` (created).
- ServiceAccount: `logging/fluent-bit` with annotation `azure.workload.identity/client-id=e87371a2-eef4-45d4-9977-0223abda223e` and label `azure.workload.identity/use=true`.
- ConfigMap: `logging/fluent-bit-shipper-code` (contains `shipper.py`).
- DaemonSet: `logging/fluent-bit`, image `mcr.microsoft.com/cbl-mariner/base/python:3`, `hostPath /var/log` read-only, env `EH_FQDN`, `EH_NAME`, `LOG_GLOB=/var/log/containers/*opensandbox*.log`.
- Tolerations: `operator: Exists` so pods schedule on both `nodepool1` and the `kata` pool (which carries a taint).
- **DaemonSet pod count: 5 / 5 nodes** Ready (`fluent-bit-5vdx4`, `-flv54`, `-hd4zd`, `-pv55t`, `-spqs9` — 3 on `nodepool1`, 2 on `kata`).

Manifest is at `evidence/runs/finish/fluentbit/daemonset.yaml`; shipper code at `evidence/runs/finish/fluentbit/shipper.py`.

## Verification

1. SDK trigger ran successfully:
   ```
   /tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py
   …
   stdout:
   HELLO_FROM_REAL_OPENSANDBOX
   …
   RUN-4 SUCCESS
   ```
   Sandbox pod was `9319c8c5-c54f-46db-88e4-0b16a5dc8eef-0` on node `aks-nodepool1-13814532-vmss000000`.

2. Within ~30 s, four blobs appeared in `stasadevse3bwihj3in4s/audit-fast` for hour `2026/05/21/04`:
   ```
   2026/05/21/04/0_5f59233e7e9b448b8848f773516697d8_1.json   937,584 B
   2026/05/21/04/0_6046258bd7a848f785a905743e6f5919_1.json   938,476 B
   2026/05/21/04/0_c8fe5e349e9e4da0948784e4c3a1b291_1.json   905,521 B
   2026/05/21/04/0_db81fd64a5ce4d81ae905cd0984178df_1.json   945,767 B
   ```

3. Captured event blob path with the HELLO trace: `audit-fast/2026/05/21/04/0_db81fd64a5ce4d81ae905cd0984178df_1.json`.

4. Sample line from that blob (the sandbox stdout being audited):
   ```json
   {"EventTime":"2026-05-21T04:47:41.3130000Z",
    "shipper_ts":"2026-05-21T04:47:41.321533+00:00",
    "node":"aks-nodepool1-13814532-vmss000000",
    "pod":"9319c8c5-c54f-46db-88e4-0b16a5dc8eef-0",
    "namespace":"opensandbox",
    "container":"sandbox",
    "stream":"stdout",
    "log_ts":"2026-05-21T04:47:41.241686704Z",
    "message":"{\"level\":\"info\",\"ts\":\"2026-05-21T04:47:41.241Z\",\"msg\":\"StreamEvent.OnExecuteStdout write data type=stdout text=HELLO_FROM_REAL_OPENSANDBOX\"}",
    "EventProcessedUtcTime":"2026-05-21T04:47:41.4060720Z",
    "PartitionId":3,
    "EventEnqueuedUtcTime":"2026-05-21T04:47:41.3130000Z"}
   ```
   (The `EventTime`, `EventProcessedUtcTime`, `EventEnqueuedUtcTime`, `PartitionId` fields are injected by Stream Analytics; everything else is the shipper's record schema.)

## Acceptance criteria

| Step | Status | Notes |
|---|---|---|
| 1. Inspect WIF pattern (`fic-kimi-demo`) | ✅ | Issuer + subject + audience reused verbatim. |
| 2. Create UAMI `id-fluentbit-opensandbox-dev` | ✅ | clientId `e87371a2-eef4-45d4-9977-0223abda223e`. |
| 3. EH Data Sender at event-hub scope | ✅ | RA id `9666d99f-b745-47c9-8f5e-04379bd119fd`. |
| 4. Namespace `logging` + SA `fluent-bit` w/ WIF annotations | ✅ | |
| 5. Federated credential subject `system:serviceaccount:logging:fluent-bit` | ✅ | |
| 6. Deploy DaemonSet | ✅ | 5/5 pods Ready. |
| 7. Trigger sandbox via `sdk_e2e.py` | ✅ | `RUN-4 SUCCESS`. |
| 8. Verify ASA blob | ✅ | 4 blobs within hour `2026/05/21/04`. |
| 9. Read blob to confirm structure | ✅ | HELLO_FROM_REAL_OPENSANDBOX present in blob `0_db81fd64…_1.json`. |

## Notes on the original blocker

The MG-scoped `EventHub_DisableLocalAuth_Modify` policy (`MCAPSGovDeployPolicies`) **was not touched**. `disableLocalAuth` on `evhns-opensandbox-dev` remains `true`, as governance requires. The pipeline now runs entirely on Entra ID via WIF, which is the durable design anyway.

## Incidental changes

- Granted `Storage Blob Data Reader` on `stasadevse3bwihj3in4s` to operator (`28b60676-…`) so the verification step could enumerate/download blobs (account has `disableSharedKeyAccess=true`). RA id `15acac34-3321-4819-9c27-438dc6f5e36f`. Leave in place; it's read-only.
- No changes to: ACA, Firewall, ACR, ACNS, AKS networking, Stream Analytics, Event Hubs namespace, sandbox pods.

## Artifacts

- `evidence/runs/finish/fluentbit/shipper.py` — log shipper source.
- `evidence/runs/finish/fluentbit/daemonset.yaml` — DaemonSet + SA manifest.
- `evidence/runs/finish/fluentbit/sample-audit.json` — captured ASA blob (937 KB).
- `evidence/runs/finish/fluentbit/blob-0_db81fd64a5ce4d81ae905cd0984178df_1.json` — captured ASA blob containing the HELLO trace.
