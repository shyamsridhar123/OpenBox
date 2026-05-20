// modules/aca.bicep — ACA environment (Workload Profiles), VNet-integrated, internal-only.
//   Three apps: control-plane, portal-api, portal-frontend (with Easy Auth).
// Plan reference: Phase 1 Task 1.1/1.6; ADR — ACA control plane + portal.
//   B-C3 fix: minReplicas=1 (NOT 0 — scale-to-zero forbidden by consensus plan).
//   KEDA HTTP scaler; cooldownPeriod bounded per S-C4.

targetScope = 'resourceGroup'

param env string
param location string
param acaSubnetId string
param lawId string
@secure()
param appInsightsConnectionString string

@description('Entra portal app ID for Easy Auth. Populate after az ad app create.')
param portalAppId string = ''

@description('AKS server app ID for OBO audience validation.')
param aksServerAppId string = ''

// ---------------------------------------------------------------------------
// ACA Managed Environment — Workload Profiles SKU, internal-only
// API version 2024-03-01 stable.
// ---------------------------------------------------------------------------

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'acaenv-opensandbox-${env}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(lawId, '2023-09-01').customerId
        sharedKey: listKeys(lawId, '2023-09-01').primarySharedKey
      }
    }
    vnetConfiguration: {
      internal: true
      infrastructureSubnetId: acaSubnetId
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
      {
        // Dedicated profile for control plane (predictable latency)
        name: 'D4'
        workloadProfileType: 'D4'
        minimumCount: 1
        maximumCount: 5
      }
    ]
    peerAuthentication: {
      mtls: {
        enabled: true
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Control Plane app — FastAPI provisioning endpoint
// ---------------------------------------------------------------------------

resource controlPlaneApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-ctrl-opensandbox-${env}'
  location: location
  properties: {
    managedEnvironmentId: acaEnv.id
    workloadProfileName: 'D4'
    configuration: {
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: []
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'control-plane'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'  // placeholder; replace with ACR image
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'AKS_SERVER_APP_ID'
              value: aksServerAppId
            }
            {
              name: 'ENVIRONMENT'
              value: env
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1   // NOT 0 — scale-to-zero forbidden (plan consensus / B-C3)
        maxReplicas: 10
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
              auth: []
            }
          }
        ]
        // cooldownPeriod is not valid on the scale object in ACA schema; removed.
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Portal API app — backend for the read-only portal
// ---------------------------------------------------------------------------

resource portalApiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-portalapi-opensandbox-${env}'
  location: location
  properties: {
    managedEnvironmentId: acaEnv.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: {
        external: false
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'portal-api'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'  // placeholder
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'ENVIRONMENT'
              value: env
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1   // NOT 0
        maxReplicas: 10
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
              auth: []
            }
          }
        ]
        // cooldownPeriod is not valid on the scale object in ACA schema; removed.
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Portal Frontend app — with Easy Auth (Entra)
// ---------------------------------------------------------------------------

resource portalFrontendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-portalfe-opensandbox-${env}'
  location: location
  properties: {
    managedEnvironmentId: acaEnv.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: {
        external: true   // Exposed via App Gateway (AppGW terminates TLS, routes to this)
        targetPort: 3000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'appinsights-connection-string'
          value: appInsightsConnectionString
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'portal-frontend'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'  // placeholder
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'ENVIRONMENT'
              value: env
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1   // NOT 0
        maxReplicas: 10
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
              auth: []
            }
          }
        ]
        // cooldownPeriod is not valid on the scale object in ACA schema; removed.
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Easy Auth for portal-frontend — Entra (AzureAD) provider
// authConfig resource requires the ACA app to exist first.
// ---------------------------------------------------------------------------

resource portalFrontendAuth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: portalFrontendApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: 'https://sts.windows.net/${subscription().tenantId}/v2.0'
          clientId: empty(portalAppId) ? 'REPLACE-WITH-PORTAL-APP-ID' : portalAppId
        }
        validation: {
          allowedAudiences: [
            empty(portalAppId) ? 'REPLACE-WITH-PORTAL-APP-ID' : portalAppId
          ]
        }
        login: {
          loginParameters: ['scope=openid profile email']
        }
      }
    }
    login: {
      routes: {
        logoutEndpoint: '/auth/logout'
      }
      tokenStore: {
        enabled: true
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output acaEnvId string = acaEnv.id
output acaEnvName string = acaEnv.name
// The static IP of the ACA internal load balancer (used by App Gateway backend pool)
output acaEnvStaticIp string = acaEnv.properties.staticIp
output controlPlaneAppFqdn string = controlPlaneApp.properties.configuration.ingress.fqdn
output portalApiAppFqdn string = portalApiApp.properties.configuration.ingress.fqdn
output portalFrontendAppFqdn string = portalFrontendApp.properties.configuration.ingress.fqdn
