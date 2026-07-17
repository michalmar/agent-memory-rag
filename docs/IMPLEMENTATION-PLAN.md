# Implementation Plan - Selectable Dual Foundry Agents

**Status:** Implemented, deployed, and accepted

## 1. Target

Add one Microsoft Foundry Basic Setup account/project containing:

- `customer-support-prompt`: native Foundry Prompt Agent;
- `customer-support-maf-hosted`: Foundry Hosted Agent implemented with Microsoft
  Agent Framework and Hosted Responses protocol `2.0.0`.

Both agents use Foundry IQ and share normalized result/citation envelopes and the
AG-UI stream. The Prompt Agent is knowledge-only. Hosted MAF additionally uses the
strict application tools and owner-scoped application data.

## 2. Locked decisions

| Area | Decision |
| --- | --- |
| Agent choice | Required for new conversations and immutable afterward |
| Retrieval | Foundry IQ only; no `classic`, `none`, or runtime RAG selector |
| Trust boundary | FastAPI authenticates users, owns application data, persists routing, and emits AG-UI |
| Prompt tools | Foundry IQ `knowledge_base_retrieve` only |
| Hosted tools | Foundry IQ plus app-only backend gateway through the public frontend proxy |
| Networking | Public Entra-only Foundry/Search/ACR endpoints; private backend and Cosmos |
| Backend identity | Existing application UAMI |
| Hosted identity | Foundry-created service principal; no UAMI support in preview |
| Agent state | Foundry conversations; private mapping persisted in Cosmos schema v3 |
| Runtime coordination | In-memory mappings and locks, single backend replica, durable restoration from Cosmos |
| Rollback | Keep current Foundry generation and prior agent versions during soak |

## 3. Implementation phases

### Phase 0 - Platform and SDK validation

- [x] Confirmed private outbound isolation requires Standard Setup and BYO resources.
- [x] Confirmed Hosted Agents can use platform-managed default session storage.
- [x] Selected Basic Setup to comply with the tenant policy that disables Storage
  shared-key access.
- [x] Confirmed Hosted Responses protocol `2.0.0`.
- [x] Pinned `azure-ai-projects==2.3.0`,
  `agent-framework-foundry==1.10.1`, and
  `agent-framework-foundry-hosting==1.0.0a260709`.
- [x] Confirmed project and Hosted Agent identity requirements.
- [x] Confirmed East US 2 model quota.

### Phase 1 - Shared contracts

- [x] Added `agent_contracts/`.
- [x] Added strict Pydantic schemas for the four Hosted application tools.
- [x] Added shared result/citation and normalized event models.
- [x] Added separate deterministic prompt versions for the IQ-only Prompt Agent and
  the application-tool Hosted agent.
- [x] Removed identity fields from model-visible tool schemas.
- [x] Removed the legacy direct Chat Completions runner and classic RAG client.

### Phase 2 - Durable runtime state

- [x] Added Cosmos schema v3 agent descriptors and private runtime state.
- [x] Added explicit public summary/detail allowlists.
- [x] Added ETag conditional replacement.
- [x] Added per-conversation overlap rejection.
- [x] Added owner-partitioned Hosted session lookup.
- [x] Added lazy migration of existing conversations to Hosted MAF.
- [x] Added cleanup when remote state cannot be persisted.

### Phase 3 - App-only Hosted tool gateway

- [x] Added application-only token validation.
- [x] Rejects delegated `scp` tokens.
- [x] Requires `AgentTools.Invoke`.
- [x] Requires an allowlisted Hosted Agent principal.
- [x] Validates user/session binding before dispatch.
- [x] Uses shared async handlers under trusted context.
- [x] Returns typed error envelopes without sensitive payload logging.

### Phase 4 - Policy-compliant hybrid Foundry infrastructure

- [x] Added an additive Foundry `AIServices` account using Basic Agent Setup.
- [x] Enabled public Entra-only Foundry access for backend and Hosted invocation.
- [x] Removed outbound network injection and Standard Setup state resources.
- [x] Enabled public Entra-only KB Search access for all clients and removed
  incompatible private endpoint/DNS paths for non-injected runtimes.
- [x] Added chat and embedding deployments.
- [x] Added the Foundry IQ `RemoteTool` project connection using
  `ProjectManagedIdentity`.
- [x] Added project, backend, ACR, Search, and telemetry RBAC.
- [x] Added backend feature flags and safe Terraform outputs.
- [x] Removed the empty resources left by the interrupted Standard Setup apply;
  the final Terraform plan has no changes.

### Phase 5 - Hosted Microsoft Agent Framework agent

- [x] Added Hosted Agent source and Dockerfile.
- [x] Uses `FoundryChatClient`, `Agent`, and `ResponsesHostServer`.
- [x] Uses async Azure Identity.
- [x] Uses Foundry IQ MCP plus async gateway wrappers routed through the public
  frontend to the internal backend.
- [x] Reads Foundry request context for user/session/call IDs.
- [x] Enforces five function-invocation iterations.
- [x] Built and pushed the immutable image through public Entra/RBAC-only ACR.
- [x] Deployed Hosted Agent version and captured
  `instance_identity.principal_id`.
- [x] Assigned `AgentTools.Invoke` and updated the backend allowlist.

### Phase 6 - Native Prompt Agent

- [x] Added idempotent Prompt Agent release tooling.
- [x] Publishes prompt/retrieval/release hashes.
- [x] Uses Foundry IQ MCP with project managed identity.
- [x] Exposes only `knowledge_base_retrieve`; no application function tools.
- [x] Added a dedicated knowledge-only prompt and async Responses adapter.
- [x] Sends trusted user identity on conversation operations.
- [x] Published and activated the Prompt Agent in the new project.

### Phase 7 - Unified runtime and AG-UI

- [x] Added application-owned async `AgentRuntime` protocol.
- [x] Added Prompt and Hosted remote adapters with lifecycle methods.
- [x] Added normalized stream parsing and AG-UI conversion.
- [x] Added immutable routing and no-failover behavior.
- [x] Added runtime/dependency readiness.
- [x] Preserves a precreated Hosted session ID if a completed response omits it.

### Phase 8 - Frontend

- [x] Added the two-agent selector.
- [x] Locks selection after conversation creation.
- [x] Restores safe agent metadata from history.
- [x] Added runtime badges and fixed Foundry IQ indicator.
- [x] Removed the RAG selector.
- [x] Preserved identity-change and late-stream guards.

### Phase 9 - Tests, security, and documentation

- [x] Added tests for strict tools and typed validation.
- [x] Added tests for gateway binding and app-only token policy.
- [x] Added tests for immutable routing and remote-state cleanup.
- [x] Added tests for ETag writes and runtime-state privacy.
- [x] Added tests for tenant-scoped identity and owner-partitioned lists.
- [x] Added Hosted session continuity and trusted identity-header tests.
- [x] Updated PRD, implementation plan, environment examples, and READMEs.
- [x] Completed Azure validation and deployment verification.

### Phase 10 - Staged rollout

- [x] Deployed Foundry Basic Setup, the Hosted MAF agent, and Prompt Agent version 3.
- [x] Verified the Prompt definition contains only Foundry IQ
  `knowledge_base_retrieve`.
- [x] Deployed backend revision `ca-agmem-backend--0000020`.
- [x] Verified Prompt and Hosted Foundry IQ grounding with citations.
- [x] Verified Prompt requests cannot invoke application tools.
- [x] Verified Hosted order lookup through the app-role-protected gateway.
- [x] Verified delegated gateway rejection and immutable per-conversation routing.
- [x] Enabled both selectors after acceptance.
- [x] Confirmed the final Terraform plan has no changes.

### Phase 11 - Cosmos semantic-memory cutover

- [x] Added the `support/memories` container with `/user_id` partitioning,
  3,072-dimensional cosine embeddings, and a `quantizedFlat` vector index.
- [x] Replaced semantic-memory store internals with async Cosmos CRUD and vector
  queries while preserving the public API contract.
- [x] Made semantic memory an optional readiness dependency so its outage cannot
  remove agents or thread history from ingress.
- [x] Verified create, idempotent upsert, list, vector search, delete, and Hosted
  `check_memory` behavior against the deployed application.
- [x] Removed the retired database, private endpoint/DNS, bootstrap identity/job,
  setup image, configuration, and deployment steps.

## 4. Azure topology

| Component | Region | Notes |
| --- | --- | --- |
| Foundry/Container Apps/VNet | East US 2 | Foundry Basic Setup uses public Entra-only ingress; Container Apps retains its VNet |
| Cosmos DB | East US 2 | Private endpoint; serverless history, profile, and vector-memory containers |
| KB Search | West Europe | Public Entra-only endpoint for Foundry IQ and setup clients |

Interactive jumpboxes and Bastion are not part of the deployed topology.

## 5. Security rules

- Never enable public access on application Cosmos or the backend
  Container App.
- Public Foundry, KB Search, and ACR must remain Entra/RBAC-only with local/key,
  admin, and anonymous authentication disabled as applicable.
- Use the Foundry project endpoint, not an unmanaged Hosted Agent URL.
- Never grant the Hosted identity direct application-data roles.
- Never accept browser- or model-provided identity as authorization context.
- Never expose private runtime IDs in API responses or telemetry.
- Never silently reroute an existing conversation to another agent.
- Stop deployment if Terraform proposes deletion/replacement of the current
  Foundry account or model deployments.

## 6. Validation gates

### Application

- Backend unit suite passes.
- Frontend TypeScript build passes.
- Shared/backend/Hosted Python source compiles.
- Prompt release module imports with pinned SDK versions.
- Hosted image dependencies resolve at pinned versions.

### Infrastructure

- `terraform fmt -check` passes.
- `terraform validate` passes.
- The live plan has no changes or replacements.
- Public/private endpoints, DNS, project connection, and role scopes match the
  approved Basic Setup design.
- Existing Foundry account and deployments are no-op.

### Live acceptance

- Backend invokes both agents through the public Entra/RBAC-only project endpoint.
- Hosted Agent reaches the app-only gateway through the public frontend proxy,
  which forwards to the internal backend.
- Both agents retrieve from the same Foundry IQ KB and return citations.
- Two users cannot access each other's application or Foundry state.
- Delegated user tokens and wrong Hosted principals cannot call the gateway.
- Persistence failures emit run errors before `RUN_FINISHED`.
- Feature flags expose only runtimes that pass readiness.

## 7. Rollback

- Disable new conversations for the affected runtime.
- Reactivate the prior Prompt or Hosted version.
- Keep existing conversation metadata unchanged.
- Do not delete an agent as routine rollback.
- Keep the new account if it contains active conversations.
- Delete the current Foundry generation only in a separate approved cleanup after
  migration, soak, and rollback-window completion.
