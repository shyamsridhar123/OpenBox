// modules/observability.bicep — LAW, App Insights, Event Hubs, Stream Analytics
// Plan reference: Phase 1 Task 1.6 (B-C3 fix) — audit log path:
//   Diagnostic Settings → Event Hubs → Stream Analytics → Log Analytics (SandboxAuditFast_CL)
//   for sub-60-s ingestion. Container Insights handles routine non-audit logs.
// Also: Defender-Kata-gap KQL alert (Critic S-C3 fix).

targetScope = 'resourceGroup'

param env string
param location string

@description('Log Analytics retention in days.')
param retentionDays int = 30

// ---------------------------------------------------------------------------
// Log Analytics Workspace — PerGB2018 pricing tier
// ---------------------------------------------------------------------------

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-opensandbox-${env}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: retentionDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Application Insights — workspace-based
// ---------------------------------------------------------------------------

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-opensandbox-${env}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Event Hubs Namespace + Event Hub (fast-path audit)
// ---------------------------------------------------------------------------

resource ehNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: 'evhns-opensandbox-${env}'
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
    capacity: 2
  }
  properties: {
    zoneRedundant: true
    isAutoInflateEnabled: true
    maximumThroughputUnits: 10
    kafkaEnabled: false
  }
}

resource ehAudit 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehNamespace
  name: 'sandbox-audit-fast'
  properties: {
    messageRetentionInDays: 1
    partitionCount: 4
    status: 'Active'
  }
}

// Authorization rule for Diagnostic Settings to send to Event Hub
resource ehDiagSendRule 'Microsoft.EventHub/namespaces/authorizationRules@2024-01-01' = {
  parent: ehNamespace
  name: 'diag-send'
  properties: {
    rights: ['Send']
  }
}

// Authorization rule for Stream Analytics to read from Event Hub
resource ehStreamAnalyticsRule 'Microsoft.EventHub/namespaces/eventhubs/authorizationRules@2024-01-01' = {
  parent: ehAudit
  name: 'stream-analytics-listen'
  properties: {
    rights: ['Listen']
  }
}

// ---------------------------------------------------------------------------
// Stream Analytics Job — input=EventHub, output=LAW custom table SandboxAuditFast_CL
// This is a stub; the transformation query must be finalized in Phase 1 Task 1.6.
// ---------------------------------------------------------------------------

resource streamAnalyticsJob 'Microsoft.StreamAnalytics/streamingjobs@2021-10-01-preview' = {
  name: 'asa-opensandbox-audit-${env}'
  location: location
  properties: {
    sku: { name: 'Standard' }
    eventsOutOfOrderPolicy: 'Adjust'
    eventsOutOfOrderMaxDelayInSeconds: 5
    compatibilityLevel: '1.2'
    // Query: passthrough from EventHub to LAW custom table
    // Finalize field mappings in Phase 1 before go-live.
    transformation: {
      name: 'MainTransformation'
      properties: {
        streamingUnits: 3
        query: '''
SELECT
    System.Timestamp() AS EventTime,
    *
INTO [audit-output]
FROM [audit-input]
'''
      }
    }
    inputs: [
      {
        name: 'audit-input'
        properties: {
          type: 'Stream'
          serialization: {
            type: 'Json'
            properties: {
              encoding: 'UTF8'
            }
          }
          datasource: {
            type: 'Microsoft.EventHub/EventHub'
            properties: {
              serviceBusNamespace: ehNamespace.name
              eventHubName: ehAudit.name
              consumerGroupName: '$Default'
              authenticationMode: 'ConnectionString'
              sharedAccessPolicyName: 'stream-analytics-listen'
              sharedAccessPolicyKey: ehStreamAnalyticsRule.listKeys().primaryKey
            }
          }
        }
      }
    ]
    outputs: [
      {
        name: 'audit-output'
        properties: {
          serialization: {
            type: 'Json'
            properties: {
              encoding: 'UTF8'
              format: 'LineSeparated'
            }
          }
          datasource: {
            #disable-next-line BCP036
            type: 'Microsoft.OperationalInsights/workspaces'
            properties: {
              workspaceId: law.properties.customerId
              workspaceKey: law.listKeys().primarySharedKey
              tableName: 'SandboxAuditFast_CL'
            }
          }
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Defender-Kata-gap KQL Scheduled Alert (Critic S-C3)
// Detects suspicious process activity on kata node logs (Defender doesn't assess Kata pods).
// ---------------------------------------------------------------------------

resource defenderKataGapAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-kata-proc-anomaly-${env}'
  location: location
  properties: {
    displayName: 'Kata Node Suspicious Process Activity'
    description: 'Compensating control for Defender-Kata coverage gap. Detects cgroup/kmod/proc anomalies in Kata node logs. See plan pre-mortem risk: Defender does not assess Kata pods.'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    scopes: [law.id]
    criteria: {
      allOf: [
        {
          query: '''
ContainerLog
| where Computer startswith "aks-kata-"
      and (LogEntry contains "/proc/" or LogEntry contains "kmod" or LogEntry contains "release_agent")
| summarize count_ = count() by Computer, _ResourceId, bin(TimeGenerated, 5m)
| where count_ > 3
'''
          timeAggregation: 'Count'
          threshold: 0
          operator: 'GreaterThan'
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: false
    // ACTION GROUP: wire to PagerDuty action group post-deploy
    // actions: { actionGroups: ['<action-group-id>'] }
  }
}

// ---------------------------------------------------------------------------
// Outputs consumed by other modules
// ---------------------------------------------------------------------------

output lawId string = law.id
output lawName string = law.name
output lawWorkspaceId string = law.properties.customerId
output appInsightsId string = appInsights.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
output ehNamespaceId string = ehNamespace.id
output ehNamespaceName string = ehNamespace.name
output ehAuditName string = ehAudit.name
output ehDiagSendRuleId string = ehDiagSendRule.id
