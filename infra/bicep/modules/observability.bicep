// modules/observability.bicep — LAW, App Insights, Event Hubs, Stream Analytics
// Plan reference: Phase 1 Task 1.6 (B-C3 fix) — audit log path:
//   Diagnostic Settings → Event Hubs → Stream Analytics → Blob Storage (staging)
//   Log Analytics ingests from Blob via DCR/Data Collection in Phase 2.
//   Container Insights handles routine non-audit logs.
// Also: Defender-Kata-gap KQL alert (Critic S-C3 fix).
//
// NOTE: ASA does not support direct output to Log Analytics workspaces.
//   The original design (ASA → LAW custom table) is replaced with ASA → Blob staging.
//   Custom log ingestion into LAW can be achieved via the Logs Ingestion API / DCR post-deploy.

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
// Storage Account — ASA audit staging (ASA cannot output directly to LAW)
// Events land here; custom log ingestion via DCR/Logs Ingestion API in Phase 2.
// ---------------------------------------------------------------------------

resource asaStagingStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stasa${env}${substring(uniqueString(resourceGroup().id), 0, 13)}'
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource asaStagingContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${asaStagingStorage.name}/default/audit-fast'
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------
// Stream Analytics Job — input=EventHub, output=Blob staging
// ASA does not support Microsoft.OperationalInsights/workspaces as an output sink.
// ---------------------------------------------------------------------------

resource streamAnalyticsJob 'Microsoft.StreamAnalytics/streamingjobs@2021-10-01-preview' = {
  name: 'asa-opensandbox-audit-${env}'
  location: location
  properties: {
    sku: { name: 'Standard' }
    eventsOutOfOrderPolicy: 'Adjust'
    eventsOutOfOrderMaxDelayInSeconds: 5
    compatibilityLevel: '1.2'
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
            type: 'Microsoft.Storage/Blob'
            properties: {
              storageAccounts: [
                {
                  accountName: asaStagingStorage.name
                  accountKey: asaStagingStorage.listKeys().keys[0].value
                }
              ]
              container: 'audit-fast'
              pathPattern: '{date}/{time}'
              dateFormat: 'yyyy/MM/dd'
              timeFormat: 'HH'
            }
          }
        }
      }
    ]
  }
  dependsOn: [asaStagingContainer]
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
output asaStagingStorageName string = asaStagingStorage.name
