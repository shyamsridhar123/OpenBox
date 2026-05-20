// modules/network.bicep — VNet, subnets, NSGs, NAT Gateway, UDR
// Plan reference: Phase 1 Task 1.1 — VNet + private endpoints + external ingress
// Subnet CIDRs per plan consensus: snet-system/24, snet-kata/23, snet-aca/23,
//   AzureFirewallSubnet/26, snet-appgw/26, snet-pe/24

targetScope = 'resourceGroup'

param env string
param location string

// ---------------------------------------------------------------------------
// NAT Gateway (outbound for system subnet — pods needing raw internet via NAT)
// ---------------------------------------------------------------------------

resource natGwPip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-natgw-opensandbox-${env}'
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
  }
}

resource natGw 'Microsoft.Network/natGateways@2023-11-01' = {
  name: 'ngw-opensandbox-${env}'
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIpAddresses: [{ id: natGwPip.id }]
    idleTimeoutInMinutes: 10
  }
}

// ---------------------------------------------------------------------------
// NSGs — default deny; open only what is needed per subnet role
// ---------------------------------------------------------------------------

// System subnet NSG — allows AKS API server, Azure infra, intra-VNet
resource nsgSystem 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-snet-system-${env}'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-AzureLoadBalancer'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Allow-VNet-Inbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Deny-All-Inbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Allow-Firewall-Egress'
        properties: {
          priority: 100
          direction: 'Outbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.10.10.0/26'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Allow-VNet-Outbound'
        properties: {
          priority: 200
          direction: 'Outbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '*'
        }
      }
      {
        // REQUIRED for AKS bootstrap. The kubelet's CSE (Custom Script Extension)
        // must reach mcr.microsoft.com, packages.aks.azure.com, and the AKS
        // control plane's public endpoints during node provisioning.
        // Without this rule, every system node OS-provisions but then fails
        // its extension install, AKS rolls the instance, and the cluster stays
        // in Creating forever (observed in deploys 003/004/005).
        // The AKS-managed Standard Load Balancer rewrites source IPs anyway,
        // so this isn't a security regression for the sandbox workloads.
        name: 'Allow-Internet-Egress-AKS-Bootstrap'
        properties: {
          priority: 150
          direction: 'Outbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: 'Internet'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Deny-All-Outbound'
        properties: {
          priority: 4096
          direction: 'Outbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// Kata subnet NSG — sandbox workloads; deny all inbound except VNet, deny direct outbound (UDR routes to Firewall)
resource nsgKata 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-snet-kata-${env}'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-VNet-Inbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Deny-All-Inbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      // Outbound is allowed at NSG level; the UDR forces all traffic to Firewall
      // which then applies the allowlist (pypi, npm, etc.)
    ]
  }
}

// ACA subnet NSG — control plane apps; inbound from AppGW only
resource nsgAca 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-snet-aca-${env}'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-AppGW-Inbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '10.10.11.0/26'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: ['80', '443', '8080']
        }
      }
      {
        name: 'Allow-VNet-Inbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Deny-All-Inbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// App Gateway subnet NSG — allow inbound 80/443 from internet + GatewayManager
resource nsgAppGw 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-snet-appgw-${env}'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-HTTPS-Inbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRanges: ['80', '443']
        }
      }
      {
        name: 'Allow-GatewayManager'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'GatewayManager'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '65200-65535'
        }
      }
      {
        name: 'Allow-AzureLoadBalancer'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Deny-All-Inbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// Private endpoints subnet NSG — allow VNet inbound only
resource nsgPe 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-snet-pe-${env}'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-VNet-Inbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '*'
        }
      }
      {
        name: 'Deny-All-Inbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// UDR for Kata subnet — force 0.0.0.0/0 to Azure Firewall private IP
// The Firewall IP is 10.10.10.4 (first usable in AzureFirewallSubnet).
// NOTE: Azure Firewall always takes .4 in its /26 subnet.
// ---------------------------------------------------------------------------

resource udrKata 'Microsoft.Network/routeTables@2023-11-01' = {
  name: 'rt-snet-kata-${env}'
  location: location
  properties: {
    disableBgpRoutePropagation: true
    routes: [
      {
        name: 'force-egress-to-firewall'
        properties: {
          addressPrefix: '0.0.0.0/0'
          nextHopType: 'VirtualAppliance'
          nextHopIpAddress: '10.10.10.4'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Virtual Network
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: 'vnet-opensandbox-${env}'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: ['10.10.0.0/16']
    }
    subnets: [
      {
        name: 'snet-system'
        properties: {
          addressPrefix: '10.10.1.0/24'
          networkSecurityGroup: { id: nsgSystem.id }
          // No NAT Gateway here: AKS uses outboundType=loadBalancer by default,
          // and attaching a NAT GW to the cluster's system subnet conflicts with
          // the AKS-managed SLB outbound rule (silent CSE bootstrap failure,
          // observed in deploys 003/004).
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-kata'
        properties: {
          addressPrefix: '10.10.2.0/23'
          networkSecurityGroup: { id: nsgKata.id }
          routeTable: { id: udrKata.id }
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: '10.10.4.0/23'
          networkSecurityGroup: { id: nsgAca.id }
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        // MUST be exactly 'AzureFirewallSubnet' — required by Azure Firewall resource
        name: 'AzureFirewallSubnet'
        properties: {
          addressPrefix: '10.10.10.0/26'
          // Azure Firewall subnet must NOT have NSG or UDR
        }
      }
      {
        name: 'snet-appgw'
        properties: {
          addressPrefix: '10.10.11.0/26'
          networkSecurityGroup: { id: nsgAppGw.id }
        }
      }
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: '10.10.12.0/24'
          networkSecurityGroup: { id: nsgPe.id }
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output vnetId string = vnet.id
output vnetName string = vnet.name
output systemSubnetId string = '${vnet.id}/subnets/snet-system'
output kataSubnetId string = '${vnet.id}/subnets/snet-kata'
output acaSubnetId string = '${vnet.id}/subnets/snet-aca'
output firewallSubnetId string = '${vnet.id}/subnets/AzureFirewallSubnet'
output appgwSubnetId string = '${vnet.id}/subnets/snet-appgw'
output peSubnetId string = '${vnet.id}/subnets/snet-pe'
