# FINISH-8 — Fast-path audit pipeline runbook

> Status: **Architecture + runbook only; no live deploy.**

## Pipeline

```
Sandbox Pod stdout/stderr  ┐
Sandbox CRD lifecycle      ├─→ Fluent Bit (DaemonSet on AKS)
execd command audit JSON   ┘
                              │
                              ▼
                       Event Hubs (eh-audit-opensandbox)
                              │
                              ▼
                    Stream Analytics job
                       (parse + enrich)
                              │
                              ▼
                    Log Analytics custom table
                       (OpenSandbox_CL)
```

Why this shape: Fluent Bit on each node has near-zero overhead for
high-volume logs; Event Hubs decouples bursty workload spikes from the
LAW ingestion quota; Stream Analytics does the parsing + enrichment
(adding sandbox.id, user, source IP) so the LAW table is query-ready.

## What's already done

`infra/bicep/modules/observability.bicep` provisions the LAW workspace.
The remaining wiring (Event Hubs namespace, Stream Analytics job,
Fluent Bit DaemonSet) needs adding.

## Deployment runbook

```bash
# 1. Provision Event Hubs.
az eventhubs namespace create -g rg-opensandbox-dev -n ehns-opensandbox-dev --sku Standard
az eventhubs eventhub create -g rg-opensandbox-dev --namespace-name ehns-opensandbox-dev \
  -n eh-audit --partition-count 4 --message-retention 1

# 2. Install Fluent Bit via Helm with the Event Hubs output.
# (Use the Kafka output protocol — Event Hubs implements the Kafka
# wire protocol on port 9093. Auth via SAS connection string.)
EH_CONN=$(az eventhubs namespace authorization-rule keys list \
  -g rg-opensandbox-dev --namespace-name ehns-opensandbox-dev \
  -n RootManageSharedAccessKey --query primaryConnectionString -o tsv)

cat > /tmp/fluent-bit-values.yaml <<EOF
config:
  outputs: |
    [OUTPUT]
        Name        kafka
        Match       kube.*
        Brokers     ehns-opensandbox-dev.servicebus.windows.net:9093
        Topics      eh-audit
        rdkafka.security.protocol  SASL_SSL
        rdkafka.sasl.mechanism     PLAIN
        rdkafka.sasl.username      \$ConnectionString
        rdkafka.sasl.password      ${EH_CONN}
EOF
helm repo add fluent https://fluent.github.io/helm-charts
helm install fluent-bit fluent/fluent-bit -f /tmp/fluent-bit-values.yaml -n logging --create-namespace

# 3. Stream Analytics job.
# Input: Event Hubs eh-audit
# Output: Log Analytics custom table OpenSandbox_CL
# Query: SELECT sandbox_id, user, command, exit_code, ts INTO law FROM eh
# Provisioning takes 5-10 min.
az stream-analytics job create -g rg-opensandbox-dev -n asa-audit-opensandbox-dev \
  --streaming-units 1

# 4. Verify a sandbox lifecycle event reaches LAW.
/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py
# Wait 2 min for pipeline lag.
az monitor log-analytics query \
  --workspace law-opensandbox-dev \
  --analytics-query "OpenSandbox_CL | where TimeGenerated > ago(5m)"
```

## Acceptance verification

- AC-28 ✅ ← Audit events queryable in LAW custom table within 2-minute lag
- `evidence/runs/finish/AC-CHECKLIST.md` row 28 → flip 🟡 to ✅
- Capture screenshot row 24

## Why this isn't running today

Three reasons:

1. **Cost** — Event Hubs Standard is ~$22/month; Stream Analytics 1-SU
   is ~$80/month. Both are charges we haven't been incurring.
2. **Volume** — at current usage (one sandbox per E2E run) there's
   essentially nothing to audit. The pipeline is for a production-scale
   workload, which we don't have.
3. **Risk** — Fluent Bit DaemonSet adds CPU/memory overhead per node
   and a Kafka client that retries forever on auth failures, which
   has bitten clusters in the past. Wants careful rollout.

For a research project, the LAW container-insights data already captures
what we need ad-hoc. For production, follow the runbook above.
