# Deep Dive Trace: implement-opensandbox-in-azure

> **Source artifacts:** [Alibaba OpenSandbox](https://github.com/alibaba/OpenSandbox) · [Azure Dev Box features](https://azure.microsoft.com/en-us/products/dev-box/#features)
> **Project type:** Greenfield (`C:\Users\shyamsridhar\code\openbox`)
> **Date:** 2026-05-19
> **Lanes:** 3 parallel tracer agents (Architecture/Mapping, Parity/Feature-Gap, Governance/Operational)

---

## Observed Result / Problem Statement

The user wants to implement Alibaba's **OpenSandbox** — a general-purpose sandbox platform for AI applications (FastAPI control plane, `execd` execution daemon, Docker/Kubernetes runtimes, ingress/egress controls, gVisor/Kata/Firecracker isolation, multi-language SDKs) — in their **Azure environment**, with **conceptual parity** to **Azure Dev Box** (DevCenter→Project→Pool hierarchy, image definitions, RBAC, network connections, autostop, self-service catalog).

User lean: **AKS + Kata Containers** as the runtime substrate.

Critical context discovered pre-trace:
- OpenSandbox targets **AI agents**, not human developers — its sandboxes are ephemeral Linux containers, not persistent Windows desktops.
- **Microsoft Dev Box is in maintenance mode** (per Microsoft Learn); Microsoft now directs customers to Windows 365. "Parity" must therefore be conceptual, not feature-for-feature.

---

## Ranked Hypotheses

| Rank | Hypothesis | Confidence | Evidence Strength | Why it leads |
|------|------------|------------|-------------------|--------------|
| 1 | **AKS + Kata (Pod Sandboxing) is the right runtime substrate; conceptual Dev Box mapping is ~65% achievable; governance via private AKS + Workload Identity + Azure Policy + ACR Notation/Ratify** | **High** | Strong | All 3 lanes independently converged on this. OpenSandbox README says it uses K8s CRDs (Lane 1); ~65% of Dev Box concepts translate cleanly to container primitives (Lane 2); AKS Baseline Architecture provides off-the-shelf governance patterns (Lane 3). |
| 2 | **Hybrid: ACA-hosted FastAPI control plane + AKS+Kata runtime** is materially better than all-on-AKS because it removes ops burden from the stateless control plane while preserving full K8s API for OpenSandbox's CRD-driven runtime layer | Medium-High | Moderate | Lane 1 explicitly ranked this #2 with High confidence; Lane 3 implicitly assumed all-on-AKS without considering ACA. This is a live, unresolved architectural choice. |
| 3 | **ACA Dynamic Sessions (Hyper-V isolated, sub-second start) could replace OpenSandbox's K8s runtime entirely**, leaving only the control plane and SDKs from OpenSandbox; gives Microsoft-managed sandboxing semantics | Low | Weak | Lane 1 ruled this out because OpenSandbox uses CRDs and a K8s controller — Dynamic Sessions exposes a REST surface, not a Pod API. Only viable if user accepts forking/replacing the OpenSandbox runtime layer. |

---

## Evidence Summary by Hypothesis

### Hypothesis 1 (AKS + Kata, primary)
- **AKS Pod Sandboxing (Kata) is GA** on K8s ≥1.27, Azure Linux 3.0, Gen2 VMs; activation is a one-line `runtimeClassName: kata-vm-isolation` per pod ([learn.microsoft.com/azure/aks/use-pod-sandboxing](https://learn.microsoft.com/azure/aks/use-pod-sandboxing)).
- Kata and `runc` pods coexist on one cluster → FastAPI control plane and sandbox pods can share infrastructure.
- ACR + Workload Identity + Azure Policy + Defender for Containers form a mature, documented governance stack ([AKS Baseline Architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks)).
- ~65% of Dev Box concepts translate cleanly (Lane 2 mapping table): DevCenter→SandboxCenter, Project→namespace, Pool→node pool+template, image definition→ACR+YAML, autostop→TTL, RBAC roles 1:1.

### Hypothesis 2 (Hybrid: ACA control plane + AKS runtime)
- ACA absorbs stateless FastAPI ops (managed TLS, autoscale, Workload Identity, KEDA) while AKS retains the K8s API and CRDs OpenSandbox requires.
- Costs: dual-plane operational complexity, control-plane↔runtime auth flows (mTLS or signed JWT).
- ACA custom container sessions provide an interesting *secondary* execution substrate for non-CRD-dependent workloads.

### Hypothesis 3 (ACA Dynamic Sessions as runtime)
- Sub-second cold start, Hyper-V isolation, no node pool ops.
- **Disqualified** as primary runtime because OpenSandbox's K8s runtime explicitly uses CRDs and a controller, which Dynamic Sessions cannot host.
- Possibly viable as a *secondary* burst-execution path for stateless workloads.

---

## Evidence Against / Missing Evidence

### Against Hypothesis 1 (AKS + Kata)
- **Kata limitations** ([docs](https://learn.microsoft.com/azure/aks/use-pod-sandboxing#limitations)): Azure Linux 3.0 + Gen2 VMs only; lower IOPS than runc; no `hostNetwork`; **Microsoft Defender for Containers does not assess Kata runtime pods** — a real security-monitoring gap.
- **Kata overhead** requires dedicated node pools with taints; complicates autoscaling and Spot eviction.
- **Workload Identity has a 20-federated-credential-per-UAMI ceiling** and seconds-long propagation delay → ephemeral pods can hit cold-start auth failures unless the control plane retries.
- **Azure Policy propagation latency** creates a non-zero enforcement gap window after assignment changes.

### Against Hypothesis 2 (Hybrid)
- Two-plane ops > one-plane ops. Justification depends on whether the control plane needs ACA's specific capabilities (autoscale-to-zero, simpler TLS) more than it values single-runtime simplicity.

### Against Hypothesis 3 (Dynamic Sessions)
- Requires replacing OpenSandbox's K8s runtime → forking project, losing upstream parity.
- Custom container sessions support BYO image + Hyper-V isolation, but expose a REST API, not a Pod API — fundamentally incompatible with OpenSandbox's controller.

### Universally Missing Evidence
- **Concrete inspection of OpenSandbox's K8s manifests, CRDs, RBAC** has not been done. The README says it uses CRDs; the YAML/code has not been read directly.
- **OpenSandbox's upstream auth model** is `api_key` only — no Entra-native integration exists. All identity must be layered externally.
- **CRIU checkpoint/restore reliability on AKS+Kata+gVisor** is unverified; hibernation-equivalent functionality is therefore aspirational, not proven.

---

## Per-Lane Critical Unknowns

### Lane 1 (Architecture/Mapping)
**Does OpenSandbox's Kubernetes runtime require `cluster-admin`-level CRDs and a custom controller, or does it only use standard Pod/Namespace/ConfigMap APIs with `runtimeClassName`?**
→ If standard-API-only, ACA custom container sessions or K3s become viable. If CRDs+controller, AKS is the only realistic Azure target. This single fact collapses the runtime decision.

### Lane 2 (Parity/Feature-Gap)
**Does the target consumer require a human-developer self-service UI (like devportal.microsoft.com), or is agent-API access the sole interaction surface?**
→ If agent-only, ~5 of 6 "needs new abstraction" items disappear. If human self-service is required, a thin React/Blazor portal becomes a real workstream.

### Lane 3 (Governance/Operational)
**Are tenants mutually untrusted (external paying customers) or mutually trusted (internal teams)?**
→ Determines whether namespace-per-project is acceptable, or whether cluster-per-tenant + ACI Confidential / Confidential AKS is required. Drives compliance scope (SOC 2 alone vs. SOC 2 + HIPAA/PCI + hardware attestation).

---

## Rebuttal Round

**Best rebuttal to the leader (H1: AKS + Kata):**
> "If you're going to put OpenSandbox's FastAPI control plane on AKS just because the runtime is on AKS, you're paying ops cost you don't need. Hybrid (H2) cleanly separates a stateless HTTP service from a complex pod-scheduling substrate. The 'one-plane simplicity' argument is real but is weighed against ACA's better managed surface for the control plane (free TLS, KEDA autoscale, simpler IaC). H1 conflates 'where does the runtime live' with 'where does the API live' — these are separable."

**Why the leader held (qualified):**
H1 remains the *runtime* answer regardless — the rebuttal only narrows the question to control-plane location. Hybrid is therefore a refinement of H1, not a replacement. The interview should resolve control-plane location explicitly.

**Why H3 failed:**
Hard-blocked by OpenSandbox's CRD dependency. Could be revisited only if the user explicitly accepts running a fork.

---

## Convergence / Separation Notes

**Strong convergence on H1 across all 3 lanes (different evidence sources):**
- Lane 1 reached H1 via runtime/API-surface evidence (CRDs require AKS).
- Lane 2 reached H1 via feature-translation evidence (image definitions, autostop, RBAC map cleanly to container primitives).
- Lane 3 reached H1 via governance maturity evidence (private cluster, Workload Identity, Azure Policy, ACR Notation/Ratify are documented best practices).

**Live disagreement: control plane location.** Lane 1 explicitly ranked ACA-control-plane + AKS-runtime as a #2 with High confidence; Lane 3 implicitly assumed everything-on-AKS without considering ACA. **The interview must resolve this.**

**No spurious merging detected.** The 3 hypotheses describe genuinely different system architectures, not relabeled versions of the same thing.

---

## Most Likely Explanation

**AKS + Kata Containers (Pod Sandboxing) is the correct primary runtime substrate.** OpenSandbox's K8s runtime requires a real Kubernetes API with CRD support, which only AKS provides among Azure compute services. Kata gives VM-grade isolation per pod via a one-line `runtimeClassName`. The control plane (FastAPI server, lifecycle API, SDKs' HTTP backend) can live either on the same AKS cluster (simpler ops) or on ACA (better managed surface for stateless HTTP) — this is a live tradeoff to resolve in the interview, not a settled choice.

**Conceptual Dev Box parity is ~65% achievable:**
- ✅ Translates cleanly: DevCenter→SandboxCenter, Project→namespace, Pool→node-pool+template, image definition→ACR+YAML, network connection→VNet integration, RBAC roles 1:1, autostop→TTL, per-user limits→ResourceQuota.
- ⚙️ Needs new abstraction: hibernate (CRIU, risky), self-service portal UI, image-build pipeline (ACR Tasks).
- ❌ Infeasible / N/A: Windows-specific features — Intune enrollment, hybrid join, RDP/GUI, Windows hibernation, VS marketplace images, conditional-access compliant-device check.

**Governance stack:** Private AKS cluster + NAT Gateway + Azure Firewall Premium for egress + Cilium NetworkPolicy + ACR Premium with Notation/Ratify signing + Defender for Containers + Workload Identity (one UAMI per project namespace) + Azure Policy in Deny mode with the K8s pod-security-restricted initiative.

---

## Critical Unknown (Synthesized — Top-Level)

The 3 per-lane unknowns are orthogonal and each must be answered to crystallize the spec. If forced to pick ONE most-blocking question:

> **"Are tenants mutually untrusted?"** (Lane 3's question)

This single answer cascades:
- If yes → cluster-per-tenant + ACI Confidential / Confidential AKS + strict portal access controls + SOC 2 + likely HIPAA/PCI scope.
- If no → namespace-per-project + standard AKS+Kata + SOC 2 baseline + simpler control plane.

The other two unknowns (CRD scope, portal-or-API) are tractable in either branch but their answers shape *scope*, not *security posture*.

---

## Recommended Discriminating Probe

> **Deploy a minimal two-namespace AKS+Kata cluster with Workload Identity and Azure Policy (Deny mode), then attempt: (1) cross-namespace secret enumeration as a non-admin service account, (2) running an unsigned image. Measure cold-start latency under federated-credential propagation. Simultaneously, read OpenSandbox's `kubernetes/` directory to confirm CRD scope.**

This single multi-purpose probe answers Lanes 1 and 3 directly and establishes the production SLA baseline. The portal-vs-API question (Lane 2) is a product/scope decision and is best answered in the interview, not by a technical probe.

---

## Feeds Into Interview Phase (3-Point Injection)

1. **Enriched initial idea:** "Most-likely explanation = AKS+Kata as runtime, ~65% Dev Box conceptual parity, governance via AKS Baseline + Workload Identity + ACR signing. Given this, what should we do?"
2. **Codebase context:** This full trace synthesis (wrapped in `<trace-context>` delimiters) replaces a fresh codebase exploration.
3. **Seeded first questions:** The 3 per-lane critical unknowns become the interview's opening questions, asked in priority order: (Q1) tenant trust model, (Q2) CRD scope from OpenSandbox source, (Q3) human portal or agent-API only.
