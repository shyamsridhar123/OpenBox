// modules/aks.bicep — Private AKS cluster, Cilium-ACNS, Kata node pool, Workload Identity
// Plan reference: Phase 1 Task 1.2; ADR — AKS+Kata runtime, ACNS, AAD-integrated,
//   Workload Identity, Availability Zones, Azure Policy add-on.
// NICE-1: AKS Kubernetes-RBAC bound to Entra groups via aadProfile.adminGroupObjectIDs.

targetScope = 'resourceGroup'

param env string
param location string
param aksAdminGroupObjectIds array
param systemSubnetId string
param kataSubnetId string
param lawId string
param acrId string

@description('Kubernetes version. Update as new stable versions are available.')
param kubernetesVersion string = '1.34'

// ---------------------------------------------------------------------------
// User-Assigned Managed Identity for AKS control plane
// ---------------------------------------------------------------------------

resource aksMi 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-aks-opensandbox-${env}'
  location: location
}

// ---------------------------------------------------------------------------
// Role assignment: AKS MI → AcrPull on ACR
// ---------------------------------------------------------------------------

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrId, aksMi.id, acrPullRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: aksMi.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// AKS Cluster
// API version 2024-09-01 is a recent stable GA version for AKS.
// ---------------------------------------------------------------------------

resource aks 'Microsoft.ContainerService/managedClusters@2024-09-01' = {
  name: 'aks-opensandbox-${env}'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${aksMi.id}': {}
    }
  }
  sku: {
    name: 'Base'
    tier: 'Standard'
  }
  properties: {
    kubernetesVersion: kubernetesVersion
    dnsPrefix: 'aks-opensandbox-${env}'

    // Private cluster
    apiServerAccessProfile: {
      enablePrivateCluster: true
      enablePrivateClusterPublicFQDN: false
    }

    // AAD integration — NICE-1: adminGroupObjectIDs provides Kubernetes-RBAC
    // bound to Entra groups. No user has cluster access without explicit group membership.
    aadProfile: {
      managed: true
      enableAzureRBAC: false
      adminGroupObjectIDs: aksAdminGroupObjectIds
      tenantID: subscription().tenantId
    }

    // Cilium-ACNS networking (plan consensus)
    networkProfile: {
      networkPlugin: 'azure'
      networkDataplane: 'cilium'
      networkPolicy: 'cilium'
      serviceCidr: '172.16.0.0/16'
      dnsServiceIP: '172.16.0.10'
      advancedNetworking: {
        enabled: true
      }
    }

    // OIDC issuer + Workload Identity
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }

    // Add-on profiles
    addonProfiles: {
      azurepolicy: {
        enabled: true
      }
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: {
          enableSecretRotation: 'true'
          rotationPollInterval: '2m'
        }
      }
    }

    // System node pool — D4ds_v5 has local SSD required for Ephemeral OS (128 GB).
    // Zones 2,3 only: eastus2 does not support zone 1 for managedClusters.
    agentPoolProfiles: [
      {
        name: 'system'
        mode: 'System'
        count: 3
        vmSize: 'Standard_D4ds_v5'
        osDiskType: 'Ephemeral'
        osDiskSizeGB: 128
        osType: 'Linux'
        osSKU: 'AzureLinux'
        vnetSubnetID: systemSubnetId
        availabilityZones: ['2', '3']
        enableAutoScaling: false
        nodeTaints: ['CriticalAddonsOnly=true:NoSchedule']
        nodeLabels: {}
        upgradeSettings: {
          maxSurge: '1'
        }
      }
      // Kata node pool — D8ds_v5 has local SSD required for Ephemeral OS (128 GB).
      // Trusted Launch (secureboot/vTPM) is incompatible with KataMshvVmIsolation;
      // isolation is provided by the Kata hypervisor. Zones 2,3 only.
      {
        name: 'kata'
        mode: 'User'
        count: 2
        minCount: 2
        maxCount: 10
        vmSize: 'Standard_D8ds_v5'
        osDiskType: 'Ephemeral'
        osDiskSizeGB: 128
        osType: 'Linux'
        osSKU: 'AzureLinux'
        vnetSubnetID: kataSubnetId
        availabilityZones: ['2', '3']
        enableAutoScaling: true
        workloadRuntime: 'KataMshvVmIsolation'
        nodeTaints: ['runtime=kata:NoSchedule']
        nodeLabels: {
          'sandbox.io/runtime': 'kata'
        }
        upgradeSettings: {
          maxSurge: '1'
        }
      }
    ]

    autoUpgradeProfile: {
      upgradeChannel: 'patch'
      nodeOSUpgradeChannel: 'NodeImage'
    }
  }

  dependsOn: [acrPullAssignment]
}

// ---------------------------------------------------------------------------
// Outputs consumed by other modules (observability diagnostic settings)
// ---------------------------------------------------------------------------

output aksClusterName string = aks.name
output aksClusterId string = aks.id
output aksMiPrincipalId string = aksMi.properties.principalId
output aksOidcIssuerUrl string = aks.properties.oidcIssuerProfile.issuerURL
output aksResourceId string = aks.id
