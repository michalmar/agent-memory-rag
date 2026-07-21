# Implementation Plan - Selectable Dual Foundry Agents

**Status:** Version 7 application and published-channel acceptance passed;
Agent 365 Defender-table provisioning pending

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
| Hosted tools | Foundry IQ plus Agent Identity-authenticated stateless MCP; profile/memory remain on the session-bound gateway |
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
- [x] Added a separate stateless MCP surface for app-wide order lookup.
- [x] Reuses the same app-role, audience, issuer, tenant, and principal allowlist
  policy for MCP and the session-bound gateway.

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
- [x] Uses Foundry IQ MCP, Agent Identity-authenticated application MCP, and async
  personal-data gateway wrappers routed through the public frontend.
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

### Phase 12 - Hosted Agent identity and Agent 365 remediation

- [x] Reproduced the private `get_order_status` gateway `403` in both Foundry and
  Microsoft 365 and separated it from the later Agent 365 exporter `403`.
- [x] Deployed Hosted Agent version 3 and captured the failed acceptance trace:
  local `get_order_status` selected correctly, but the attempted manual blueprint
  exchange returned `401`.
- [x] Confirmed the Foundry-created federated credential is for Agent Service's
  platform exchange, not the Hosted container's ordinary managed identity.
- [x] Confirmed direct published-channel calls have no frontend-created
  user/session binding, so owner-scoped gateway dispatch is not a valid app-only
  channel architecture.
- [x] Removed the unsupported in-container `fmi_path` exchange and its custom
  identity variables.
- [x] Added `customer-support-tools-mcp` as a `RemoteTool` connection using
  `AgenticIdentityToken` and the application API audience.
- [x] Added an authenticated, stateless FastMCP endpoint exposing only
  `get_order_status`; personal profile/memory tools remain session-bound.
- [x] Deployed version 4 and reproduced the Responses API validation failure
  caused by supplying `project_connection_id` without the separately required
  MCP `server_url`.
- [x] Added the public `/api/mcp/` URL to the Hosted MCP descriptor while
  retaining the project connection for Agent Identity authentication, plus a
  regression test for both fields.
- [x] Deployed version 5 and confirmed the MCP connection reached the endpoint,
  but direct Foundry execution used the shared project Agent Identity, which did
  not yet have `AgentTools.Invoke`.
- [x] Added the shared project Agent Identity to the strict MCP allowlist and
  extended setup automation to grant it only `AgentTools.Invoke`; the published
  identity retains its separate tool and Agent 365 grants.
- [x] Set the MAF `Agent.id` from `FOUNDRY_AGENT_INSTANCE_CLIENT_ID` so Agent 365
  export no longer partitions spans under the SDK-generated agent UUID.
- [x] Added MCP discovery, order-result, and token-policy tests.
- [x] Extended the identity setup script to grant `AgentTools.Invoke` and
  `Agent365.Observability.OtelWrite` and configure the MCP connection in azd.
- [x] Added and validated the Hosted Agent `agent.yaml` runtime contract.
- [x] Made identity setup repair the concrete Foundry project endpoint so azd
  cannot persist a self-referential `${FOUNDRY_PROJECT_ENDPOINT}` value; the
  existing-project endpoint is explicit in the nested deployment manifest.
- [x] Deployed backend MCP release `mcp-agent-id-20260720-r3`.
- [x] Deployed corrected Hosted Agent release `mcp-agent-id-20260720-r4` as
  version 5; the request reached MCP and isolated the shared-identity
  authorization gap.
- [x] Applied the shared project Agent Identity role and allowlist correction;
  version 5 returns authoritative `ORD-003` data through MCP.
- [x] Confirmed Agent 365 export no longer reports missing
  `Agent365.Observability.OtelWrite`.
- [x] Isolated the remaining Agent 365 drop: eligible `invoke_agent` spans have
  the published agent ID but lack `microsoft.tenant.id`.
- [x] Deployed release `mcp-agent-id-20260720-r5` as version 6 and confirmed
  `ORD-003` remains authoritative.
- [x] Confirmed version 6 still logs `No eligible genAI spans to export`: the
  environment fallback is visible to Agent Server logging, but
  `microsoft.tenant.id` is absent from the completed `invoke_agent` span when
  Agent 365 filters it.
- [x] Added request-scoped Agent 365 `BaggageBuilder` enrichment after inbound
  trace-context extraction, using the resolved tenant and
  `FOUNDRY_AGENT_INSTANCE_CLIENT_ID`.
- [x] Added a regression test proving the resulting `invoke_agent` span reaches
  `filter_and_partition_by_identity` under the published tenant/agent key.
- [x] Deployed release `mcp-agent-id-20260720-r6` as version 7 and confirmed
  authoritative order retrieval, grounded Foundry IQ, complete Agent 365 identity
  attributes, successful exporter token acquisition, and no HTTP/export error.
- [x] Audited the initial tenant subscriptions and confirmed that neither
  Microsoft 365 E7 nor Microsoft Agent 365 was present. Microsoft 365 Copilot and
  E5 assignments alone did not satisfy the documented ingestion prerequisite.
- [x] Activated 25 `AGENT_365` seats and assigned both test users. The
  `AGENT_365`, `DEFENDER_FOR_AI`, and `AUDIT_FOR_AGENTS` service plans report
  successful provisioning.
- [x] Verified the published package is unblocked, allowed tenant-wide, backed by
  Agent Identity `a15ba753-8d64-45a3-a34c-5fb507ce34a8`, and supports both Teams
  and Copilot.
- [x] Retested `ORD-003` through the published channel. Trace
  `0d83c8cfa56a5bc8d8d932ce4177d042` called `get_order_status` and returned
  `delivered`, `Delivered Jan 20, 2026`.
- [x] Retested Foundry IQ through the published channel. Trace
  `d383ced605d6bd23a0112584a023bf70` called `knowledge_base_retrieve`, retrieved
  three policy documents, and returned the grounded 30-day policy.
- [x] Proved the similarly named `agent-framework-agent-foundry-` package was a
  separate Hosted Agent in `rg-ai/demo-swe/demo-swe-prj`, not an earlier version
  of `customer-support-maf-hosted`.
- [x] Blocked legacy package `T_8f4394fb-7025-5b06-1413-b93e8d5e46b8`, force
  deleted Hosted Agent `agent-framework-agent-foundry-skills-responses` and
  versions 1-3, and deleted generated Bot Service
  `agent-framework-agent-foun51016`.
- [x] Verified the platform removed dedicated Agent Identity
  `4c413efd-d9f4-4619-b27c-d86b3b05193f`, blueprint
  `6e0057e9-ae01-4b2f-83a3-5c0241e93ae8`, and residual RBAC. Agent Registry
  reconciliation removed the blocked package.
- [x] Preserved the shared `demo-swe` project and its four unrelated agents.
  `customer-support-maf-hosted` remains enabled on active version 7, and a
  post-cleanup `ORD-003` request returned the authoritative delivered result.
- [ ] Verify either published-channel trace in Defender `CloudAppEvents`. Graph
  advanced hunting currently reports that the table does not exist, so tenant
  Defender/Agent 365 backend provisioning remains incomplete.

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
- Let Foundry Agent Service perform Agent Identity authentication for app-only
  MCP tools; do not recreate the platform exchange inside the Hosted container.
- Never use an app-only Agent Identity as owner context for profile or
  conversation-memory access.
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
- Hosted Agent reaches the app-role-protected MCP and session gateway through the
  public frontend proxy, which forwards to the internal backend.
- Both agents retrieve from the same Foundry IQ KB and return citations on
  Foundry/application surfaces; published Microsoft 365 and Teams channels
  currently suppress citation rendering.
- Published channels can call stateless order MCP; personal profile/memory tools
  require application binding or a future OAuth identity-passthrough connection.
- Two users cannot access each other's application or Foundry state.
- Delegated user tokens and wrong Hosted principals cannot call the gateway.
- The Agent 365 exporter reaches its authenticated HTTP path independently of
  Application Insights ingestion; downstream acceptance is verified separately
  and requires an eligible tenant license assignment.
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
