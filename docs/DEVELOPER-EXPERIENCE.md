# Developer Experience

This document is the on-ramp for a developer at our company who wants to use the OpenBox
sandbox runtime to execute untrusted or AI-generated code. It is the
operating manual for the SDK and the surrounding workflow. For the underlying
platform shape (CRDs, controller, firewall, ACA wiring) see
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md). For day-2 operations see
[`docs/OPERATIONS.md`](OPERATIONS.md).

---

## 1. The 30-second mental model

OpenBox is a Kubernetes-native, Kata-isolated code execution runtime on Azure. You
call `Sandbox.create()` in Python, get a fresh VM-isolated pod with its own
kernel, run shell commands against it, read the output, and let the SDK tear it
down. The lifecycle (when to create, when to kill, what to run) is your
application's job. The isolation boundary — a separate guest kernel per
sandbox, deny-all egress, image pulled from a private ACR — is the platform's
job. You never SSH into anything and you never touch a `Pod` directly.

The call path on a laptop today:

```
+-------------------+         +------------------------+         +------------------------+
| your application  | HTTP    | sandbox server         |  K8s    | sandbox controller     |
| (Python SDK)      |  -----> | (FastAPI, in cluster)  | ------> | (Go, reconciles CRDs)  |
| Sandbox.create()  |   :18080|                        |         |                        |
+-------------------+         +------------------------+         +-----------+------------+
                                                                              |
                       +-----------kubectl port-forward----------+            v
                       |                                          |   creates BatchSandbox CR
                       v                                          |            |
              localhost:18080  (laptop only;                      |            v
              ACA + AppGW once FINISH-7 lands)            +-----------------------------+
                                                          | Kata pod on snet-kata        |
                                                          | (Cloud Hypervisor + AzLinux3 |
                                                          |  guest kernel, runc-on-host) |
                                                          |  init: bootstrap.sh          |
                                                          |  main: execd daemon          |
                                                          +-----------------------------+
                                                                       ^
                                                                       | exec / file I/O
                                                                       | (server proxies)
                                                                       |
                                                       <----- response back to SDK -----+
```

Why this exists at our company, and not "just a Docker container" or "just ACI":

- **A Docker container shares the host kernel.** A compromised payload that
  finds a Linux LPE escapes onto the AKS node. Kata gives each sandbox a fresh
  VM with its own kernel, so a kernel-level escape only earns the attacker an
  empty disposable VM.
- **Azure Container Instances has no batch / CRD semantics.** No pooling, no
  warm-pool, no controller-driven readiness, no `kubectl get batchsandbox -A`,
  and no Kata. You'd be on your own for lifecycle, audit, and network egress
  control.
- **The runtime is a vendored third-party project.** We run its controller,
  server, and `execd` unchanged (see [`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md)),
  on top of an Azure landing zone (firewall,
  ACR Premium PE, Event Hubs audit, Workload Identity, ACA control plane).

---

## 2. Getting started

You will reproduce [`evidence/runs/finish/sdk_e2e.py`](../evidence/runs/finish/sdk_e2e.py)
end-to-end. That script is the canonical "first sandbox" example.

### 2.1 Prereqs

- Python 3.12+
- `az` CLI with `az login` against the tenant that owns `rg-opensandbox-dev`
- `kubectl` on PATH (the AKS-issued credential is what we need)
- Membership in the AKS RBAC group that grants `azureKubernetesService.user`
  on `aks-opensandbox-dev`. If `kubectl get ns` fails with `Forbidden`, you do
  not have it yet — ask the platform team.

### 2.2 Pull the cluster credential

```bash
az aks get-credentials \
  -g rg-opensandbox-dev \
  -n aks-opensandbox-dev \
  --overwrite-existing
```

Verify:

```bash
kubectl get ns opensandbox
```

If that returns `opensandbox  Active  ...`, you're in.

### 2.3 Get the API key

The server enforces a single API key today (see section 9 for the per-developer
plan). It lives in a `Secret` in the cluster:

```bash
kubectl -n opensandbox get secret opensandbox-server-api-key \
  -o jsonpath='{.data.api-key}' | base64 -d > .opensandbox-api-key
```

Store that file alongside your script and never commit it. The companion
scripts under `evidence/runs/finish/` read `.opensandbox-api-key` next to
themselves.

### 2.4 Install the SDK

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install --upgrade pip
pip install opensandbox
```

Or, if you prefer `uv`:

```bash
uv venv
uv pip install opensandbox
```

The SDK currently ships as `opensandbox` on PyPI; this repo was last verified
against `OpenSandbox-Python-SDK/0.1.9` (see
`opensandbox.config.ConnectionConfig.user_agent`).

### 2.5 Open the tunnel

From your laptop you cannot dial `opensandbox-server` directly — its Service is
ClusterIP only. Port-forward:

```bash
kubectl -n opensandbox port-forward svc/opensandbox-server 18080:8080
```

Leave this running in its own terminal. Everything that follows assumes
`localhost:18080` reaches the server. (Once FINISH-7 lands and the ACA control
plane is fronting the server through Application Gateway, you'll swap this for
an `https://...` URL — see section 11.)

### 2.6 Run your first sandbox

The whole script, lifted from `evidence/runs/finish/sdk_e2e.py`:

```python
import asyncio
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig

API_KEY_FILE = Path(__file__).resolve().parent / ".opensandbox-api-key"
DOMAIN = "localhost:18080"
IMAGE = "python:3.12-slim"


async def main():
    api_key = API_KEY_FILE.read_text(encoding="utf-8").strip()

    config = ConnectionConfig(
        domain=DOMAIN,
        api_key=api_key,
        protocol="http",
        use_server_proxy=True,
    )

    sandbox = await Sandbox.create(
        IMAGE,
        connection_config=config,
        timeout=timedelta(minutes=5),
        ready_timeout=timedelta(minutes=3),
    )
    print(f"sandbox id = {sandbox.id}")

    async with sandbox:
        execution = await sandbox.commands.run(
            "echo HELLO && uname -a && python3 -c 'print(2+2)'"
        )

        def _flatten(events):
            if not events:
                return ""
            if isinstance(events, str):
                return events
            return "\n".join(getattr(e, "text", str(e)) for e in events)

        print("exit:", execution.exit_code)
        print("stdout:")
        print(_flatten(execution.logs.stdout if execution.logs else None))


asyncio.run(main())
```

Save as `first_sandbox.py` and run:

```bash
python first_sandbox.py
```

The first run will take 60–120 seconds — that's a cold Kata VM booting on the
`kata` nodepool. Subsequent runs against an already-warm node take ~30s.

### 2.7 Understanding `OutputMessage`

`execution.logs.stdout` is **not** a string. It is `list[OutputMessage]`, where
each `OutputMessage` carries:

```
OutputMessage(text: str, timestamp: datetime, is_error: bool)
```

`execd` streams the underlying process's output as discrete events (one per
write boundary, roughly), which is why a multi-line `echo` may show up as one
event and a long `python3` run as many. The `_flatten` helper above is the
shape every script in this repo uses — copy it, don't re-invent it.

### 2.8 First 5 minutes troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `urllib.error.HTTPError: 401` from `Sandbox.create` | API key missing / wrong | Re-fetch from the `opensandbox-server-api-key` secret (section 2.3); confirm no trailing newline (`.strip()`). |
| `ConnectionRefusedError: localhost:18080` | port-forward not running | Re-run the `kubectl port-forward` from section 2.5 in its own terminal. |
| `SandboxReadyTimeoutException` after 30 s | Kata cold start exceeded default `ready_timeout` | Pass `ready_timeout=timedelta(minutes=3)`; see section 7 on latency. |
| `KUBERNETES::POD_READY_TIMEOUT` in server logs | Kata nodepool out of capacity | `kubectl get nodes -l agentpool=kata` to confirm; scale via `az aks nodepool scale` (see section 7). |
| `ImagePullBackOff` on the sandbox pod | Image not in `acropensandboxdemo7075` and not on the firewall allowlist | Use one of the images in section 5 or have it mirrored. |

For the long tail, see section 12.

---

## 3. The Sandbox lifecycle in practice

`Sandbox.create` is a class method on `opensandbox.Sandbox`. The full
signature (from `/tmp/osb-sdk/.../opensandbox/sandbox.py`):

```python
await Sandbox.create(
    image: SandboxImageSpec | str | None = None,
    *,
    snapshot_id: str | None = None,
    timeout: timedelta | None = timedelta(minutes=10),
    ready_timeout: timedelta = timedelta(seconds=30),
    env: dict[str, str] | None = None,
    metadata: dict[str, str] | None = None,
    resource: dict[str, str] | None = None,
    network_policy: NetworkPolicy | None = None,
    entrypoint: list[str] | None = None,
    volumes: list[Volume] | None = None,
    connection_config: ConnectionConfig | None = None,
    skip_health_check: bool = False,
)
```

The fields that matter for day-to-day work:

| Param | What it does | Default |
|---|---|---|
| `image` | Container image reference, either `"python:3.12-slim"` or a `SandboxImageSpec(image=..., auth=...)` for private registries beyond ACR. | required (or `snapshot_id`) |
| `timeout` | Hard upper bound on sandbox lifetime. After this, the controller terminates the pod regardless of what your code is doing. `None` means manual cleanup — be sure you actually call `sandbox.kill()`. | `10 min` |
| `ready_timeout` | How long `Sandbox.create` will wait for `execd` to become healthy before raising. Bump this to 2–3 minutes for cold Kata starts. | `30 s` |
| `env` | Environment variables visible inside the sandbox. See section 9 on secrets. | `{}` |
| `resource` | `{"cpu": "1", "memory": "2Gi"}` style limits enforced on the pod. | `1 CPU / 2 GiB` |
| `connection_config` | Where to reach the server and how. See section 3.2. | `ConnectionConfig()` |

### 3.1 `use_server_proxy=True` — almost always what you want

The SDK has two modes for talking to `execd` (the agent inside each sandbox
pod):

1. **Direct mode** (`use_server_proxy=False`): the SDK fetches the pod's
   internal `execd` endpoint and dials it. This only works when the caller
   shares pod-network routability — typically only another pod in the cluster
   on the same overlay.
2. **Proxy mode** (`use_server_proxy=True`): the SDK sends every `exec`/file
   call to the server, which forwards into the pod over its in-cluster
   connection. One extra hop, slightly higher per-call latency, but no
   pod-network requirement.

Use proxy mode whenever the caller is:

- A laptop (you reach the server through `kubectl port-forward`, but you
  cannot reach pod IPs at all).
- An ACA container app (different network plane from AKS pods).
- Any non-AKS host.

Both `sdk_e2e.py` and `kimi_via_osb.py` use `use_server_proxy=True`. Unless
you are explicitly running another pod inside the same AKS cluster on the
overlay, leave it on.

### 3.2 `ConnectionConfig`

```python
from opensandbox.config import ConnectionConfig
from datetime import timedelta

config = ConnectionConfig(
    domain="localhost:18080",      # host:port, no scheme
    api_key="<from the secret>",
    protocol="http",               # "http" for port-forward; "https" for AppGW
    use_server_proxy=True,
    request_timeout=timedelta(seconds=60),  # default 30
    debug=False,                   # set True for httpx wire logs
)
```

`ConnectionConfig` is a `pydantic.BaseModel`. It owns an `httpx` transport on
your behalf and tears it down when the owning `Sandbox` is closed. If you pass
in your own `transport=`, the SDK will not close it — that's your responsibility.

### 3.3 The `async with sandbox:` pattern

```python
sandbox = await Sandbox.create(IMAGE, connection_config=config)
async with sandbox:
    await sandbox.commands.run("...")
```

Exiting the `async with` block calls `sandbox.close()`, which **only closes
the local HTTP transport**. To terminate the remote sandbox pod, call
`sandbox.kill()` (or let the server-side `timeout` reap it). The upstream
docstring is explicit about this — the context manager is not a
fire-and-forget destructor.

The idiomatic pattern, which both demo scripts use:

```python
sandbox = await Sandbox.create(IMAGE, connection_config=config,
                               timeout=timedelta(minutes=5))
try:
    async with sandbox:
        # do work
        ...
finally:
    await sandbox.kill()
```

If you forget `kill()`:

- The `BatchSandbox` CRD stays around until its `timeout` expires.
- The Kata pod keeps running, holding a slot on the Kata nodepool.
- You burn quota and audit-pipeline volume for nothing.

### 3.4 Inspecting sandboxes from outside the SDK

```bash
# All BatchSandbox custom resources in every namespace
kubectl get batchsandbox -A

# Underlying pods (Kata runtimeClassName visible)
kubectl get pods -A -l opensandbox.io/role=sandbox

# Describe a stuck one
kubectl describe batchsandbox -n opensandbox <name>

# Nuke a runaway sandbox by ID
kubectl delete batchsandbox -n opensandbox <name>
```

You can also use the `SandboxManager` (section 13) to do this through the SDK
with the same API key, which is what the future portal will do.

---

## 4. Running commands

### 4.1 `sandbox.commands.run`

```python
execution = await sandbox.commands.run("python3 -c 'print(2+2)'")
```

Returns an execution result with:

- `execution.exit_code: int`
- `execution.logs.stdout: list[OutputMessage] | None`
- `execution.logs.stderr: list[OutputMessage] | None`

`OutputMessage` is `(text: str, timestamp: datetime, is_error: bool)`. Always
flatten with the `_flatten` helper from section 2.7 unless you actually care
about timestamps.

### 4.2 Streaming vs aggregated

`commands.run` returns once the command exits. Long-running interactive
workloads should be split into multiple `run` calls; the SDK is not the right
shape for `tail -f`. For pseudo-streaming, write incremental output to files
and poll them with `sandbox.files.read_file(...)`.

### 4.3 Exit codes

Treat exit code like you would in any shell. Specifically:

- `0` — success.
- Non-zero — the *command's* exit code, not a sandbox-level error.
- Sandbox-level failures (timeout, OOM, image pull) raise an exception from
  `commands.run`, not a non-zero exit. Catch `opensandbox.exceptions.SandboxException`
  for those.

### 4.4 Files in and out

```python
# Write
await sandbox.files.write_file("/tmp/hello.py", "print('hi')\n")

# Read back
content = await sandbox.files.read_file("/tmp/hello.py")

# List a directory
entries = await sandbox.files.list_dir("/tmp")
```

`write_file` accepts `str` or `bytes`. For multi-megabyte payloads, prefer
chunked writes or a registry-pulled image with the data baked in — the file
API goes through the same server proxy as `commands.run` and is bandwidth-bound
by it.

### 4.5 Working directory

`sandbox.commands.run` does not have a `cwd` parameter in 0.1.9. Either set it
in the command itself —

```python
await sandbox.commands.run("cd /workspace && python3 main.py")
```

— or use `RunCommandOpts` from `opensandbox.models.execd` if you need finer
control over env/stdin per call.

### 4.6 Environment variables: build-time vs run-time

- **Build-time** (passed to `Sandbox.create(env=...)`): set once when the pod
  is created, visible to every command. Persist for the sandbox lifetime.
  Suitable for non-secret config.
- **Run-time** (set inline in the command): scoped to that one invocation.
  Prefer this for anything sensitive you don't want sticking around.

```python
# build-time
await Sandbox.create(IMAGE, env={"PYTHONUNBUFFERED": "1"}, ...)

# run-time
await sandbox.commands.run("MYTOKEN=$SECRET python3 do_thing.py")
```

---

## 5. Choosing an image

The Kata pool can only pull from registries the firewall allows and the
kubelet identity can authenticate to. In practice that means our private ACR
and mirrored upstream images.

| Image | Use it for | Source |
|---|---|---|
| `python:3.12-slim` | First example, lightweight Python tasks. | Mirrored into `acropensandboxdemo7075.azurecr.io/library/python:3.12-slim`. |
| `acropensandboxdemo7075.azurecr.io/opensandbox/code-interpreter:v1.0.0` | Jupyter + scientific Python (numpy, pandas, matplotlib, scikit-learn). What you want for "act like ChatGPT Code Interpreter". | Built from upstream `code-interpreter` Dockerfile under `third_party/opensandbox/`. |
| `acropensandboxdemo7075.azurecr.io/opensandbox/code-interpreter-base:v1.0.0` | Ubuntu base for your own derivative images — Node.js, Go, anything else. | Built from upstream `code-interpreter-base` Dockerfile. |

### 5.1 Pulls and auth

The Kata nodepool's kubelet identity has `AcrPull` on
`acropensandboxdemo7075`. ACR Premium is private-endpoint-only
(`pe-acr-opensandbox-dev`, 10.10.12.6) with the `privatelink.azurecr.io`
private DNS zone linked to the cluster vnet. As a developer you don't
configure any of this — `image: <something>` just works as long as it's in
that registry.

For one-off images outside ACR, use a `SandboxImageSpec` with explicit auth:

```python
from opensandbox.models.sandboxes import SandboxImageSpec, SandboxImageAuth

spec = SandboxImageSpec(
    image="ghcr.io/my-org/private-image:1.0",
    auth=SandboxImageAuth(username="...", password="..."),
)
await Sandbox.create(spec, ...)
```

This still requires the registry FQDN to be on the firewall allowlist (file
an issue against the platform — see section 8).

### 5.2 Adding a new sandbox image

The full procedure (ACR build, signature, mirror) lives in
[`docs/OPERATIONS.md`](OPERATIONS.md). The short version for a developer who
just needs another language toolchain:

1. Fork from `code-interpreter-base:v1.0.0`.
2. Add your toolchain in a Dockerfile, `apt-get` from the firewall-allowed
   distro mirrors only.
3. Tag as `acropensandboxdemo7075.azurecr.io/opensandbox/<name>:<semver>`.
4. `az acr build` against `acropensandboxdemo7075` (you'll need
   `AcrPush` — ask the platform).
5. Reference it by its full ACR URI in `Sandbox.create`.

---

## 6. Agentic application patterns

This is the section to read if you're plugging the sandbox into an LLM. The
reference implementation is
[`evidence/runs/finish/kimi_via_osb.py`](../evidence/runs/finish/kimi_via_osb.py)
— Kimi-K2.5/K2.6 in Microsoft Foundry, generating Python code, executed in a
fresh Kata sandbox. The same shape works for GPT-4o, Claude Sonnet, or any
chat model.

### 6.1 The flow

```
+---------+   prompt    +-------------+   <code>    +------------+   stdout   +---------+
| your    | ----------> | LLM         | ----------> | extract /  | ---------> | Sandbox |
| caller  |             | (Kimi /     |             | strip      |            |   exec  |
|         | <---------- | GPT / etc.) | <---------- | fences     | <--------- |         |
+---------+   answer    +-------------+   code      +------------+            +---------+
```

### 6.2 Code extraction

Wrap the model's output in a tag so you can locate the code unambiguously,
then strip stray markdown fences. The pattern from `kimi_via_osb.py`:

```python
import re

def extract_code(raw: str) -> str:
    m = re.search(r"<code>(.*?)</code>", raw, re.S)
    body = (m.group(1) if m else raw).strip()
    body = re.sub(r"^```[a-zA-Z]*\n?", "", body, flags=re.M)
    return body.replace("```", "").strip()
```

The prompt that goes with it:

```
Write a Python program inside <code>...</code> tags that ...
Only the code, no explanation.
```

Models will still occasionally ignore "no explanation" and emit markdown
fences inside the tag — the regex handles both.

### 6.3 Heredoc for multi-line code

Do not try to escape the model's code into a single `python3 -c "..."` shell
string. You will eat a backslash-and-quote war you cannot win. Use a heredoc:

```python
wrapped = (
    "cat > /tmp/code.py <<'OSBPYEOF'\n"
    f"{code}\n"
    "OSBPYEOF\n"
    "python3 /tmp/code.py"
)
await sandbox.commands.run(wrapped)
```

The single-quoted `'OSBPYEOF'` delimiter disables variable expansion inside
the heredoc, so the model's code is passed through verbatim.

### 6.4 Retry + fallback across deployments

Foundry deployments throttle independently. The pattern from `kimi_via_osb.py`:

```python
KIMI_DEPLOYMENTS = ["Kimi-K2.5", "Kimi-K2.6"]

for dep in KIMI_DEPLOYMENTS:
    for attempt in range(3):
        try:
            resp = call_model(dep, payload)
            if resp.text.strip():
                return dep, resp.text
        except HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)   # exponential backoff
            else:
                break                       # don't retry on real errors
```

K2.5 is the primary, K2.6 is the fallback. Both are deployed on
`aihubeastus26267492086`.

### 6.5 Token acquisition

- **From a laptop:** `az login` once, then
  `az account get-access-token --resource https://cognitiveservices.azure.com`.
  Cache it; tokens are good for ~an hour.
- **In-cluster:** Workload Identity. The federated credential
  `id-kimi-demo-dev` is bound to the `demo` namespace's service account
  (see `evidence/runs/finish/wi-federated-credential.json` and
  `wi-role-assignment.txt`). Pods that mount that SA get a token-file at
  `/var/run/secrets/azure/tokens/azure-identity-token` and trade it via MSAL.

### 6.6 Cost awareness

Each sandbox is a Kata VM. The cold-boot cost is real:

- ~30–90 s wall time for the first sandbox on a cold node.
- Each VM occupies its share of the Kata node's CPU/memory until torn down.
- Inference cost (Kimi/GPT/etc.) is *per request* and dwarfs the runtime cost
  for short workloads.

Rules of thumb:

- Batch short LLM-generated snippets into one sandbox rather than one
  sandbox per snippet.
- If you're going to run >10 calls in a session, look at `SandboxPool`
  (section 13).
- Set `timeout` aggressively — `timedelta(minutes=2)` is plenty for a single
  code-extract-and-run cycle. Don't leave 30-minute sandboxes idle.

---

## 7. Performance + limits

| Knob | Today | How to change |
|---|---|---|
| Cold start (first sandbox on cold node) | 30–90 s (Cloud Hypervisor + Azure Linux 3 guest boot) | Pre-warm via `SandboxPool`; don't fight Kata startup time. |
| Warm command RTT (through `use_server_proxy`) | single-digit ms | n/a |
| Default per-sandbox resource | 1 CPU / 2 GiB | `Sandbox.create(resource={"cpu": "2", "memory": "4Gi"})` |
| Concurrency | ~10 concurrent sandboxes on the current 1-node Kata pool | `az aks nodepool scale -g rg-opensandbox-dev --cluster-name aks-opensandbox-dev -n kata --node-count N` |
| Sandbox lifetime | `timeout` on `Sandbox.create` (default 10 min) | Pass any `timedelta`; `None` for manual. |
| Readiness wait | `ready_timeout` (default 30 s) | Bump to 2–3 min for cold starts. |
| SDK HTTP timeout | `ConnectionConfig.request_timeout` (default 30 s) | Pass `request_timeout=timedelta(seconds=60)` for slow networks. |

Kata cold start is dominated by VM boot (kernel + init + `bootstrap.sh`) and
is not something the SDK can hide. If you need sub-second access to an
execution environment, you need a warm pool — that's exactly what
`SandboxPool` is for (section 13).

---

## 8. Networking + isolation guarantees

A sandbox lives on `snet-kata` (10.10.40.0/22). All outbound traffic from
`snet-kata` is routed through `afw-opensandbox-dev` (Azure Firewall Premium,
private IP 10.10.10.4) via the UDR `rt-snet-kata-dev`. The default policy is
**deny-all**; the explicit allows live in two rule collection groups on
`afwp-opensandbox-dev`:

- `rcg-aks-bootstrap` (priority 100): AKS control plane FQDNs needed for the
  node to function.
- `rcg-sandbox-egress` (priority 200): the sandbox-facing allowlist.

By default, sandbox code can reach:

- `*.pypi.org`, `files.pythonhosted.org` — Python wheels.
- `*.npmjs.org`, `registry.npmjs.org` — npm packages.
- `proxy.golang.org`, `sum.golang.org` — Go modules.
- `*.azurecr.io` (via private link DNS for our ACR, public FQDN for upstream
  pull-through if/when we add it).

What it **cannot** reach by default:

- The public Internet at large (deny-all at priority 300).
- The Azure metadata service (169.254.169.254) — explicitly blocked.
- Other namespaces in the cluster (NetworkPolicy enforced by Cilium).
- The host's filesystem or kernel — Kata gives the sandbox its own kernel and
  a virtio-block root; nothing on the AKS node is shared.

### 8.1 Requesting a new FQDN

If your workload needs to reach, say, `huggingface.co`, file an issue against
the platform repo with:

- The exact FQDN(s) and ports.
- Justification (what package/model, why this can't be vendored).
- Expected traffic volume.

The platform team will add a rule to `rcg-sandbox-egress` and confirm via
firewall log query. The runbook is at
[`evidence/runs/finish/FINISH-4-fw-runbook.md`](../evidence/runs/finish/FINISH-4-fw-runbook.md).

### 8.2 The Kata boundary

This is the security story to memorise:

- Each sandbox pod runs under `runtimeClassName: kata-vm-isolation`.
- That dispatches to Cloud Hypervisor (MSHV backend) which boots a fresh VM
  with kernel `6.6.130.1-3.azl3` (Azure Linux 3).
- The container inside the VM still looks like a normal Linux container, but
  the kernel it talks to is the VM's, not the host's.
- A container-escape (CVE-of-the-week, etc.) lands you in an empty VM, not on
  the AKS node.
- The host filesystem is **not** bind-mounted. The host's secrets are **not**
  reachable. There is no `/var/run/docker.sock`, no `/host`, no shared
  `/proc`.

---

## 9. Authentication + secrets

### 9.1 Server API key

There is one API key, gated at the server (`opensandbox-server`). Today every
developer and every workload uses the same key. To rotate it, see
[`docs/OPERATIONS.md`](OPERATIONS.md). Per-developer keys are a tracked work
item; until then, treat the shared key as a write-once secret in your local
environment and never let it land in source control or CI logs.

### 9.2 Sandbox-side secrets

Anything you pass into `env=` on `Sandbox.create` lives for the sandbox's
lifetime, in plaintext inside the pod spec. If the model you're driving can
read its own environment (and it can — that's the point), it can read those
secrets. Two patterns:

- **Short-lived inline secret**: pass it on the command line and unset it.
  ```python
  await sandbox.commands.run(
      "SECRET='" + token + "' python3 do_thing.py && unset SECRET"
  )
  ```
- **No secret in the sandbox at all**: do the secret-bearing call from your
  application, write only its non-sensitive output into the sandbox via
  `sandbox.files.write_file`. This is the right answer when the model is
  generating untrusted code.

### 9.3 Workload Identity for in-cluster callers

If your app runs in the same AKS cluster (e.g. the Kimi demo pod), you don't
need an `az` login or a stored client secret. The federated credential
`id-kimi-demo-dev` is wired to the `demo/<sa-name>` service account, with
role assignments listed in `evidence/runs/finish/wi-role-assignment.txt`.
Your pod gets a projected token file, exchanges it for an AAD token, and
calls Foundry. The same SDK code as the laptop demo works — only the AAD
token source changes.

---

## 10. Observability

Everything important about a sandbox lands in the same audit pipeline.

```
sandbox pod stdout/stderr ----+
                              |
controller events ------------+--> Fluent Bit DaemonSet (on AKS nodes)
                              |        |
sandbox lifecycle events ----+         v
                                  Event Hubs (evhns-opensandbox-dev,
                                  hub: sandbox-audit-fast, 4 partitions,
                                  LocalAuthDisabled)
                                       |
                                       v
                                  Stream Analytics
                                  (asa-opensandbox-audit-dev, system MI)
                                       |
                                       v
                                  Blob Storage
                                  (stasadevse3bwihj3in4s / audit-fast)
```

What that gives you in practice:

- **Per-sandbox stdout/stderr** is available in blob within ~minute
  granularity for incident review.
- **Sandbox create/destroy** events are emitted by the controller and flow
  through the same pipeline.
- **Network audit** is Cilium/Hubble; see
  [`evidence/runs/finish/FINISH-6-acns-runbook.md`](../evidence/runs/finish/FINISH-6-acns-runbook.md).
- **App Insights**: ACA-hosted services will publish their own metrics and
  traces once FINISH-7 is done. The connection string is wired via Container
  Apps managed env vars.

For development debugging, the fastest path is:

```bash
kubectl logs -n opensandbox deploy/opensandbox-server --tail=200
kubectl logs -n opensandbox deploy/opensandbox-controller-manager --tail=200
kubectl logs -n <ns> <sandbox-pod> -c execd
```

---

## 11. From local dev to production

### 11.1 Local

- Transport: `kubectl port-forward svc/opensandbox-server 18080:8080`
- `protocol="http"`, `domain="localhost:18080"`.
- Auth: shared API key from the cluster secret + your `az login` for Foundry.

### 11.2 Production (when FINISH-7 lands)

- Transport: Application Gateway in front of the ACA environment
  `acaenv-opensandbox-dev`, HTTPS terminating at the gateway, WAF rules
  enforced.
- `protocol="https"`, `domain="<gateway-fqdn>"`.
- Auth: same API key initially; per-developer keys + AAD bearer in flight.
- The SDK code does not change apart from `ConnectionConfig`.

### 11.3 Before you ship to prod — checklist

1. `connection_config.protocol == "https"` and the domain points at the
   AppGW, not at a port-forward.
2. The API key is sourced from a managed secret (Key Vault reference in your
   container env, not a file in the image).
3. `Sandbox.create(timeout=...)` is set to a sane upper bound for your
   workload — never `None` in prod.
4. You call `sandbox.kill()` in a `finally`, not only on the happy path.
5. Your code handles `SandboxException` and `SandboxReadyTimeoutException`
   explicitly, with a fallback (retry, queue, or graceful error to the user).
6. Inputs from the model are bounded — you don't paste 4 MB of generated
   code into `commands.run` without a length check.
7. Your sandbox image is from `acropensandboxdemo7075` and pinned by digest
   or semver, not `:latest`.
8. You've talked to the platform team about traffic volume so the Kata
   nodepool is sized for your peak.

---

## 12. Troubleshooting (the long tail)

| Error | Cause | Fix |
|---|---|---|
| `KUBERNETES::POD_READY_TIMEOUT` | Kata cold start exceeded `ready_timeout`. | Pass `ready_timeout=timedelta(minutes=3)`. If recurring, scale the Kata nodepool or add warm pool. |
| `KUBERNETES::INITIALIZATION_ERROR` | Server cannot reach the AKS API (typical on the ACA path before private DNS is wired). | Confirm the ACA-side kubeconfig points at the private cluster FQDN and that the ACA vnet has the private DNS zone linked. See `evidence/runs/finish/FINISH-7-aca-runbook.md`. |
| `exec /opt/opensandbox/bin/bootstrap.sh: no such file or directory` | CRLF line endings in `bootstrap.sh` — the kernel rejects the shebang. | Already fixed in the `v1.0.8` sandbox image we ship. If you build a custom image, set `core.autocrlf=false` and confirm `file bootstrap.sh` reports `ASCII text` not `with CRLF line terminators`. |
| HTTP `504` from `Sandbox.create` | Server's wait-for-pod-Ready timed out, usually nodepool capacity. | `kubectl get pods -A -l opensandbox.io/role=sandbox` to see the queue; scale the Kata pool. |
| HTTP `429` from Kimi/Foundry | Deployment-level throttle. | Exponential backoff + fall back to the secondary deployment (`Kimi-K2.5 → Kimi-K2.6`). See `kimi_via_osb.py`. |
| DNS failures for `*.azurecr.io` | The caller's vnet doesn't have `privatelink.azurecr.io` linked. | Link the private DNS zone to the caller vnet, or call ACR over its public FQDN if approved. |
| `Container App is stopped or does not exist` (when calling the ACA-hosted server) | Either the ACA private DNS zone isn't linked to the AppGW vnet, or no healthy revision is taking traffic. | `az containerapp revision list -n <app> -g rg-opensandbox-dev`; ensure one revision is `Provisioned/Healthy` and weight 100. Verify the private DNS zone `<acaenv-domain>.azurecontainerapps.io` is linked. |
| `Sandbox.create` returned an ID but `commands.run` hangs | `execd` didn't bootstrap inside the pod. | `kubectl logs <pod> -c execd-init` — if you see CRLF or a missing binary, the image is broken. Otherwise raise `ready_timeout` and re-run; you may be hitting a race where you skipped the readiness check. |
| `ImagePullBackOff` | Image not in ACR, or kubelet identity missing AcrPull. | `kubectl describe pod` for the exact error; confirm `acropensandboxdemo7075/<image>:<tag>` exists with `az acr repository show-tags`. |
| Sandbox stuck in `Pending` | Kata nodepool exhausted or taint mismatch. | `kubectl describe pod` for the unschedulable reason; scale nodepool or add the right toleration. |
| 401/403 from Foundry | AAD token expired or wrong audience. | Re-issue with `--resource https://cognitiveservices.azure.com`; in-cluster, restart the pod to refresh the federated token. |

---

## 13. SDK API reference (the practical subset)

This is the minimal set of types you'll touch. The full SDK surface lives at
`/tmp/osb-sdk/Lib/site-packages/opensandbox/` (or wherever pip put it).

### 13.1 `opensandbox.Sandbox`

The primary entrypoint. Class method `create` builds and returns a ready
sandbox; instance methods drive it.

```python
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from datetime import timedelta

sandbox = await Sandbox.create(
    "python:3.12-slim",
    connection_config=ConnectionConfig(
        domain="localhost:18080",
        api_key="...",
        protocol="http",
        use_server_proxy=True,
    ),
    timeout=timedelta(minutes=5),
    ready_timeout=timedelta(minutes=3),
)
async with sandbox:
    result = await sandbox.commands.run("echo hi")
await sandbox.kill()
```

Useful methods:

- `await sandbox.commands.run(cmd: str)` — execute, return result with
  `exit_code` and `logs`.
- `await sandbox.files.write_file(path, content)` / `read_file(path)` /
  `list_dir(path)`.
- `await sandbox.get_info()` — current `SandboxInfo` (state, image, timeout).
- `await sandbox.get_metrics()` — CPU/memory snapshot.
- `await sandbox.renew(timeout=timedelta(...))` — extend the lifetime.
- `await sandbox.pause()` / `await Sandbox.resume(...)` — checkpoint and
  restart (advanced; pairs with snapshots).
- `await sandbox.kill()` — terminate the pod.
- `await sandbox.close()` — release local HTTP transport (also called by
  `async with`).

### 13.2 `opensandbox.config.ConnectionConfig`

```python
ConnectionConfig(
    api_key: str | None = None,
    domain: str | None = None,
    protocol: str = "http",                   # "http" | "https"
    request_timeout: timedelta = timedelta(seconds=30),
    debug: bool = False,
    user_agent: str = "OpenSandbox-Python-SDK/0.1.9",
    headers: dict[str, str] = {},
    transport: httpx.AsyncBaseTransport | None = None,
    use_server_proxy: bool = False,
)
```

Almost every script in this repo uses the same five fields: `domain`,
`api_key`, `protocol`, `use_server_proxy`, and (sometimes) `request_timeout`.
Leave the rest at defaults unless you have a reason.

### 13.3 `opensandbox.SandboxManager`

Use the manager when you need to operate on sandboxes you didn't create —
listing, killing, inspecting — without re-implementing the HTTP plumbing.

```python
from opensandbox.manager import SandboxManager
from opensandbox.models.sandboxes import SandboxFilter

async with SandboxManager(connection_config=config) as mgr:
    infos = await mgr.list_sandbox_infos(SandboxFilter())
    for info in infos.items:
        print(info.id, info.state)
        if info.metadata.get("owner") == "ghost":
            await mgr.kill_sandbox(info.id)
```

`SandboxManager.create(...)` mirrors `Sandbox.create(...)` and returns a live
`Sandbox`. Prefer `Sandbox.create` for app code (one-off sandbox) and
`SandboxManager` for ops code (operating on the fleet).

### 13.4 `opensandbox.SandboxPool` / `AsyncSandboxPool`

When you need warm sandboxes — your latency budget cannot absorb a 30–90 s
cold boot — use a pool. The pool keeps `N` idle sandboxes pre-created and
hands one out per `acquire()`.

```python
from opensandbox import AsyncSandboxPool
from opensandbox.pool_types import AsyncPoolConfig, PoolCreationSpec
from datetime import timedelta

spec = PoolCreationSpec(
    image="python:3.12-slim",
    timeout=timedelta(minutes=10),
)
pool_cfg = AsyncPoolConfig(
    min_idle=2,
    max_total=10,
    creation_spec=spec,
)

async with AsyncSandboxPool(config=pool_cfg, connection_config=conn) as pool:
    async with pool.acquire() as sandbox:
        result = await sandbox.commands.run("python3 -c 'print(2+2)'")
```

Use the pool when:

- You'll run more than ~10 sandboxes per session.
- Your call site is user-facing (chat UI) and 60 s of cold boot is not
  acceptable.
- You want a hard cap on concurrent sandboxes (`max_total`).

Skip the pool when:

- You're running a one-shot script (the demo case).
- Each sandbox needs a very different image — pool one image per pool.

`SandboxPoolSync` is exported as `SandboxPool` for non-async callers.

---

## Appendix: minimum-viable script

If you want one thing to copy into a new repo and start building from:

```python
"""Minimum-viable OpenSandbox app."""
import asyncio
from datetime import timedelta
from pathlib import Path

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig


async def main():
    api_key = Path(".opensandbox-api-key").read_text().strip()
    config = ConnectionConfig(
        domain="localhost:18080",
        api_key=api_key,
        protocol="http",
        use_server_proxy=True,
    )
    sandbox = await Sandbox.create(
        "python:3.12-slim",
        connection_config=config,
        timeout=timedelta(minutes=5),
        ready_timeout=timedelta(minutes=3),
    )
    try:
        async with sandbox:
            execution = await sandbox.commands.run("python3 -c 'print(2+2)'")
            text = "\n".join(
                getattr(e, "text", str(e))
                for e in (execution.logs.stdout or [])
            )
            print("exit:", execution.exit_code)
            print(text)
    finally:
        await sandbox.kill()


if __name__ == "__main__":
    asyncio.run(main())
```

That's the whole shape. Everything else in this document is variations,
guardrails, and failure modes around that one pattern.

---

## Related reading

- [`README.md`](../README.md) — what's actually deployed.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — the platform's internal shape.
- [`docs/OPERATIONS.md`](OPERATIONS.md) — day-2 runbooks and rotation.
- [`evidence/runs/finish/sdk_e2e.py`](../evidence/runs/finish/sdk_e2e.py) —
  canonical first-sandbox script.
- [`evidence/runs/finish/kimi_via_osb.py`](../evidence/runs/finish/kimi_via_osb.py) —
  canonical LLM-driven sandbox script.
- [`evidence/runs/finish/FINISH-4-fw-runbook.md`](../evidence/runs/finish/FINISH-4-fw-runbook.md) —
  firewall allowlist procedure.
- [`evidence/runs/finish/FINISH-7-aca-runbook.md`](../evidence/runs/finish/FINISH-7-aca-runbook.md) —
  ACA control-plane wiring (in progress).
