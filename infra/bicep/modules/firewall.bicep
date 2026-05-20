// modules/firewall.bicep — Azure Firewall (Standard or Premium) with egress allowlist
// Plan reference: Phase 1 Task 1.5 (B-C1 fix) — conditional SKU based on egressEnforcementTier.
//
// Decision logic (set via parameter egressEnforcementTier):
//   "standard" → Phase 0 task 0.4 PASSED: Cilium L7 handles sandbox egress; Firewall = L3/L4 backup.
//                Uses network rules + FQDN tags where supported.
//   "premium"  → Phase 0 task 0.4 FAILED: Firewall Premium is primary L7 enforcer.
//                Uses application rules with SNI-based HTTPS filtering (no TLS MITM inside Kata).
//
// Allowlist FQDNs (package managers): pypi.org, files.pythonhosted.org,
//   registry.npmjs.org, proxy.golang.org, sum.golang.org

targetScope = 'resourceGroup'

param env string
param location string

@allowed(['standard', 'premium'])
param egressEnforcementTier string

param firewallSubnetId string
param lawId string

// ---------------------------------------------------------------------------
// Public IP for Firewall
// ---------------------------------------------------------------------------

resource firewallPip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-fw-opensandbox-${env}'
  location: location
  sku: { name: 'Standard' }
  zones: ['1', '2', '3']
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}

// ---------------------------------------------------------------------------
// Azure Firewall Policy — SKU conditional on egressEnforcementTier
// ---------------------------------------------------------------------------

resource firewallPolicy 'Microsoft.Network/firewallPolicies@2023-11-01' = {
  name: 'afwp-opensandbox-${env}'
  location: location
  properties: {
    sku: {
      tier: egressEnforcementTier == 'premium' ? 'Premium' : 'Standard'
    }
    threatIntelMode: 'Alert'
    // DNS proxy enables FQDN resolution in network rules
    dnsSettings: {
      enableProxy: true
    }
  }
}

// ---------------------------------------------------------------------------
// Rule Collection Group — Sandbox Egress Allowlist
// ---------------------------------------------------------------------------

resource sandboxEgressRcg 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2023-11-01' = {
  parent: firewallPolicy
  name: 'rcg-sandbox-egress'
  properties: {
    priority: 200
    ruleCollections: egressEnforcementTier == 'premium'
      // PREMIUM: Application rules with SNI inspection (no TLS termination inside Kata)
      ? [
          {
            ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
            name: 'rc-sandbox-pkg-mgr-https'
            priority: 200
            action: { type: 'Allow' }
            rules: [
              {
                ruleType: 'ApplicationRule'
                name: 'allow-pypi'
                protocols: [{ protocolType: 'Https', port: 443 }]
                targetFqdns: ['pypi.org', 'files.pythonhosted.org']
                sourceAddresses: ['10.10.2.0/23']
              }
              {
                ruleType: 'ApplicationRule'
                name: 'allow-npm'
                protocols: [{ protocolType: 'Https', port: 443 }]
                targetFqdns: ['registry.npmjs.org']
                sourceAddresses: ['10.10.2.0/23']
              }
              {
                ruleType: 'ApplicationRule'
                name: 'allow-golang-proxy'
                protocols: [{ protocolType: 'Https', port: 443 }]
                targetFqdns: ['proxy.golang.org', 'sum.golang.org']
                sourceAddresses: ['10.10.2.0/23']
              }
            ]
          }
          {
            // Deny all other application-layer traffic from Kata subnet
            ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
            name: 'rc-sandbox-deny-other'
            priority: 300
            action: { type: 'Deny' }
            rules: [
              {
                ruleType: 'ApplicationRule'
                name: 'deny-all-kata-app'
                protocols: [
                  { protocolType: 'Http', port: 80 }
                  { protocolType: 'Https', port: 443 }
                ]
                targetFqdns: ['*']
                sourceAddresses: ['10.10.2.0/23']
              }
            ]
          }
        ]
      // STANDARD: Network rules with FQDN tags (L3/L4 backup; Cilium L7 handles app-layer)
      : [
          {
            ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
            name: 'rc-sandbox-pkg-mgr-net'
            priority: 200
            action: { type: 'Allow' }
            rules: [
              {
                ruleType: 'NetworkRule'
                name: 'allow-pypi-net'
                ipProtocols: ['TCP']
                destinationPorts: ['443']
                sourceAddresses: ['10.10.2.0/23']
                destinationFqdns: ['pypi.org', 'files.pythonhosted.org']
              }
              {
                ruleType: 'NetworkRule'
                name: 'allow-npm-net'
                ipProtocols: ['TCP']
                destinationPorts: ['443']
                sourceAddresses: ['10.10.2.0/23']
                destinationFqdns: ['registry.npmjs.org']
              }
              {
                ruleType: 'NetworkRule'
                name: 'allow-golang-net'
                ipProtocols: ['TCP']
                destinationPorts: ['443']
                sourceAddresses: ['10.10.2.0/23']
                destinationFqdns: ['proxy.golang.org', 'sum.golang.org']
              }
            ]
          }
        ]
  }
}

// ---------------------------------------------------------------------------
// Azure Firewall
// ---------------------------------------------------------------------------

resource firewall 'Microsoft.Network/azureFirewalls@2023-11-01' = {
  name: 'afw-opensandbox-${env}'
  location: location
  zones: ['1', '2', '3']
  properties: {
    sku: {
      name: 'AZFW_VNet'
      tier: egressEnforcementTier == 'premium' ? 'Premium' : 'Standard'
    }
    firewallPolicy: { id: firewallPolicy.id }
    ipConfigurations: [
      {
        name: 'fw-ipconfig'
        properties: {
          subnet: { id: firewallSubnetId }
          publicIPAddress: { id: firewallPip.id }
        }
      }
    ]
  }

  dependsOn: [sandboxEgressRcg]
}

// ---------------------------------------------------------------------------
// Diagnostic settings → LAW
// ---------------------------------------------------------------------------

resource fwDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-fw-${env}'
  scope: firewall
  properties: {
    workspaceId: lawId
    logs: [
      { category: 'AzureFirewallApplicationRule', enabled: true }
      { category: 'AzureFirewallNetworkRule', enabled: true }
      { category: 'AzureFirewallDnsProxy', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output firewallId string = firewall.id
output firewallName string = firewall.name
// Azure Firewall always takes .4 in AzureFirewallSubnet
output firewallPrivateIp string = firewall.properties.ipConfigurations[0].properties.privateIPAddress
output firewallPublicIp string = firewallPip.properties.ipAddress
