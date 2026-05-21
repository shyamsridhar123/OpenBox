# Azure Firewall / AKS Kata Bootstrap Failure — Trace Report

**Date:** 2026-05-20  
**Cluster:** `aks-opensandbox-dev` · rg `rg-opensandbox-dev` · eastus2  
**Firewall:** `afw-opensandbox-dev` (Premium SKU, private IP 10.10.10.4)  
**Policy:** `afwp-opensandbox-dev`

---

## Ranked Hypotheses

### H1 — DNS Proxy OFF → FQDN tag `AzureKubernetesService` was inert ★ MOST LIKELY ROOT CAUSE

**Confidence: HIGH**

The rule collection `rc-aks-bootstrap` was added as an **application rule** with `fqdnTags=["AzureKubernetesService"]`. Application rules using FQDN tags work at Layer 7 (SNI/host header) and **do NOT require DNS proxy**. However, the CSE failure was an SSL connect error (`curl error 35 / SSL_ERROR_SYSCALL`) — meaning the TCP handshake to `packages.microsoft.com:443` never completed. This is a symptom of the traffic being **dropped before TLS**, not of an FQDN resolution mismatch.

Two sub-cases:

**H1a — Wrong rule type for the observed failure mode**  
The probe `curl --resolve packages.microsoft.com:443:10.10.10.4 https://packages.microsoft.com/` bypasses DNS entirely (pin via `--resolve`) and hits the firewall's IP directly. Application rules work by inspecting SNI. The firewall should have matched on the SNI `packages.microsoft.com` regardless of DNS proxy status. **BUT** — `curl --resolve` sends the TLS ClientHello to `10.10.10.4` with SNI `packages.microsoft.com`. For this to work, the firewall must be in `provisioningState=Succeeded` and the rule committed to the data plane. During the 25-min `Updating` window, **rules from the new collection were not yet active** — the policy change had not propagated to the firewall instances.

**H1b — DNS Proxy OFF breaks FQDN-in-network-rules (if the rule was a network rule)**  
Microsoft docs are explicit:  
> *"To use FQDN on network rules, you need DNS proxy enabled."*  
> — [AKS limit-egress-traffic](https://learn.microsoft.com/azure/aks/limit-egress-traffic#create-an-azure-firewall-and-enable-dns-proxy)

The probe failure is therefore explained by H1a (propagation not done), not by DNS proxy. But if a **network rule** with FQDNs was used instead of an application rule, DNS proxy absence would render it permanently inert.

**Evidence FOR H1a:**
- `provisioningState=Updating` persisted for 25+ min throughout all probe attempts.
- `curl --resolve` probe returned HTTP=000 / exit 35 throughout, consistent with the firewall data plane still applying the **old policy** (Deny-other at priority 300).
- After the wait was abandoned, the problem was sidestepped (UDR removed) — policy never confirmed `Succeeded` before giving up.

**Evidence AGAINST H1a:**
- 25 min for a single rule-collection add is unusually long but not impossible (see H2). No counter-evidence that the rule was ever applied.

---

### H2 — Azure Firewall Premium policy propagation is slow by design for large/complex policies ★ CONTRIBUTING FACTOR

**Confidence: MEDIUM**

Azure Firewall Premium uses a more powerful VM SKU with IDPS, TLS inspection, and URL filtering pipelines. Policy commits to Premium firewalls are **not SLA-bound to a specific propagation time**. Microsoft's published SLA covers **availability** (99.99%) not update latency. The WAF docs note "most deployments finish in under 20 min" — but that is for WAF on Front Door, not Azure Firewall Policy.

Known factors that extend Premium policy commit time:
- Large existing rule sets (existing `rcg-sandbox-egress` with priority 200/300 rules already in place).
- IDPS signature database sync during rule commits (even with `idpsMode: null`, Premium initialises IDPS pipelines).
- TransportSecurity/TLS inspection being null/unconfigured can still hold the commit while the Premium pipeline evaluates cert-chain config.

**No documented SLA exists for Azure Firewall Policy propagation latency.** Community reports and field experience suggest 5–15 min is typical for Premium; 25–30 min is at the outer edge but not a stuck operation. There is no `provisioningState=Failed` in the described scenario, suggesting it eventually succeeded — just very slowly.

**Evidence FOR H2:**
- Only one tenant (no concurrent modifications stated) → no lock contention.
- Policy eventually showed `Updating` not `Failed` → normal (slow) propagation, not a stuck RP bug.
- Azure docs: no published propagation SLA for Firewall Policy.

**Evidence AGAINST H2 (stuck RP):**
- No ARM `deploymentOperations` errors were captured.
- No support ticket / `az monitor activity-log` query was run to look for `Microsoft.Network/firewallPolicies/write` failures.

---

### H3 — Wrong `outboundType` on the AKS cluster (`loadBalancer` vs `userDefinedRouting`) ★ SECONDARY CAUSE

**Confidence: MEDIUM–HIGH for long-term correctness**

The system pool uses `outboundType=loadBalancer` with no UDR and bootstraps fine. The Kata pool is on `snet-kata` with UDR `rt-snet-kata-dev` (0.0.0.0/0 → 10.10.10.4). However, the **AKS cluster object itself** still has `outboundType=loadBalancer`, which means AKS provisions a Standard Load Balancer with outbound rules. This creates an asymmetric routing conflict:

- Outbound packets from `snet-kata` nodes → UDR sends them to firewall (10.10.10.4).  
- Return path from firewall (SNAT'd to FW public IP) → enters AKS subnet → node kernel sees it as asymmetric (no SLB in the path on return).  
- The SLB outbound rule may also attempt to SNAT traffic from `snet-kata` nodes if they share the cluster's SLB. In this topology the Kata nodepool is a separate subnet, so the SLB likely doesn't apply, but the `outboundType=loadBalancer` tells the AKS control plane the SLB is the egress device, potentially causing confusion during node registration.

**Microsoft's canonical guidance** ([protect-azure-kubernetes-service](https://learn.microsoft.com/azure/firewall/protect-azure-kubernetes-service#restrict-egress-traffic-using-azure-firewall)) explicitly states:
> *"You define the outbound type to use the UDR that already exists on the subnet. This configuration enables AKS to skip the setup and IP provisioning for the load balancer."*

Correct deployment pattern for UDR+FW:
```
outboundType: userDefinedRouting   ← must be set at cluster CREATE time
```
Changing `outboundType` post-cluster-create is not supported; it requires cluster recreation.

---

### H4 — Missing Kata-specific repo FQDN (`packages.microsoft.com/azurelinux/3.0`) not covered by `AzureKubernetesService` tag

**Confidence: HIGH for completeness**

The CSE failure URL is:
```
https://packages.microsoft.com/azurelinux/3.0/prod/cloud-native/x86_64/repodata/repomd.xml
```

Per [AKS outbound-rules-control-egress](https://learn.microsoft.com/azure/aks/outbound-rules-control-egress#azure-global-required-fqdn--application-rules):

| FQDN | Port | Use |
|------|------|-----|
| `packages.microsoft.com` | HTTPS:443 | Microsoft packages repo for `apt-get` / `dnf` ops (Moby, kubelet, etc.) |

`packages.microsoft.com` **is included in the `AzureKubernetesService` FQDN tag** — so the tag was conceptually correct. However, additional FQDNs needed for Kata/Azure Linux 3.0 bootstrap may include:

- `packages.microsoft.com` (HTTPS:443) ← in AzureKubernetesService tag  
- `mcr.microsoft.com` (HTTPS:443)  
- `*.data.mcr.microsoft.com` (HTTPS:443)  
- `acs-mirror.azureedge.net` or `packages.aks.azure.com` (HTTPS:443) ← kubelet/CNI binaries  
- `management.azure.com` (HTTPS:443)  
- `login.microsoftonline.com` (HTTPS:443)  
- `*.hcp.eastus2.azmk8s.io` (HTTPS:443) ← API server tunnel

The error `exit 121` from CSE is specifically the kubelet version lookup failing because `packages.microsoft.com` was blocked. This is the **first** FQDN hit during Azure Linux 3.0 CSE, so even if others were also blocked, fixing this one would have unblocked the immediate failure.

---

## ONE Concrete Next Probe That Would Have Unblocked Us

**Enable DNS proxy first, then verify FW policy is `Succeeded` before attaching UDR.**

```bash
# Step 1 – Enable DNS proxy on the firewall policy (required for FQDN network rules;
#           harmless for app rules, and fixes potential future network rule usage)
az network firewall policy update \
  --resource-group rg-opensandbox-dev \
  --name afwp-opensandbox-dev \
  --enable-dns-proxy true

# Step 2 – Wait for policy commit to complete (poll until Succeeded)
watch -n 15 "az network firewall policy show \
  --resource-group rg-opensandbox-dev \
  --name afwp-opensandbox-dev \
  --query provisioningState -o tsv"

# Step 3 – Once Succeeded, validate the rule is live with a direct probe
# (from a VM on snet-kata, NOT --resolve hack which bypasses DNS)
curl -v --connect-timeout 10 https://packages.microsoft.com/azurelinux/3.0/prod/cloud-native/x86_64/repodata/repomd.xml

# Step 4 – Only then attach the UDR to snet-kata
az network vnet subnet update \
  --resource-group rg-opensandbox-dev \
  --vnet-name <vnet-name> \
  --name snet-kata \
  --route-table rt-snet-kata-dev
```

**Why this is the single unblocking action:** The probe during the incident was run while `provisioningState=Updating`. Policy changes are not atomic — rules in a `Updating` policy are committed to firewall data-plane instances on a rolling basis. Probing before `Succeeded` gives a false-negative. Waiting for `Succeeded` would have confirmed either (a) rule working → attach UDR → Kata pool bootstraps, or (b) rule not working → diagnose DNS proxy / FQDN tag issue.

---

## Next-Time FW Pre-Flight Checklist (before attaching UDR to Kata subnet)

```markdown
## Azure Firewall Pre-Flight for AKS UDR Subnet

### 1. DNS Proxy
- [ ] `az network firewall show ... --query "additionalProperties"` → confirm `Network.DNS.EnableProxy = true`
- [ ] If false: `az network firewall policy update --enable-dns-proxy true` and wait for Succeeded

### 2. Policy Propagation
- [ ] `az network firewall policy show ... --query provisioningState` → must be `Succeeded`
- [ ] If Updating: wait and re-poll every 30s. Typical: 5–15 min. Timeout: 40 min → open support ticket.

### 3. Required AKS Rules Present (application rules, HTTP/HTTPS, source = snet-kata CIDR)
- [ ] FQDN tag `AzureKubernetesService` OR explicit FQDNs:
  - `packages.microsoft.com` (HTTPS:443)
  - `mcr.microsoft.com` + `*.data.mcr.microsoft.com` (HTTPS:443)
  - `acs-mirror.azureedge.net` or `packages.aks.azure.com` (HTTPS:443)
  - `management.azure.com` (HTTPS:443)
  - `login.microsoftonline.com` (HTTPS:443)
  - `*.hcp.<region>.azmk8s.io` (HTTPS:443)
- [ ] Network rules (UDP/TCP, NOT app rules) for:
  - `AzureCloud.eastus2:1194` (UDP/1194) — tunnel to control plane
  - `AzureCloud.eastus2:9000` (TCP/9000) — tunnel to control plane
  - `ntp.ubuntu.com:123` (UDP/123) — NTP

### 4. Validate Before UDR Attach
- [ ] Spin up a test VM on snet-kata (no UDR yet) OR use an existing pod
- [ ] `curl -sv https://packages.microsoft.com/ 2>&1 | grep -E "HTTP|SSL|Connected"` → expect HTTP 200/301/302
- [ ] `curl -sv https://mcr.microsoft.com/` → expect HTTP 200/301
- [ ] Confirm FW logs show `Action: Allow` for the probe (Azure Monitor / Firewall diagnostic logs)

### 5. AKS Cluster outboundType
- [ ] If cluster will use UDR for ALL nodepools: set `outboundType=userDefinedRouting` at cluster CREATE time
- [ ] If only some nodepools use UDR (mixed topology): use `outboundType=loadBalancer` for cluster
       but ensure the Kata nodepool subnet's UDR does NOT conflict with SLB outbound rules
- [ ] Verify no asymmetric routing: packets from Kata nodes leave via FW (UDR), return traffic
       enters via FW public IP → SLB health probes must still reach nodes on snet-kata

### 6. Kata-Specific (Azure Linux 3.0)
- [ ] Confirm `packages.microsoft.com/azurelinux/3.0` path is reachable (Kata CSE uses dnf/rpm)
- [ ] Azure Linux 2.0 is EOL (Nov 2025). Ensure osSku=AzureLinux3 on Kata nodepool.
- [ ] Kata VMs require additional HTTPS egress for pod-VM sandbox image pulls from MCR
```

---

## References

| Doc | URL |
|-----|-----|
| AKS required outbound FQDNs | https://learn.microsoft.com/azure/aks/outbound-rules-control-egress |
| AKS + Azure Firewall (limit-egress) | https://learn.microsoft.com/azure/aks/limit-egress-traffic |
| Azure Firewall protect AKS | https://learn.microsoft.com/azure/firewall/protect-azure-kubernetes-service |
| Azure Firewall DNS proxy | https://learn.microsoft.com/azure/firewall/dns-settings |
| FQDN filtering in network rules | https://learn.microsoft.com/azure/firewall/fqdn-filtering-network-rules |
| Azure Firewall FQDN tags | https://learn.microsoft.com/azure/firewall/fqdn-tags |
| Azure Firewall known issues | https://learn.microsoft.com/troubleshoot/azure/firewall/firewall-known-issues |
| AKS outboundType UDR | https://learn.microsoft.com/azure/aks/egress-outboundtype |
