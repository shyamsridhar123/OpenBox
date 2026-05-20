# OpenSandbox on Azure

> A general-purpose AI-agent sandbox platform on Azure, with conceptual parity to Microsoft Dev Box's governance and image-management model.
>
> **Status:** v1 scaffold (autopilot Phase 2 output). Phase 0 spike required before infra deploy.

## What this is

An implementation of [Alibaba's OpenSandbox](https://github.com/alibaba/OpenSandbox) hosted on Microsoft Azure:

- **Sandbox runtime:** AKS with Kata Containers (Pod Sandboxing) for VM-grade per-pod isolation of untrusted code.
- **Control plane:** FastAPI on Azure Container Apps with Entra ID per-user authentication (OBO).
- **Identity:** End-to-end user OID propagation via Entra OBO → JWT → Workload Identity.
- **Image supply chain:** ACR Premium + Notation (Notary v2) signing + Ratify admission enforcement.
- **Network:** Private AKS cluster + Azure Firewall + Cilium ACNS L7 NetworkPolicy + per-user namespace.
- **Portal:** Read-only React frontend on ACA with Easy Auth (Entra SSO).
- **SDKs:** Python, JavaScript, Go.

See `docs/ARCHITECTURE.md` for the full design and `.omc/plans/ralplan-implement-opensandbox-in-azure.md` for the consensus plan.

## Quickstart (development)

> ⚠️ **Phase 0 (validation) must succeed before any infra deploy.** See `scripts/phase0/` for the gating spikes.

```bash
# 1. Run Phase 0 spikes — these GATE the infra deploy
./scripts/phase0/spike-opensandbox-crd.sh        # Validates CRD scope assumption
./scripts/phase0/spike-cilium-kata-l7.sh         # Validates Cilium L7 on Kata pods

# 2. Review Phase 0 results
cat docs/integration-spikes.md
# If Cilium L7 fails on Kata, set egressEnforcementTier=premium in parameters

# 3. Deploy infrastructure (15-25 minutes)
az deployment sub create \
  --location eastus2 \
  --template-file infra/bicep/main.bicep \
  --parameters infra/bicep/parameters/dev.parameters.json

# 4. Deploy applications
helm upgrade --install opensandbox infra/helm/opensandbox \
  --namespace opensandbox-system --create-namespace \
  --values infra/helm/opensandbox/values.dev.yaml

# 5. Verify
kubectl get runtimeclass kata-vm-isolation
kubectl get pods -n opensandbox-system
curl https://api-opensandbox.<your-domain>/healthz
```

## Architecture at a glance

```
                              ┌────────────────┐
        Internet  ──HTTPS──>  │ App Gateway    │  WAF, TLS termination
                              │ + WAF          │
                              └────────┬───────┘
                                       ▼
                       ┌──────────────────────────────┐
                       │  Azure Container Apps env    │  Workload Profiles
                       │  (snet-aca)                  │
                       │  ┌────────────┐ ┌─────────┐  │
                       │  │ FastAPI    │ │ Portal  │  │  Entra OBO,
                       │  │ control    │ │ frontend│  │  Easy Auth (portal),
                       │  │ plane      │ │ React   │  │  KEDA scale,
                       │  │ (min=1)    │ │         │  │  App Insights
                       │  └─────┬──────┘ └─────────┘  │
                       └────────┼─────────────────────┘
                                ▼ private endpoint, OBO token (aud=AKS server app)
                       ┌──────────────────────────────────────┐
                       │  AKS cluster (private, AAD-int.)     │
                       │  Cilium + ACNS, Azure Policy Deny,   │
                       │  Workload Identity, ≥3 AZs           │
                       │                                      │
                       │  ┌───────────────────┐  system pool  │
                       │  │ OpenSandbox       │  (runc)       │
                       │  │ controller + CRDs │               │
                       │  └───────────────────┘               │
                       │  ┌───────────────────┐  kata pool    │
                       │  │ execd DaemonSet   │  (Gen2,       │
                       │  │ Image pre-warm DS │  Azure Linux  │
                       │  └───────────────────┘  3.0)         │
                       │  ┌───────────────────���               │
                       │  │ ns-<user-oid>     │  kata pods    │
                       │  │ Sandbox pod (Kata)│  per session  │
                       │  │ UAMI-projected SA │               │
                       │  └─────────┬─────────┘               │
                       └────────────┼─────────────────────────┘
                                    ▼ allowlisted egress via UDR
                       ┌──────────────────────────────┐
                       │  Azure Firewall              │  Standard if Cilium L7 OK,
                       │  + Cilium L7 NetworkPolicy   │  Premium SNI otherwise
                       └────────┬─────────────────────┘
                                ▼ allowlisted FQDNs only
                              Internet (pypi.org, npmjs.org, etc.)

  Supporting services (private endpoints, all in VNet):
   • ACR Premium (Notation-signed, dual-cert TrustPolicy)
   • Key Vault (Notation certs, per-user kv-user-<oid>)
   • Log Analytics + Event Hubs + Stream Analytics (audit < 60s)
   • Application Insights (distributed traces, traceparent end-to-end)
```

## Documentation

- `docs/ARCHITECTURE.md` �� Full architecture decisions, the hybrid ACA + AKS+Kata rationale.
- `docs/threat-model.md` — Trust model, Kata's role, known gaps (Defender on Kata).
- `docs/upstream-delta.md` — Deltas between this implementation and upstream OpenSandbox.
- `docs/integration-spikes.md` — Results of Phase 0 spikes (CRD scope, Cilium-Kata L7).
- `docs/acceptance-checklist.md` — 34 acceptance criteria, amended per consensus plan.
- `runbooks/` — Incident response, onboarding, Notation cert rotation, CVE response, DR drill.

## Non-goals (v1)

- ❌ Windows sandboxes (Linux-only)
- ❌ GUI / RDP / desktop access
- ❌ Multi-cloud / hybrid on-prem
- ❌ Multi-region (architecture supports it; v1 deploys to a single region)
- ❌ Hibernation / CRIU
- ❌ External-customer multi-tenancy
- ❌ Full self-service write portal (v1.5)
- ❌ TTL idle reaper (v1.5)

## Project lineage

- **Phase 0 (Trace):** 3-lane parallel investigation — `.omc/specs/deep-dive-trace-implement-opensandbox-in-azure.md`
- **Phase 1 (Spec):** Crystallized via deep-dive interview at ~14% ambiguity — `.omc/specs/deep-dive-implement-opensandbox-in-azure.md`
- **Phase 2 (Plan):** RALPLAN-DR consensus through 3 Planner/Architect/Critic iterations — `.omc/plans/ralplan-implement-opensandbox-in-azure.md`
- **Phase 3 (this code):** Autopilot scaffold

## License

TBD by your organization.
