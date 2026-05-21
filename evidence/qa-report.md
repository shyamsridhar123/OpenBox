# Static QA Report — OpenSandbox-on-Azure scaffold

Generated: 2026-05-20

## Summary
- PASS: 11 / FAIL: 6 / SKIP: 2 / TOTAL: 19

---

## Detailed Results

### Python: control plane (`apps/control-plane/`)

- ✅ **Check 1: Package install** (`uv pip install -e ".[dev]"`)
  - All 20+ packages resolved and installed cleanly. No errors.

- ✅ **Check 2: Syntax compile** (`python -m py_compile **/*.py`)
  - EXIT:0 — all `.py` files parse without error.

- ❌ **Check 3: Ruff lint** (`uv run ruff check app/`)
  - **25 errors found** (7 auto-fixable).
  - Representative violations:
    - `E501` — lines >100 chars in `aks_client.py`, `config.py`
    - `UP037` — quoted type annotations in `auth/dependencies.py`
    - `UP035` — `from typing import AsyncGenerator` (use `collections.abc`) in `main.py`
    - `B904` — `raise ... from err` missing in `routers/users.py:151`
    - `UP041` — `asyncio.TimeoutError` → use builtin `TimeoutError`
  - Also: two deprecated top-level config keys (`ignore`/`select` → `lint.ignore`/`lint.select`) and two removed rule IDs (`ANN101`, `ANN102`) in `pyproject.toml`.

- ❌ **Check 4: Unit tests** (`uv run pytest tests/ -v --tb=short`)
  - **2 FAILED, 17 passed** in 13.56s.
  - `test_create_session_success` and `test_low_latency_with_role_succeeds` both fail with:
    ```
    TypeError: '>' not supported between instances of 'MagicMock' and 'int'
      File "app/auth/obo_exchange.py", line 119, in exchange_for_aks
        ttl = max(0, expires_in - 300)
    ```
  - Root cause: `exchange_for_aks` mock returns a `MagicMock` for `expires_in` instead of an `int`. The OBO mock fixture does not stub the `expires_in` field of the token response.

---

### Python: SDK (`sdks/python/`)

- ✅ **Check 5: Package install** (`uv pip install -e ".[dev]"`)
  - All packages installed cleanly.

- ✅ **Check 6: Unit tests** (`uv run pytest tests/ -v`)
  - **15/15 passed** in 4.24s. Full green.

- ❌ **Check 7: Ruff lint** (`uv run ruff check opensandbox_azure/`)
  - **1 error**: `F401` — `import asyncio` unused in `opensandbox_azure/client.py:7`. Auto-fixable.

---

### JavaScript SDK (`sdks/js/`)

- ✅ **Check 8: npm install**
  - `added 345 packages` — two deprecation warnings for `inflight@1.0.6` and `glob@7.2.3` (transitive, non-blocking).

- ✅ **Check 9: TypeScript type-check** (`npx tsc --noEmit`)
  - TSC_EXIT:0 — no type errors.

---

### Go SDK (`sdks/go/opensandbox/`)

- ✅ **Check 10: `go mod download`**
  - EXIT:0 — module graph resolved.

- ❌ **Check 11: `go vet` / `go build`**
  - **Both fail** — VET_EXIT:1, BUILD_EXIT:1.
  - Three missing `go.sum` entries for Azure SDK packages:
    ```
    client.go:22: missing go.sum entry: github.com/Azure/azure-sdk-for-go/sdk/azcore
    client.go:23: missing go.sum entry: github.com/Azure/azure-sdk-for-go/sdk/azcore/policy
    client.go:24: missing go.sum entry: github.com/Azure/azure-sdk-for-go/sdk/azidentity
    ```
  - Fix: run `go get github.com/your-org/opensandbox-azure-go` to regenerate `go.sum`.

---

### Helm chart (`infra/helm/opensandbox/`)

- ⏭️ **Check 12: `helm lint`** — SKIP: `helm` not installed on this machine.
- ⏭️ **Check 13: `helm template` / kubeval** — SKIP: `helm` not installed on this machine.

---

### CI/CD Workflows (`.github/workflows/`)

- ✅ **Check 14: Checkout action presence**
  - `pr.yml`: 8 occurrences, `main.yml`: 10 occurrences, `nightly.yml`: 3 occurrences. All wired correctly.

- ✅ **Check 15: `pr.yml` YAML validity** — parses clean.

- ❌ **Check 16: `main.yml` YAML validity**
  - `yaml.scanner.ScannerError: mapping values are not allowed here` at **line 371, col 44**.
  - Context (line ~371): a `run:` step uses an inline shell string containing an unescaped colon sequence (`${SYNC_BRANCH} && ...`). Likely needs a block scalar (`run: |`).

- ❌ **Check 17: `nightly.yml` YAML validity**
  - Same error class at **line 151, col 33**.
  - Context: multiline `run:` value with embedded colons in a heredoc-style string (e.g. `"1. \`git fetch upstream && git checkout ${SYNC_BRANCH}..."`). Needs `run: |` block scalar.

---

### Bash Scripts (`scripts/`)

- ✅ **Check 18: Shell syntax** (`bash -n`)
  - All 3 scripts parse without error:
    - `scripts/phase0/spike-cilium-kata-l7.sh` ✅
    - `scripts/phase0/spike-kind-local.sh` ✅
    - `scripts/phase0/spike-opensandbox-crd.sh` ✅

---

### Markdown links (`docs/`, `runbooks/`)

- ✅ **Check 19: Local link integrity**
  - 0 broken local links found across all `.md` files in `docs/` and `runbooks/`.

---

### TODOs / Stubs (project source only)

- ❌ **Check 20: Unimplemented stubs**
  - **Go SDK (`sdks/go/opensandbox/client.go`)**: 22 `TODO:` markers — every public method body is a stub (`// TODO: implement`). Affected: `getToken`, `authHeaders`, `mapError`, `CreateSession`, `ListSessions`, `GetSession`, `Run`, `DeleteSession`, `generateTraceparent` (also uses `math/rand`, flagged as needing `crypto/rand`).
  - **Go SDK (`client_test.go`)**: 5 test stubs (no actual test bodies).
  - Python/TypeScript source files: no project-owned TODOs found (third-party `node_modules` excluded).

---

## Critical Issues Blocking Deploy

| Severity | Item | File | Impact |
|----------|------|------|--------|
| **Critical** | `main.yml` YAML parse error (line 371) | `.github/workflows/main.yml` | CI pipeline won't parse — merges to main will not trigger deploy |
| **Critical** | `nightly.yml` YAML parse error (line 151) | `.github/workflows/nightly.yml` | Nightly security/sync jobs silently broken |
| **High** | Go SDK `go.sum` missing entries | `sdks/go/opensandbox/` | Go SDK won't compile; blocks any Go consumer |
| **High** | Control-plane test failures (OBO mock — `expires_in` not stubbed) | `tests/test_sessions_router.py` | Session-create and low-latency paths untested; mock gap may hide real runtime bug |
| **High** | Go SDK entirely unimplemented (22 TODO stubs) | `sdks/go/opensandbox/client.go` | Go SDK is a scaffold with no real logic; any Go integration will panic at runtime |

---

## Acceptable Warnings

- Control-plane ruff: 25 style/modernisation violations — all non-functional; `ruff --fix` resolves most automatically.
- Python SDK ruff: 1 unused import (`asyncio`) — trivial.
- JS npm: deprecated transitive deps (`inflight`, `glob`) — no security CVEs, cosmetic only.
- `pyproject.toml` (control-plane): deprecated ruff config keys (`ignore`/`select`) — warnings only, lint still runs.

---

## Recommended Next Steps

1. **Fix `main.yml` line 371 and `nightly.yml` line 151** — convert unquoted multiline `run:` values to block scalars (`run: |`). This unblocks CI entirely.
2. **Fix OBO mock in `tests/test_sessions_router.py`** — stub `expires_in` as an `int` (e.g. `mock_token_response.expires_in = 3600`) to fix the 2 failing tests.
3. **Regenerate `sdks/go/opensandbox/go.sum`** — run `go get github.com/your-org/opensandbox-azure-go` so the Go SDK compiles.
4. **Implement Go SDK stubs** — all methods in `client.go` need real bodies; `generateTraceparent` must use `crypto/rand`, not `math/rand`.
5. **Run `ruff --fix app/`** (control-plane) + remove unused `asyncio` import in Python SDK — housekeeping before code review.
6. **Install `helm` in CI and locally** to re-enable checks 12–13 (`helm lint` / `helm template`).
