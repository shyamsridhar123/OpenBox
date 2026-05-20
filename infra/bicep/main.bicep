// main.bicep — OpenSandbox-on-Azure root deployment
// Plan reference: RALPLAN-DR Summary, Phase 1 (Tasks 1.1-1.6), ADR (FINAL)
// Scope: subscription — creates resource group then delegates to all modules.

targetScope = 'subscription'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Environment name (dev | prod).')
@allowed(['dev', 'prod'])
param env string = 'dev'

@description('Azure region for all resources.')
param location string = 'eastus2'

@description('AAD group object IDs that receive cluster-admin (AKS Kubernetes-RBAC).')
param aksAdminGroupObjectIds array

@description('Egress enforcement tier. "standard" = Cilium L7 + Firewall L3/L4 backup. "premium" = Firewall Premium as primary L7 enforcer (Phase 0 fallback).')
@allowed(['standard', 'premium'])
param egressEnforcementTier string = 'standard'

@description('AAD-integrated AKS server application ID (used for OBO audience). Obtain from cluster aadProfile.serverAppID after first deploy or from Entra app registration.')
param aksServerAppId string = ''

@description('Entra app IDs — populated manually after running az ad app create commands in modules/entra.bicep comments.')
param apiAppId string = ''
param portalAppId string = ''

@description('Log Analytics retention days.')
param lawRetentionDays int = 30

@description('Admin email for Kata/sandbox alerts (reserved for future use — wire to action group when PagerDuty is configured).')
#disable-next-line no-unused-params
param kataAdminEmail string = ''

@description('Name of an EXISTING ACR to reference instead of creating a new one. Only used when acrExisting=true.')
param acrName string = ''

@description('Resource group containing the existing ACR. Only used when acrExisting=true.')
param acrResourceGroup string = 'rg-opensandbox-demo'

@description('When true, reference an existing ACR (acrName/acrResourceGroup) instead of creating a new one.')
param acrExisting bool = false

// ---------------------------------------------------------------------------
// Resource Group
// ---------------------------------------------------------------------------

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-opensandbox-${env}'
  location: location
}

// ---------------------------------------------------------------------------
// Module: Observability (creates LAW first — other modules depend on its ID)
// ---------------------------------------------------------------------------

module observability 'modules/observability.bicep' = {
  name: 'observability'
  scope: rg
  params: {
    env: env
    location: location
    retentionDays: lawRetentionDays
  }
}

// ---------------------------------------------------------------------------
// Module: Network
// ---------------------------------------------------------------------------

module network 'modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    env: env
    location: location
  }
}

// ---------------------------------------------------------------------------
// Module: Firewall
// ---------------------------------------------------------------------------

module firewall 'modules/firewall.bicep' = {
  name: 'firewall'
  scope: rg
  params: {
    env: env
    location: location
    egressEnforcementTier: egressEnforcementTier
    firewallSubnetId: network.outputs.firewallSubnetId
    lawId: observability.outputs.lawId
  }
}

// ---------------------------------------------------------------------------
// Module: ACR
// When acrExisting=true, reference an existing ACR (e.g. acropensandboxdemo7075 in rg-opensandbox-demo)
// instead of creating a new one. This avoids duplicate ACR creation for demo environments.
// ---------------------------------------------------------------------------

module acr 'modules/acr.bicep' = if (!acrExisting) {
  name: 'acr'
  scope: rg
  params: {
    env: env
    location: location
    privateEndpointSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    lawId: observability.outputs.lawId
  }
}

// Reference existing ACR (when acrExisting=true). Uses a resource group scope lookup.
resource existingAcr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = if (acrExisting) {
  name: acrName
  scope: resourceGroup(acrResourceGroup)
}

// ---------------------------------------------------------------------------
// Module: Key Vault
// ---------------------------------------------------------------------------

module kv 'modules/kv.bicep' = {
  name: 'kv'
  scope: rg
  params: {
    env: env
    location: location
    privateEndpointSubnetId: network.outputs.peSubnetId
    vnetId: network.outputs.vnetId
    lawId: observability.outputs.lawId
  }
}

// ---------------------------------------------------------------------------
// Module: Entra roles (subscription-scoped role definitions)
// ---------------------------------------------------------------------------

module entra 'modules/entra.bicep' = {
  name: 'entra'
  params: {
    apiAppId: apiAppId
    portalAppId: portalAppId
  }
}

// ---------------------------------------------------------------------------
// Module: AKS
// ---------------------------------------------------------------------------

module aks 'modules/aks.bicep' = {
  name: 'aks'
  scope: rg
  params: {
    env: env
    location: location
    aksAdminGroupObjectIds: aksAdminGroupObjectIds
    systemSubnetId: network.outputs.systemSubnetId
    kataSubnetId: network.outputs.kataSubnetId
    lawId: observability.outputs.lawId
    // BCP318: conditional null safe — exactly one of existingAcr or acr module is active at deploy time
    #disable-next-line BCP318
    acrId: acrExisting ? existingAcr.id : acr.outputs.acrId!
  }
}

// ---------------------------------------------------------------------------
// Module: App Gateway + WAF
// ---------------------------------------------------------------------------

module appgw 'modules/appgw.bicep' = {
  name: 'appgw'
  scope: rg
  params: {
    env: env
    location: location
    appgwSubnetId: network.outputs.appgwSubnetId
    acaEnvStaticIp: aca.outputs.acaEnvStaticIp
    lawId: observability.outputs.lawId
  }
}

// ---------------------------------------------------------------------------
// Module: ACA environment + apps
// ---------------------------------------------------------------------------

module aca 'modules/aca.bicep' = {
  name: 'aca'
  scope: rg
  params: {
    env: env
    location: location
    acaSubnetId: network.outputs.acaSubnetId
    lawId: observability.outputs.lawId
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    portalAppId: portalAppId
    aksServerAppId: aksServerAppId
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output resourceGroupName string = rg.name
output aksClusterName string = aks.outputs.aksClusterName
// BCP318: conditional null safe — exactly one branch active at deploy time
#disable-next-line BCP318
output acrLoginServer string = acrExisting ? existingAcr.properties.loginServer : acr.outputs.acrLoginServer!
output lawId string = observability.outputs.lawId
output acaEnvStaticIp string = aca.outputs.acaEnvStaticIp
output firewallPrivateIp string = firewall.outputs.firewallPrivateIp
