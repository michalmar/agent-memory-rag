# Plan: Consolidate the MAF Hosted agents (customer-support + directive-rag)

**Status:** Ready for implementation

**Date:** 2026-07-23

## Objective

Remove accidental divergence between the two Foundry Hosted Microsoft Agent
Framework (MAF) agents — `customer-support-maf` (original) and
`directive-rag-maf` (new Directive Assistant) — while preserving their
intentional scope/logic differences. The goal is one shared hosting foundation,
one project layout convention, one authoritative build/deploy path, and
symmetric test coverage. No change to agent behavior, prompts, tool schemas, the
public citation model, or the backend runtime contract.

## Background

A senior-architect review compared both agents. They correctly share the same
principles (identical stack, identity plumbing, gateway-tool transport, the same
backend `FoundryHostedMafRuntime`, and the same `dispatch_agent_tool` gateway),
and diverge — legitimately — in scope: managed Foundry-IQ RAG + memory for
support vs. bespoke agentic directive RAG for the Directive Assistant.

The problems worth fixing are **not** in the agent logic. They are:

1. ~90 lines of identity/observability/middleware bootstrap and the ~40-line
   gateway `_invoke` are duplicated verbatim across the two agents.
2. The two agents use different folder layouts, and the directive `azure.yaml`
   points `project:` at a folder that contains no Dockerfile or source.
3. `deploy_images.sh` orchestrates only the support agent; directive must be
   built manually.
4. `DIRECTIVE_MAX_ITERATIONS` is parsed before its range guard, so an empty or
   non-numeric value crashes at boot with a bare `ValueError`.
5. Two tool-timeout layers (agent-side HTTP vs. backend tool) are unverified.
6. Two different citation strategies (model-emitted markup vs. deterministic
   server-side enrichment).
7. Test coverage is split: middleware is tested only in support; the gateway
   `_invoke` wrapper only in directive. Because the code is duplicated, each copy
   is only half-covered.

## Findings → phases (severity)

| # | Finding | Severity | Phase |
|---|---------|----------|-------|
| 1 | Duplicated identity/middleware/`main()` + gateway `_invoke` | Medium | 1 |
| 7 | Asymmetric test coverage (fixed for free by Phase 1) | Low/Med | 1 |
| 2 | Divergent layout + non-self-consistent `azure.yaml` build | Med/High | 2, 3 |
| 3 | `deploy_images.sh` omits the directive agent | Medium | 4 |
| 4 | `DIRECTIVE_MAX_ITERATIONS` parse crashes before guard | Low | 5 |
| 5 | Unverified agent-vs-backend timeout layering | Low/Med | 5 |
| 6 | Inconsistent citation strategy | Low | 6 (optional) |

## Locked decisions

- Keep both agents as separate deployable units; do **not** merge them into one
  runtime image.
- Preserve all intentional scope differences: tool sets, prompts, iteration
  ceilings, timeouts, the directive workflow/progress stream, and the two
  citation models remain behaviorally intact (Phase 6 is opt-in and separate).
- Extract shared code into a new repo-root, pip-installable package that both
  Docker images install exactly like `agent_contracts` today.
- Do not change the wire contract to the gateway
  (`POST /internal/agent-tools/{tool}` with `{user_id, session_id, call_id,
  arguments}`) or the `agent_contracts` tool-definition/argument models.
- Do not change the backend `FoundryHostedMafRuntime`, `dispatch_agent_tool`,
  per-agent principal allow-lists, or `config.py`.
- Keep server-side ACR builds; no local Docker requirement is introduced.

## Open decisions (confirm before/with Phase 2)

- **D1 — Layout convention.** Choose one:
  - **Option A (recommended): co-locate** `Dockerfile` + `agent.yaml` under
    `agents/<agent>/src/<agent>/` (matches `customer-support-maf` today).
    Simpler; everything for one agent lives in one folder.
  - **Option B: separate** a top-level `agents/<agent>.Dockerfile` +
    `agents/<agent>/deployment/<agent>/agent.yaml` (matches `directive-rag-maf`
    today). Cleaner manifest/source separation but more folders.
- **D2 — Authoritative build path.** Is azd (`azure.yaml`,
  `docker.remoteBuild: true`) a real deploy path, or is
  `scripts/build_hosted_agent_image.sh` the single source of truth? This
  determines whether Phase 3 fixes `azure.yaml` or removes its build service.

## Current implementation facts (for the implementer)

- Both Dockerfiles `COPY` from the **repo root** (`COPY agent_contracts`,
  `COPY agents/<agent>/src/<agent>`), so the working build context is the repo
  root — as used by `scripts/build_hosted_agent_image.sh`
  (`az acr build -f <dockerfile> "$REPO_ROOT"`).
- `agents/customer-support-maf/`: `azure.yaml` `project: src/customer-support-maf`
  which **does** contain `Dockerfile` + `agent.yaml`.
- `agents/directive-rag-maf/`: `azure.yaml`
  `project: deployment/directive-rag-maf-hosted` which contains **only**
  `agent.yaml`; the Dockerfile is `agents/directive-rag-maf.Dockerfile`.
- Duplicated blocks live in:
  - `agents/customer-support-maf/src/customer-support-maf/main.py`
  - `agents/directive-rag-maf/src/directive-rag-maf/main.py`
  - `agents/*/src/*/gateway_tools.py` (the `_invoke` transport only).
- Per-agent scripting already exists for `directive` in
  `scripts/build_hosted_agent_image.sh` and
  `scripts/assign_hosted_agent_access.sh`; only `scripts/deploy_images.sh` omits
  it (builds `--agent support`).
- Tests pass today: support 9, directive 4 (`python -m unittest`). No pytest in
  the agent test venvs.

## Implementation phases

### Phase 1 — Extract a shared `maf_hosting` package

Create a new repo-root package (sibling of `agent_contracts/`) that both images
install. Proposed name: `maf_hosting` (confirm during review).

**New files**

- `maf_hosting/pyproject.toml` — mirror `agent_contracts/pyproject.toml`
  (setuptools, `package-dir`, py>=3.11). Dependencies limited to what the shared
  code imports (`starlette`, `httpx`, `azure-identity`, `microsoft-opentelemetry`,
  `azure-ai-agentserver-core` — align exact pins with the agents' current
  `requirements.txt`).
- `maf_hosting/__init__.py` — export the public API below.
- `maf_hosting/identity.py`:
  - `configure_observability_identity() -> tuple[str, str | None]` (verbatim
    behavior of today's `_configure_observability_identity`, including the
    `FOUNDRY_AGENT_TENANT_ID`/`ENTRA_TENANT_ID` fallback and the hosted-env
    `FOUNDRY_AGENT_INSTANCE_CLIENT_ID` requirement).
  - `Agent365IdentityMiddleware` (verbatim behavior of today's
    `_Agent365IdentityMiddleware`).
  - `install_agent365_identity_middleware(server, *, tenant_id, agent_id)`
    (verbatim behavior, including the "exactly one create_response route" check).
- `maf_hosting/gateway.py`:
  - `async def invoke_gateway_tool(tool_name, arguments, *, timeout=None) -> dict`
    — the shared `_invoke` transport. Standardize on: read
    `get_request_context()`, validate `user_id`/`session_id`/`call_id`, acquire
    the `APP_TOOL_GATEWAY_SCOPE` token, then open the `httpx.AsyncClient`, POST to
    `{APP_TOOL_GATEWAY_URL}/internal/agent-tools/{tool_name}`, `raise_for_status`,
    and validate the response is a `dict`.
  - `timeout` resolution: explicit arg wins; else env var (name passed by caller,
    default `30.0`). This preserves support's 30s and directive's configurable
    `DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS` (default 180s).
  - Keep a module-level `DefaultAzureCredential` exactly as today.
- `maf_hosting/runtime.py` (optional convenience):
  - `def run_hosted_agent(build_agent: Callable[[], Agent]) -> None` — performs
    the identity bootstrap, constructs `ResponsesHostServer(build_agent())`,
    conditionally installs the middleware, and calls `server.run()`. This is the
    entire body of both current `main()` functions.

**Agent changes**

- `customer-support-maf/src/customer-support-maf/main.py`: delete the local
  identity/middleware/`main()` blocks; import from `maf_hosting`. `build_agent()`
  stays (it wires the two Foundry MCP tools + three local tools). `main()` becomes
  `run_hosted_agent(build_agent)`.
- `directive-rag-maf/src/directive-rag-maf/main.py`: same; keep `_max_iterations`
  here (it is directive-specific) but see Phase 5 for hardening.
- Both `gateway_tools.py`: keep the per-agent `@tool` wrappers and the directive
  `_arguments()`/tuple exactly as they are; replace the private `_invoke` body with
  a call to `maf_hosting.invoke_gateway_tool(...)`, passing the directive timeout
  env for the directive agent.
- Both `requirements.txt`: keep pins; the shared package is installed from source
  in the Dockerfile (below), so no version line is strictly required, but add a
  path/editable reference if the team prefers explicit dependency capture.

**Dockerfile changes (both)**

- Add `COPY maf_hosting /build/maf_hosting` next to the existing
  `COPY agent_contracts ...`.
- Change the install line to
  `pip install /build/agent_contracts /build/maf_hosting -r /build/requirements.txt`.

**Test changes**

- Add `maf_hosting/tests/` covering: identity fallbacks + hosted-env agent-id
  requirement, `Agent365IdentityMiddleware` span-identity behavior (port the
  support test), `install_...` route-wrapping + "exactly one create_response"
  error, and `invoke_gateway_tool` request-context injection + timeout resolution
  (port the directive test).
- Reduce the two agent suites to agent-specific assertions only (support: the two
  Foundry MCP tools are configured; directive: exactly the eight directive tools
  register). Both suites keep passing under `python -m unittest`.

**Acceptance criteria**

- Both agents import identity/middleware/`main()`/gateway transport from
  `maf_hosting`; zero duplicated identity or `_invoke` logic remains.
- Support still registers `knowledge_base_retrieve` + `application_tools` +
  three local tools; directive still registers exactly its eight tools.
- `maf_hosting` unit tests plus both agent suites pass.
- Support's tool HTTP timeout stays 30s by default; directive's stays driven by
  `DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS` (default 180s).

### Phase 2 — Symmetrize the project layout (depends on D1)

Apply the chosen convention to **both** agents so they are structurally
identical.

- If **Option A**: move `agents/directive-rag-maf.Dockerfile` →
  `agents/directive-rag-maf/src/directive-rag-maf/Dockerfile` and
  `agents/directive-rag-maf/deployment/directive-rag-maf-hosted/agent.yaml` →
  `agents/directive-rag-maf/src/directive-rag-maf/agent.yaml`; delete the empty
  `deployment/` tree.
- If **Option B**: move support's `Dockerfile` and `agent.yaml` out to
  `agents/customer-support-maf.Dockerfile` and
  `agents/customer-support-maf/deployment/customer-support-maf-hosted/agent.yaml`.
- Update `DOCKERFILE=` in `scripts/build_hosted_agent_image.sh` for whichever
  path(s) moved.
- Update each `azure.yaml` `project:` to the new manifest folder.
- Keep Dockerfile `COPY` paths repo-root-relative (unchanged); only the file
  location moves.

**Acceptance criteria**

- Both agents share one folder shape.
- `scripts/build_hosted_agent_image.sh --agent support` and `--agent directive`
  both build against the moved paths (dry-run/print the resolved `-f` path).

### Phase 3 — Reconcile the azd/`azure.yaml` build path (depends on D2)

Resolve the mismatch between repo-root `COPY` Dockerfiles and azd's `project`
directory as build context.

- **Spike first:** determine how `host: azure.ai.agent` with
  `docker.remoteBuild: true` resolves the build context (project dir vs. repo
  root) for these Dockerfiles.
- If azd **is** authoritative and supports it: set explicit `docker.path`
  (Dockerfile) and `docker.context` (repo root) in both `azure.yaml` files so the
  repo-root `COPY` resolves; verify an azd remote build succeeds for both.
- If scripts are authoritative: remove the buildable `docker` service intent from
  both `azure.yaml` files (or reduce them to a documented, non-build manifest) and
  add a short note in `README.md`/`docs/IMPLEMENTATION-PLAN.md` that
  `scripts/build_hosted_agent_image.sh` is the single build path.
- Ensure the directive `azure.yaml` no longer points `project:` at a folder with
  no Dockerfile.

**Acceptance criteria**

- Exactly one documented, working build path per agent, identical in shape across
  the two agents, with no manifest pointing at a Dockerfile-less folder.

### Phase 4 — Add the directive agent to the deploy orchestrator

- In `scripts/deploy_images.sh`, add a directive build step symmetric to the
  support one, e.g. `build_hosted_agent_image.sh --agent directive` gated behind
  the directive feature flag / a `--with-directive` argument (default off to avoid
  surprising existing runs).
- Read `directive_foundry_agent_name` / `directive_agent_release_id` from
  terraform outputs (already used by `build_hosted_agent_image.sh`).
- Match whatever publish/rollout step support uses (Container App update vs.
  Foundry publish); if the hosted agents are published via azd/Foundry rather than
  `az containerapp update`, mirror that step, do not invent a Container App update
  for the hosted image.
- Update the script header comment to document the directive path and the flag.

**Acceptance criteria**

- A single documented command builds (and, where applicable, publishes) both
  hosted agents; running without the flag preserves today's support-only behavior.

### Phase 5 — Config robustness (iterations + timeout layering)

- Harden `_max_iterations()` in the directive `main.py`: parse inside a
  `try/except ValueError` and raise the same range-style `RuntimeError` for
  empty/non-numeric input, so a missing `${DIRECTIVE_MAX_ITERATIONS}` substitution
  fails with a clear message instead of a bare `ValueError`. Add a unit test for
  empty and non-numeric values (extend
  `test_iteration_ceiling_is_independent_and_bounded`).
- Verify timeout layering: the agent-side HTTP timeout
  (`DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS`, default 180s) must be **≥** the
  backend-side `directive_tool_timeout_seconds` so the backend owns failure
  semantics and the agent does not abort mid-tool and desync the progress stream.
  Document the relationship in `backend/.env.example` / the agent `agent.yaml`
  comments and, if needed, adjust defaults so agent ≥ backend.

**Acceptance criteria**

- Empty/non-numeric `DIRECTIVE_MAX_ITERATIONS` raises a clear `RuntimeError` at
  boot; a valid value in 1..30 still works.
- The agent-side and backend-side directive timeouts are documented and ordered
  agent ≥ backend.

### Phase 6 — (Optional) Unify the citation strategy

Separate follow-up, not required for consolidation. Evaluate backporting the
directive agent's deterministic server-side citation enrichment
(`_enrich_directive_tool_events` in `foundry_hosted_maf_runtime.py`) to the
support agent so citations no longer rely on the model emitting
`【search_idx:ref_id†source_name】` markup. Track separately if pursued, since it
touches the support UX and the Foundry-IQ citation path.

## Testing strategy

- Run `python -m unittest` for `maf_hosting`, `customer-support-maf`, and
  `directive-rag-maf` (the agent venvs lack pytest; unittest is the contract).
- Run the backend suite (`backend/tests/`, notably `test_dual_agents.py`,
  `test_directive_tools.py`, `test_hardening.py`) to confirm the gateway/runtime
  contract is untouched.
- For build phases, do a print/dry-run of the resolved Dockerfile path and (if
  D2 = azd) one real `az acr build` per agent from repo-root context.

## Rollout / verification

1. Land Phase 1 (shared package + tests) — pure refactor, no behavior change.
2. Land Phase 2 + 3 together (layout + build path) behind D1/D2 decisions.
3. Land Phase 4 (orchestrator) with the directive step defaulting off.
4. Land Phase 5 (config hardening).
5. Rebuild both images via the reconciled path; smoke-test each agent end-to-end
   (support: order/KB/memory; directive: resolve→content→citations + progress
   stream). Confirm Application Insights still receives Agent 365 identity spans.

## Risks & mitigations

- **Shared-package dependency creep** — keep `maf_hosting` deps minimal and pinned
  to the agents' existing versions; it must not pull in `agent_framework` or
  backend packages.
- **azd build-context uncertainty (D2)** — gate Phase 3 on the spike; do not edit
  `azure.yaml` build settings until the context behavior is confirmed.
- **Orchestrator surprise** — default the directive build off in
  `deploy_images.sh`; require an explicit flag.
- **Hidden behavior drift during extraction** — port existing tests verbatim into
  `maf_hosting/tests/` first, then delete the duplicated agent copies, so the
  shared code is proven equivalent before removal.

## Out of scope

- Any change to agent prompts, tool schemas, or the public citation model
  (except the optional Phase 6 spike).
- Backend runtime, gateway, `config.py`, principal allow-lists, and terraform.
- Merging the two agents into a single image or runtime.
