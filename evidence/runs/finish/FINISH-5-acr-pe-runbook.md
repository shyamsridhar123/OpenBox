# FINISH-5 — ACR private endpoint + DNS runbook

> Status as of 2026-05-20: **Bicep authored + validated, live deploy deferred.**
>
> Why deferred: the live ACR is `acropensandboxdemo7075` in resource
> group `rg-opensandbox-demo`, SKU **Basic**, public access **Enabled**.
> Private endpoints require **Premium** SKU. Cutting public access on a
> live ACR that the AKS cluster (in a different RG) is actively pulling
> from would break the cluster the moment DNS hasn't propagated.

## What the Bicep already does

`infra/bicep/modules/acr.bicep` is **already correct and ready**:

- Creates ACR Premium with `publicNetworkAccess: 'Disabled'`
- Creates private DNS zone `privatelink.azurecr.io` linked to the vnet
- Creates private endpoint in `snet-pe` with `groupIds: ['registry']`
- Wires the PE into the DNS zone group so `*.azurecr.io` resolves to
  the PE IP inside the vnet
- Sends ACR repo + login events to Log Analytics

The only divergence from live state is:
- Live ACR is `acropensandboxdemo7075` (provisioned ad-hoc, Basic SKU,
  different RG `rg-opensandbox-demo`)
- Bicep would create `acropensandboxdev` in `rg-opensandbox-dev`

## Two deployment paths

### Path A — promote live ACR to Premium in place (recommended)

Keeps the existing images, just adds Private Link.

```bash
# 0. Pre-flight: confirm cluster currently pulls from this ACR.
kubectl get pods -A -o jsonpath='{range .items[*]}{.spec.containers[*].image}{"\n"}{end}' \
  | grep acropensandboxdemo7075

# 1. Upgrade SKU. Online, no downtime for pulls.
az acr update -g rg-opensandbox-demo -n acropensandboxdemo7075 --sku Premium

# 2. Create the private DNS zone (in rg-opensandbox-dev — the vnet's RG).
az network private-dns zone create -g rg-opensandbox-dev -n privatelink.azurecr.io
az network private-dns link vnet create \
  -g rg-opensandbox-dev \
  -n link-acr-dev \
  --zone-name privatelink.azurecr.io \
  --virtual-network vnet-opensandbox-dev \
  --registration-enabled false

# 3. Create the PE in snet-pe pointing at the existing ACR (cross-RG).
VNET_ID=$(az network vnet show -g rg-opensandbox-dev -n vnet-opensandbox-dev --query id -o tsv)
SUBNET_ID="$VNET_ID/subnets/snet-pe"
ACR_ID=$(az acr show -g rg-opensandbox-demo -n acropensandboxdemo7075 --query id -o tsv)

az network private-endpoint create \
  -g rg-opensandbox-dev \
  -n pe-acr-opensandbox-dev \
  --subnet "$SUBNET_ID" \
  --private-connection-resource-id "$ACR_ID" \
  --group-id registry \
  --connection-name pe-acr-conn-dev

# 4. Wire PE into the DNS zone group.
az network private-endpoint dns-zone-group create \
  -g rg-opensandbox-dev \
  --endpoint-name pe-acr-opensandbox-dev \
  -n acrDnsZoneGroup \
  --private-dns-zone privatelink.azurecr.io \
  --zone-name privatelink-azurecr-io

# 5. WAIT 60 seconds for DNS propagation to the cluster's CoreDNS.
sleep 60
kubectl run dns-probe --image=alpine:3.20 --rm -it --restart=Never -- \
  nslookup acropensandboxdemo7075.azurecr.io
# Must resolve to a 10.10.12.x address (snet-pe), NOT a public IP.

# 6. Once DNS resolves privately, disable public access.
az acr update -g rg-opensandbox-demo -n acropensandboxdemo7075 \
  --public-network-enabled false

# 7. Verify a fresh sandbox still creates (proves the pull still works).
/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py
# Expect RUN-4 SUCCESS again.

# 8. Capture screenshot row 11 + 21 in evidence/screenshots/SHOTS.md.
```

### Path B — full Bicep deploy (greenfield)

Requires copying images from `acropensandboxdemo7075` to the new
`acropensandboxdev`. Larger blast radius. Skip unless you want to
consolidate everything into one RG.

## Acceptance verification (after step 7)

- AC-22 ✅ ← `publicNetworkAccess: Disabled`, PE resolves correctly inside vnet
- `evidence/runs/finish/AC-CHECKLIST.md` row 22 → flip 🟡 to ✅

## Rollback

```bash
# Re-enable public access immediately if step 7 fails.
az acr update -g rg-opensandbox-demo -n acropensandboxdemo7075 \
  --public-network-enabled true
# Then collect the failed pod's events to understand which FQDN path
# CoreDNS still routes to the public IP.
```
