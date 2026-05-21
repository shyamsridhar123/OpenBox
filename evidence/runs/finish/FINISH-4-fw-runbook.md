# FINISH-4 — FW + UDR reattach runbook

> Status as of 2026-05-20: **Bicep authored + validated, live deploy deferred to a deliberate maintenance window.**
>
> Why deferred: the live cluster currently runs E2E green (RUN-4, FINISH-1).
> Deploying a 20-minute FW provision against a live cluster, with a UDR
> reattach that could disrupt running sandboxes mid-flight, is a planned
> maintenance operation — not something to fold into a session that also
> has 6 other tasks closing. The Bicep is the artifact; this runbook is
> the deployment guide.

## What changed in code

`infra/bicep/modules/firewall.bicep` — added a new `rcg-aks-bootstrap` rule
collection group at policy priority **100** (evaluated before sandbox rules
at priority 200), containing:

1. `allow-aks-fqdn-tag` — uses Microsoft's `AzureKubernetesService` FQDN
   tag, which covers `mcr.microsoft.com`, `*.tun.<region>.azmk8s.io`,
   `packages.aks.azure.com`, `login.microsoftonline.com`, and the rest of
   the AKS managed-control-plane fanout. Source: snet-system + snet-kata.
2. `allow-azure-linux-packages` — `*.azurelinux.microsoft.com`,
   `packages.microsoft.com`, `security.azurelinux.microsoft.com` on
   ports 80+443. The CSE script the kubelet runs at node-bootstrap
   pulls Azure Linux 3 packages from these hosts.
3. `allow-acr-pulls` — `*.azurecr.io` and `*.data.azurecr.io`. Belt
   and braces in case ACR Private Link DNS hasn't propagated when a
   node first boots.

`sandboxEgressRcg` now `dependsOn: [aksBootstrapRcg]` to guarantee the
bootstrap rules exist before the sandbox-deny rule at priority 300 can
be evaluated.

## Why the previous attempt died

`evidence/runs/finish/fw-failure-trace.md` captured it: UDR was attached
to `snet-kata` BEFORE the FW had any AKS bootstrap rules. AKS rolled
nodes, every roll failed CSE because `packages.aks.azure.com` wasn't
allowed, AKS rolled again, cluster stayed in `Creating` forever.

Fix order is therefore non-negotiable: **rules first → FW healthy → UDR
attached last**.

## Deployment runbook

```bash
# 0. Pre-flight: confirm everything currently green
kubectl get pods -A | grep -E 'opensandbox|sandbox'   # control plane Running
/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py   # RUN-4 SUCCESS

# 1. Tear down the existing FAILED firewall (cannot patch a Failed resource).
az network firewall delete -g rg-opensandbox-dev -n afw-opensandbox-dev --yes
az network firewall policy delete -g rg-opensandbox-dev -n afwp-opensandbox-dev --yes

# 2. What-if the redeploy to catch surprises.
az deployment group what-if \
  -g rg-opensandbox-dev \
  --template-file infra/bicep/main.bicep \
  --parameters env=dev egressEnforcementTier=premium

# 3. Apply.
az deployment group create \
  -g rg-opensandbox-dev \
  --template-file infra/bicep/main.bicep \
  --parameters env=dev egressEnforcementTier=premium

# 4. Wait for FW Succeeded. ~10-20 minutes.
az network firewall show -g rg-opensandbox-dev -n afw-opensandbox-dev \
  --query "provisioningState" -o tsv
# Loop until 'Succeeded'.

# 5. NOW attach the UDR to snet-kata.
az network vnet subnet update \
  -g rg-opensandbox-dev \
  --vnet-name vnet-opensandbox-dev \
  -n snet-kata \
  --route-table rt-snet-kata-dev

# 6. Verify the live cluster still works.
kubectl get pods -A | grep -E 'opensandbox|sandbox'
/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py
/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/kimi_via_osb.py

# 7. If any pod CrashLoops with FW denials in its events, see step 8.
# Otherwise, mark FINISH-4 complete and move on.

# 8. Rollback (if step 6 fails):
az network vnet subnet update \
  -g rg-opensandbox-dev \
  --vnet-name vnet-opensandbox-dev \
  -n snet-kata \
  --remove routeTable
# Then collect the FW logs (already wired to LAW via fwDiag) and identify
# which FQDN got blocked, add it to firewall.bicep, redeploy, retry.
```

## Cost / risk note

Azure Firewall Premium runs **~$1.25/hr** plus data egress. The cluster
has been running without FW since the "skip the fw" pivot, so this is
the first time we'd be paying for it. Plan accordingly — set a budget
alert before step 3 if this is a personal subscription.

## Acceptance verification (after step 6)

- AC-21 ✅ ← FW reattached, UDR on snet-kata, AKS bootstrap rules present
- `evidence/runs/finish/AC-CHECKLIST.md` row 21 → flip 🟡 to ���
- Capture screenshot rows 18, 19, 20 in `evidence/screenshots/SHOTS.md`
