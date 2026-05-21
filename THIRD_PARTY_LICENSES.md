# Third-Party Licenses and Attributions

This repository (OpenBox on Azure) is licensed under the MIT License
(see [`LICENSE`](LICENSE)). It vendors and depends on third-party
software that is governed by its own license terms, listed below.

## Vendored source — `third_party/opensandbox/`

| Field | Value |
|---|---|
| Project | OpenSandbox |
| Upstream | https://github.com/alibaba/OpenSandbox |
| Copyright | © Alibaba Group and the OpenSandbox contributors |
| License | Apache License, Version 2.0 |
| License text | [`third_party/opensandbox/LICENSE`](third_party/opensandbox/LICENSE) |
| Pinned commit | `8886b07dfe557a5f044d132e5dc8cc9b5c8bf573` |

The Apache License 2.0 text governs all files under
`third_party/opensandbox/`. Nothing in this file or in the wrapper-repo
`LICENSE` overrides those terms for that directory.

### Modifications carried against the vendored tree

In accordance with Apache-2.0 §4(b) ("You must cause any modified files
to carry prominent notices stating that You changed the files"), this
section enumerates every change held against the pinned upstream commit.

There are exactly **two** modifications. Both are build-pipeline
adjustments required for Azure-region builds and a Windows-developer
checkout; neither alters the runtime behaviour of the controller,
server, or `execd`.

#### 1. Replace `goproxy.cn` with `proxy.golang.org`

Files:
- `third_party/opensandbox/kubernetes/Dockerfile` — line 36
- `third_party/opensandbox/kubernetes/Dockerfile.image-committer` — line 25

Change:

```diff
-RUN GOPROXY=https://goproxy.cn,direct go mod download
+RUN GOPROXY=https://proxy.golang.org,direct go mod download
```

Rationale: `goproxy.cn` (Aliyun module proxy) is unreachable from
Azure-region build agents under our egress firewall allowlist.
`proxy.golang.org` is the canonical Go module proxy and is already on
the firewall allowlist. No code behaviour changes.

#### 2. CRLF protection in the execd bootstrap shell script

File: `third_party/opensandbox/components/execd/Dockerfile`

Change: appended (post line 74):

```diff
+# Strip CRLF line endings that Git on Windows injects into shell scripts.
+# Without this, the kernel sees shebang "#!/bin/sh\r" and execve() fails
+# with ENOENT — surfaced misleadingly as "no such file or directory" on
+# the SCRIPT path rather than the interpreter. Caused sandbox container
+# CrashLoop in the Azure build pipeline.
+RUN sed -i 's/\r$//' ./bootstrap.sh && chmod +x ./bootstrap.sh ./execd
```

Rationale: Windows contributor checkouts auto-translate `\n` to `\r\n`
on `.sh` files unless `.gitattributes` overrides it. A CRLF shebang
yields a kernel ENOENT on the interpreter (`/bin/sh\r`), which manifests
in `kubectl logs` as "cannot execute: required file not found" pointing
at the script itself. The repo-level fix is the root-level
`.gitattributes`; this Dockerfile guard is belt-and-braces.
See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §"The CRLF
bootstrap.sh story" for the full incident write-up.

Nothing else in `third_party/opensandbox/` is modified. The intent is
to keep this list at exactly two entries and to drive both upstream
when feasible.

### Sync policy

The vendored tree is refreshed by `.github/workflows/nightly.yml`, which
opens a PR if upstream `main` advances. The two modifications above are
re-applied on top of each sync; conflicts page the platform team.

---

## Other notable third-party dependencies

This is not an exhaustive SBOM — application dependencies are resolved
at build/install time from `requirements*.txt`, `package.json`,
`go.mod`, Helm chart values, and base container images, and each
carries its own license. The list below covers the components called
out by name in the architecture and runbook docs.

| Component | License | Notes |
|---|---|---|
| Kubernetes / containerd / Cilium | Apache-2.0 | Provided by Azure Kubernetes Service. |
| Kata Containers + Cloud Hypervisor (MSHV) | Apache-2.0 | AKS Pod Sandboxing runtime class `kata-vm-isolation`. |
| FastAPI, Pydantic, Uvicorn | MIT | Control-plane stack on ACA. |
| Azure SDKs (`azure-identity`, `azure-mgmt-*`, `azure-storage-*`) | MIT | Azure resource access from SDKs and IaC tooling. |
| Notation / Ratify | Apache-2.0 | Image-signing toolchain (deferred to a later milestone — see ROADMAP). |
| Fluent Bit | Apache-2.0 | Audit log shipper (deployed by a parallel workstream). |
| Bicep modules | MIT (Microsoft) | Azure ARM template DSL used in `infra/bicep/`. |

If a dependency you care about is missing from this list, open an
issue — the goal is to keep this file accurate, not exhaustive by
accident.
