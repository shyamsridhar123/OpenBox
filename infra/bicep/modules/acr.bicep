// modules/acr.bicep — Azure Container Registry Premium
// Plan reference: Phase 1 Task 1.3 — ACR Premium, private endpoint, zone redundancy,
//   AdminUser disabled, soft-delete, geo-replication ready (no replicas in v1).

targetScope = 'resourceGroup'

param env string
param location string
param privateEndpointSubnetId string
param vnetId string
param lawId string

// ACR name cannot contain hyphens — use concatenation
var acrName = 'acropensandbox${env}'

// ---------------------------------------------------------------------------
// ACR Premium
// API version 2023-07-01 is stable for ACR.
// ---------------------------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Premium'
  }
  properties: {
    adminUserEnabled: false
    zoneRedundancy: 'Enabled'
    publicNetworkAccess: 'Disabled'
    networkRuleBypassOptions: 'AzureServices'
    // Policies block: correct location per Microsoft.ContainerRegistry/registries API schema
    policies: {
      // Soft-delete for retention of deleted images
      // BCP037: Bicep type defs incomplete for Policies — softDeletePolicy IS valid per ARM schema
      #disable-next-line BCP037
      softDeletePolicy: {
        status: 'enabled'
        retentionDays: 7
      }
      // Quarantine policy: images must pass Notation/Ratify before promotion
      #disable-next-line BCP037
      quarantinePolicy: {
        status: 'enabled'
      }
    }
    // Geo-replication: no replicas in v1; add replicas here for v1.5
    // replications: []
  }
}

// ---------------------------------------------------------------------------
// Private DNS Zone for ACR
// ---------------------------------------------------------------------------

resource acrPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.azurecr.io'
  location: 'global'
}

resource acrDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: acrPrivateDnsZone
  name: 'link-acr-${env}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

// ---------------------------------------------------------------------------
// Private Endpoint on snet-pe
// ---------------------------------------------------------------------------

resource acrPe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-acr-opensandbox-${env}'
  location: location
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'pe-acr-conn-${env}'
        properties: {
          privateLinkServiceId: acr.id
          groupIds: ['registry']
        }
      }
    ]
  }
}

resource acrPeDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: acrPe
  name: 'acrDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-azurecr-io'
        properties: {
          privateDnsZoneId: acrPrivateDnsZone.id
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Diagnostic settings → LAW
// ---------------------------------------------------------------------------

resource acrDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-acr-${env}'
  scope: acr
  properties: {
    workspaceId: lawId
    logs: [
      { category: 'ContainerRegistryRepositoryEvents', enabled: true }
      { category: 'ContainerRegistryLoginEvents', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output acrId string = acr.id
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
