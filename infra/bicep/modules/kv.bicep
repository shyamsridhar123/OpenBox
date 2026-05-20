// modules/kv.bicep — Key Vault with RBAC, private endpoint, purge protection,
//   soft-delete 90 days, TWO Notation signing certificates (notation-primary, notation-secondary).
//
// Plan reference: Phase 1 Task 1.3 (B-C4 fix) — IaC-enforced dual-cert Notation rotation.
// Pre-mortem #2: Ratify TrustPolicy must always reference TWO trustedCerts.
//   cert overlap ≥ 14 days is the OPERATOR / RUNBOOK responsibility:
//   - Mint new secondary at 21 days remaining lifetime of current primary.
//   - Remove old primary only at 7 days remaining lifetime.
//   - IaC here provisions both certs with a self-signed issuer.
//   - Real CA issuance (e.g., DigiCert via AKV Issuer) is an OPERATOR step — not automated here.
//   - A deployment script should assert BOTH certs exist before deployment is marked complete.

targetScope = 'resourceGroup'

param env string
param location string
param privateEndpointSubnetId string
param vnetId string
param lawId string

// ---------------------------------------------------------------------------
// Key Vault
// API version 2023-07-01 stable.
// ---------------------------------------------------------------------------

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-opensandbox-${env}'
  location: location
  properties: {
    sku: { family: 'A', name: 'premium' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true   // RBAC mode — no access policies
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
    enabledForDiskEncryption: false
    enabledForDeployment: false
    enabledForTemplateDeployment: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

// ---------------------------------------------------------------------------
// Notation signing certificates
//
// NOTE on cert validity overlap: The Bicep here provisions both certs with a
// self-signed issuer and a 365-day validity. The OPERATOR must ensure:
//   1. notation-secondary is minted when notation-primary has ≤ 21 days remaining.
//   2. notation-primary is retired (removed from Ratify TrustPolicy) only when
//      notation-secondary is fully propagated AND at ≤ 7 days remaining.
//   3. Minimum 14-day overlap between primary and secondary validity windows.
// This 14-day overlap cannot be mechanically enforced by Bicep alone because
// KV certificate expiry is calendar-time dependent; it IS enforced by the
// rotation runbook at runbooks/cert-rotation.md and verified by the canary CI
// test (see plan pre-mortem #2, Task 1.3 addition).
// ---------------------------------------------------------------------------

#disable-next-line BCP081
resource notationCertPrimary 'Microsoft.KeyVault/vaults/certificates@2023-07-01' = {
  parent: kv
  name: 'notation-primary'
  properties: {
    certificatePolicy: {
      keyProperties: {
        exportable: false
        keyType: 'EC'
        curve: 'P-256'
        reuseKey: false
      }
      secretProperties: {
        contentType: 'application/x-pem-file'
      }
      x509CertificateProperties: {
        subject: 'CN=notation-primary-opensandbox-${env}'
        validityInMonths: 12
        keyUsage: ['digitalSignature']
        ekus: ['1.3.6.1.5.5.7.3.3']  // codeSigning EKU
      }
      issuerParameters: {
        name: 'Self'  // Self-signed; operator replaces with trusted CA issuer in production
        certificateType: 'Unknown'
      }
      attributes: {
        enabled: true
      }
      lifetimeActions: [
        {
          trigger: {
            daysBeforeExpiry: 21
          }
          action: {
            actionType: 'EmailContacts'  // Alert operator; DO NOT auto-renew to avoid silent rotation
          }
        }
      ]
    }
  }
}

#disable-next-line BCP081
resource notationCertSecondary 'Microsoft.KeyVault/vaults/certificates@2023-07-01' = {
  parent: kv
  name: 'notation-secondary'
  properties: {
    certificatePolicy: {
      keyProperties: {
        exportable: false
        keyType: 'EC'
        curve: 'P-256'
        reuseKey: false
      }
      secretProperties: {
        contentType: 'application/x-pem-file'
      }
      x509CertificateProperties: {
        subject: 'CN=notation-secondary-opensandbox-${env}'
        validityInMonths: 12
        keyUsage: ['digitalSignature']
        ekus: ['1.3.6.1.5.5.7.3.3']
      }
      issuerParameters: {
        name: 'Self'
        certificateType: 'Unknown'
      }
      attributes: {
        enabled: true
      }
      lifetimeActions: [
        {
          trigger: {
            daysBeforeExpiry: 21
          }
          action: {
            actionType: 'EmailContacts'
          }
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Private DNS Zone for Key Vault
// ---------------------------------------------------------------------------

resource kvPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
}

resource kvDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: kvPrivateDnsZone
  name: 'link-kv-${env}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

// ---------------------------------------------------------------------------
// Private Endpoint
// ---------------------------------------------------------------------------

resource kvPe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-kv-opensandbox-${env}'
  location: location
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [
      {
        name: 'pe-kv-conn-${env}'
        properties: {
          privateLinkServiceId: kv.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource kvPeDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: kvPe
  name: 'kvDnsZoneGroup'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-vaultcore-azure-net'
        properties: {
          privateDnsZoneId: kvPrivateDnsZone.id
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Diagnostic settings → LAW
// ---------------------------------------------------------------------------

resource kvDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-kv-${env}'
  scope: kv
  properties: {
    workspaceId: lawId
    logs: [
      { category: 'AuditEvent', enabled: true }
      { category: 'AzurePolicyEvaluationDetails', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output kvId string = kv.id
output kvName string = kv.name
output kvUri string = kv.properties.vaultUri
output notationPrimaryCertName string = notationCertPrimary.name
output notationSecondaryCertName string = notationCertSecondary.name
