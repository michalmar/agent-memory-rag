# Agentic AI Memory - Foundry Agent Chat

Reference implementation of a secure agent chat application with five memory
layers, Foundry IQ retrieval, two support agents, and a separately deployed
directive RAG agent in one Microsoft Foundry project.

The current product and architecture source of truth is
[`docs/PRD-Solution-Challenges-1-5.md`](docs/PRD-Solution-Challenges-1-5.md).
[`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md) is the delivery record,
not an alternative architecture.

## Current architecture

| Agent | Runs in | Capabilities |
| --- | --- | --- |
| **Foundry Prompt Agent** | Native Foundry Prompt Agent | Foundry IQ `knowledge_base_retrieve` only |
| **Hosted Agent Framework** | Foundry Hosted Agent using Microsoft Agent Framework | Foundry IQ, an Agent Identity-authenticated order MCP tool, and app-session profile/memory tools |
| **Directive Assistant** | Separate Foundry Hosted Agent using Microsoft Agent Framework | Eight strict backend directive tools for discovery, long-form content, comparisons, linked directives, and mandatory-status labeling; enabled for the current internal pilot |

Agent selection is required for a new conversation and immutable afterward. All
configured agents emit the same normalized AG-UI stream, but they intentionally
have different application capabilities. The Directive Assistant is visible in
the current pilot environment; its independent enablement and visibility gates
remain the rollback controls.

```mermaid
flowchart LR
    U[Browser] --> F[Public Frontend ACA]
    F --> B[Internal FastAPI ACA]
    B -->|Application UAMI| P[Native Prompt Agent]
    B -->|Application UAMI| H[Hosted MAF Agent]
    B -->|Application UAMI| D[Directive Hosted MAF Agent]
    P --> IQ[Foundry IQ]
    H --> IQ
    IQ --> S[Public Entra-only Azure AI Search]
    H -->|AgenticIdentityToken / Remote MCP| F
    D -->|Agent Identity / directive gateway| F
    F -->|Authenticated MCP and restricted API proxy| B
    B --> C[(Private Cosmos DB: history, profile, semantic memory)]
    B --> R[(Private Cosmos DB: directive catalog and content)]
    B --> A[(Private Blob: immutable directive artifacts)]
    B -->|Source-manager UAMI| X[(Private Blob: directive source PDFs)]
    J[Manual directive ingestion job] -->|Reader UAMI| X
    J --> A
    J --> R
    J --> S
    B -->|Direct hybrid directive queries| S
    B --> M[Active Foundry Models]
    S -->|Vectorization and support IQ planning| M
    P --> O[Project Application Insights]
    H --> O
    B -->|UAMI / AMPLS| O
```

FastAPI remains the authentication, authorization, conversation registry,
persistence, tool-policy, and public API boundary. The Hosted Agent never receives
direct application-data roles.

Publishing the stable endpoint to Microsoft 365 Copilot or Teams does not change
these runtime boundaries. Those channels currently suppress streaming and citation
rendering. Stateless Agent Identity-authenticated MCP tools such as order lookup
remain available, while owner-scoped profile and conversation memory require the
application-created user/session binding and are not exposed as app-only published
channel tools.

## What is implemented

- **Backend** (`backend/`) - FastAPI application with AG-UI SSE chat, owner-scoped
  conversation/profile/memory APIs, remote Foundry adapters, an app-role-protected
  stateless MCP endpoint, a session-bound Hosted tool gateway, privacy-safe
  telemetry, Cosmos-authoritative directive tools, deterministic direct hybrid
  directive Search with cross-intent RRF, protected exact-version directive
  document/PDF endpoints, a role-protected directive source manager, and bounded
  liveness/readiness endpoints.
- **Agent contracts** (`agent_contracts/`) - separate versioned prompts, strict
  application-tool schemas, runtime state, citation/result envelopes, and
  normalized agent events.
- **MAF hosting foundation** (`maf_hosting/`) - shared Hosted Agent identity,
  observability middleware, gateway transport, runtime startup, and
  directive-only stateful continuation.
- **Native Prompt release** (`setup/agents/`) - idempotently publishes an immutable
  Prompt Agent definition containing exactly one Foundry IQ MCP tool.
- **Hosted MAF agent** (`agents/customer-support-maf/`) - uses
  `FoundryChatClient`, `Agent`, and `ResponsesHostServer` with Hosted Responses
  protocol `2.0.0`; support-only compatibility pins keep stateless
  `gpt-4o-mini` calls from requesting unsupported encrypted reasoning content.
- **Directive Hosted MAF agent** (`agents/directive-rag-maf/`) - separately
  packaged GPT-5.6 agent with exactly eight RequestContext-backed gateway tools
  and no support IQ, order, profile, memory, or direct data-plane access. A
  backend-owned inner Foundry conversation and `AgentSession` provide
  `store=true` continuation without replaying prior outer history after
  bootstrap.
- **Frontend** (`frontend/`) - Vite + Lit SPA with a login-first Entra gate,
  immutable agent selection, Markdown/citation streaming, an accessible
  Markdown-first directive document viewer with authenticated original-PDF
  loading and section/page citation navigation, a metadata-only directive source
  rail for approved operators, and a constrained A2UI subset for internal tool
  cards.
- **Infrastructure** (`infra/`) - Terraform for Foundry Basic Setup, Container Apps,
  Search, Cosmos DB catalog/content containers, ACR, private endpoints, monitoring, managed
  identities, and least-privilege RBAC.
- **Direct Foundry release** (`scripts/release_foundry_assets.sh`) - configures
  Search/Foundry IQ and publishes the Prompt Agent without setup containers.

### Published directive documents

Directive entries in an answer's **Documents** section open the exact published
version at the top in a responsive side drawer. The default **Document** tab
renders the canonical Markdown with a shared `marked` + DOMPurify policy.
Relative references to another directive PDF are converted into authenticated
viewer actions instead of direct storage links.

Inline directive citations and rows in the detailed **Sources** list open that
same exact-version drawer at the cited Markdown section. A **Cited location**
banner shows the source number, section, and PDF page range; the matching heading
is scrolled into view and focused. Moving between citations in one open document
does not refetch it. If the section cannot be identified uniquely, the document
stays at the top rather than navigating to a guessed heading.

The **Original PDF** tab is loaded only on demand. The SPA fetches the PDF through
the delegated-token API, creates a short-lived browser Blob URL, and supports the
native viewer, open-in-new-tab, and download flows. Citation page metadata adds a
`#page=` fragment when available. Closing or replacing the document aborts stale
requests, revokes Blob URLs, and restores focus to the latest triggering
document or citation control.

The backend resolves only an exact published catalog version and exposes:

- `GET /directives/{directive_id}/versions/{directive_version_id}/document` for
  canonical Markdown and safe document metadata;
- `GET /directives/{directive_id}/versions/{directive_version_id}/source` for the
  streamed original PDF.

Both routes require the same Entra delegated authentication as other browser
APIs. Blob names and storage URLs remain private, public Blob access stays
disabled, and the browser receives neither a SAS token nor direct Blob
coordinates. Document entries restored from conversation history retain the same
viewer behavior.

Published directive data has one runtime authority per artifact:

| Data | Runtime authority | Derived or supporting store |
| --- | --- | --- |
| Operator-managed ingestion corpus | Private Blob `directive-source` container | Input only; never read by agents |
| Version metadata, manifest, summary, and private document locators | Cosmos `catalog` version bundle | None |
| Ordered section text | Immutable Cosmos `directive_content` items | Azure AI Search is a derived retrieval projection |
| Complete canonical Markdown and published PDF copy | Private Blob `directive-artifacts` container | Located through the published version bundle |

Legacy catalog records are not read or migrated. After this schema change,
existing directive versions must be republished by ingestion.
Failed ingestion inputs retain the existing private quarantine artifacts; they
are not runtime content authorities.

Approved operators with the `DirectiveSource.Manage` Entra application role can
open the **Sources** rail to list filename, size, and last-modified metadata,
upload a uniquely named PDF, or confirm deletion. Uploads are create-only and use
the `<eight-digit-id>-<name>-v<number>.pdf` contract. The rail does not preview,
download, rename, overwrite, or expose Blob coordinates. Upload and deletion do
not trigger ingestion, and deleting a source leaves previously published data
untouched.

Production ingestion reads the current `directive-source` blobs only when the
manual job runs. Local development keeps folder-based ingestion through
`DIRECTIVE_SOURCE_KIND=local`.

## Five memory layers

1. **Session memory** - Foundry conversations plus bounded in-memory runtime
   mappings and per-conversation locks.
2. **Conversation history** - Cosmos DB, partitioned and queried by tenant-scoped
   authenticated user ID.
3. **Semantic conversation memory** - owner-partitioned Cosmos DB documents with
   3,072-dimensional cosine vector search.
4. **User profile memory** - owner-partitioned Cosmos DB profile documents.
5. **Enterprise knowledge** - Foundry IQ for support knowledge plus backend-owned
   direct hybrid Azure AI Search retrieval for directive evidence and citations.

The backend is intentionally pinned to one replica because Redis-based distributed
session coordination is not part of this implementation.

## Security and networking

| Component | Network exposure | Identity model |
| --- | --- | --- |
| Frontend Container App | Public | Entra delegated user tokens; app-only Hosted tool route |
| Backend Container App | Internal ACA ingress | Application UAMI and backend token validation |
| Foundry agent account/project and models | Public only | Entra/RBAC only; local auth disabled |
| Azure AI Search / Foundry IQ | Public only | Entra/RBAC only; local auth disabled |
| Azure Container Registry | Public plus private endpoint | Entra/RBAC only; admin and anonymous pull disabled |
| Cosmos DB | Private endpoint only | Application UAMI reads directive bundles/content and user data; local auth disabled |
| Directive source Storage | Private endpoint only | Ingestion UAMI reads; backend UAMI lists, creates, and deletes; browser and Hosted identities have no data-plane role |
| Directive artifact Storage | Private endpoint only | Backend UAMI reads complete canonical Markdown and published PDFs; ingestion writes immutable and quarantine artifacts; shared keys and public Blob access disabled |
| Application Insights / Log Analytics | Public Foundry platform path plus private AMPLS path for ACA | Foundry project connection; backend UAMI |

The public Foundry, Search, and ACR endpoints are required by non-VNet-injected
Foundry runtimes. Foundry and Search intentionally have no private endpoints. ACR
retains a private path for Container Apps image pulls.

Foundry uses Basic Setup with platform-managed agent state. Standard Setup and BYO
Storage are intentionally not used because tenant policy disables Storage
shared-key access.

The Foundry project is connected to the workspace-based Application Insights
resource. Prompt platform traces, Hosted MAF traces/dependencies, backend telemetry,
and Foundry diagnostics use the same project workspace. Foundry tracing requires
public ingestion and a connection string; this is the documented exception to the
managed-identity-only preference. Trace reads remain Entra/RBAC-controlled with
30-day retention. Full Foundry traces can contain user, model, retrieval, and tool
content.

Agent 365 export is a separate destination. The Hosted Agent identity must have
`Agent365.Observability.OtelWrite`, and exported spans must include both
`gen_ai.agent.id` and `microsoft.tenant.id`. An Agent 365 authorization or
eligibility failure does not disable Application Insights ingestion or change an
agent response.

Agent 365 ingestion is also tenant-gated. At least one user in the tenant must
have a Microsoft 365 E7 or Microsoft Agent 365 license assigned; having only
Microsoft 365 Copilot or E5 is not the documented ingestion entitlement. Without
an eligible assignment, Agent 365 can return HTTP `200` with
`partialSuccess: null` while silently discarding the entire request. After the
first eligible run, wait about five minutes and verify the agent ID in Defender
`CloudAppEvents`; HTTP success alone is not acceptance. See the official
[Agent 365 observability prerequisites](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/direct-open-telemetry-integration#prerequisites)
and
[troubleshooting flow](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/direct-open-telemetry-troubleshooting#verifying-ingestion).

### Authorization boundaries

- Browser APIs require a validated single-tenant Entra token with
  `access_as_user`.
- User ownership keys are derived as `tid:oid`; APIs do not accept caller-supplied
  user IDs.
- The backend uses a user-assigned managed identity for Foundry, Cosmos DB,
  Search, ACR, and telemetry.
- Foundry Agent Service performs the Agent Identity token exchange for the
  `customer-support-tools-mcp` `RemoteTool` connection and sends the resulting
  token to `/api/mcp/`. The Hosted MCP descriptor supplies both the server URL
  and project connection ID; application code does not implement `fmi_path`
  exchange.
- The shared project Agent Identity has only `AgentTools.Invoke`. Each published
  Hosted Agent identity is granted its own `AgentTools.Invoke` and
  `Agent365.Observability.OtelWrite` assignments. The directive identity has no
  direct Search, Cosmos, Blob, mandate, or model data-plane role.
- Hosted MCP and gateway tokens must be application-only, contain the required role, and
  come from an allowlisted principal. Delegated `scp` tokens are rejected.
- Stateless order lookup is exposed through MCP without impersonating a user.
  Profile and conversation-memory dispatch still verifies the stored user/session
  binding before accessing owner-scoped data.
- The MAF `Agent.id` uses the platform-provided
  `FOUNDRY_AGENT_INSTANCE_CLIENT_ID`, keeping Agent Framework spans and Agent 365
  export aligned with the authorized Agent Identity. Agent Server uses the
  platform-provided `FOUNDRY_AGENT_TENANT_ID` when present; otherwise startup
  bridges the deployment's `ENTRA_TENANT_ID` before observability initializes.
  The create-response route then establishes Agent 365 `BaggageBuilder` context
  with the resolved tenant and published identity after inbound trace-context
  extraction. This ensures `invoke_agent` and child spans receive both required
  identity attributes before exporter eligibility filtering.
- Conversation DTOs use explicit allowlists and exclude private runtime IDs,
  owner keys, ETags, and Cosmos metadata.
- Authenticated profile and memory APIs intentionally return only the current
  user's profile and memory content. Telemetry excludes that content, identities,
  messages, tokens, and tool arguments.

## Async runtime model

- Azure-backed stores and runtimes are asynchronous and expose explicit
  initialize/close lifecycles.
- Cosmos history, profile, and semantic-memory stores use
  `azure.cosmos.aio.CosmosClient`.
- Runtime Azure SDK and HTTP clients are asynchronous.
- Agent streams are consumed with `async for`.
- Synchronous JWT/JWKS work is isolated from the event loop.
- Shutdown closes credentials, clients, pools, and refresh tasks.
- Persistence and remote invocation failures are surfaced rather than converted to
  success-shaped fallbacks.

## Run locally

Local mode uses mock users and mock agent runtimes. It does not require Azure.

### Backend

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11:

```bash
cd backend
uv venv --python 3.11
uv pip install --python .venv/bin/python -e ../agent_contracts -e .
.venv/bin/python -m uvicorn agent_memory_backend.server:app --port 8000
```

### Frontend

Requires Node.js 20+:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5175. The frontend proxies `/api` to
`http://localhost:8000`.

Mock users are `user-alice`, `user-bob`, and `user-charlie`. Mock orders are
`ORD-001` (shipped), `ORD-002` (processing), and `ORD-003` (delivered). The local
Prompt mock remains knowledge-only; order-tool behavior belongs to the Hosted MAF
mock.

## Entra app registration

Terraform manages Azure subscription resources, but the SPA/API app registration
is intentionally manual because it requires Entra directory permissions such as
Application Administrator or `Application.ReadWrite.All`.

Create or update it with:

```bash
AZURE_CONFIG_DIR="$HOME/.azure-365" \
  ./scripts/create_entra_app.sh \
    --frontend-url https://<frontend-fqdn> \
    --localhost
```

The script:

- exposes the delegated `access_as_user` scope;
- defines the `AgentTools.Invoke` application role;
- defines the user-assignable `DirectiveSource.Manage` application role;
- configures v2 access tokens and SPA redirect URIs;
- preauthorizes Azure CLI for test-token acquisition;
- prints the tenant and client values required by Terraform.

The first run creates the app. Later updates must pass the printed client ID
explicitly with `--app-id`; the script never reuses an application based only on
its non-unique display name.

For v2 access tokens, configure the backend user-token audience as the client-ID
GUID. Hosted identity token acquisition still uses
`api://<client-id>/.default`.

Assign `DirectiveSource.Manage` to approved users or groups on the app's
Enterprise Application. The delegated scope alone does not authorize source
management.

Example authenticated request:

```bash
TOKEN=$(az account get-access-token \
  --scope api://<client-id>/access_as_user \
  --query accessToken -o tsv)

curl -H "Authorization: Bearer $TOKEN" \
  https://<frontend-fqdn>/api/me
```

## Deployment model

Application-only continuous deployment is defined in
`.github/workflows/deploy-app.yml`. A matching push to `main` rebuilds both
application images through ACR Tasks, updates the backend and frontend Container
Apps in sequence, and verifies the public application. The required GitHub OIDC
secrets and repository variables are documented in the
[minimal GitHub deployment plan](docs/TEMP-plan-minimal-github-cicd.md).

For a new Microsoft Entra tenant or subscription, follow the
[cross-tenant Azure deployment runbook](docs/CROSS-TENANT-AZURE-DEPLOYMENT.md).
It separates Contributor work from Microsoft Entra administration and Azure
authorization tasks, and documents the target-specific Terraform and Hosted
Agent changes required before deployment.

1. Configure `infra/terraform.tfvars` from
   `infra/terraform.tfvars.example`.
2. Provision Azure resources and RBAC with Terraform.
3. Create or update the Entra app roles, assign `DirectiveSource.Manage` to
   approved operators, and deploy the backend/frontend application images.
4. Upload the initial PDFs through the **Sources** rail.
5. Run `scripts/deploy_directive_ingestion.sh <release>` to build the ingestion
   image, verify exact source-reader/artifact-contributor roles, run preflight,
   publish the current source corpus, and verify the resulting state.
6. Run `scripts/release_foundry_assets.sh all` to configure Search/Foundry IQ and
   publish the native Prompt Agent directly.
7. Build and deploy each selected Hosted MAF image to the Foundry project through
   its azd project; local Docker is not required.
8. Configure the generated Hosted Agent identity, including the application MCP
   and Agent 365 roles plus the MCP connection ID:

   ```bash
   AZURE_CONFIG_DIR="$HOME/.azure-365" COPILOT_HOME="$HOME/.copilot" \
     ./scripts/assign_hosted_agent_access.sh \
       --principal-id <hosted-agent-principal-id> \
       --api-app-id <application-client-id> \
       --azd-project-dir agents/customer-support-maf
   ```

   This step requires Application Administrator or Global Administrator.
9. Deploy backend/frontend images and enable agents only after readiness and live
   acceptance pass.

`scripts/build_hosted_agent_image.sh` is the single authoritative build path for
both Hosted MAF images. It runs ACR Tasks from the repository-root context; the
two `azure.yaml` files only deploy their referenced prebuilt images and do not
build them. The current azd Foundry extension still requires
`AZD_AGENT_SKIP_ACR=true` to select this path in non-interactive deployments.
The build helper's `--configure-azd` flag persists that marker and the exact
built image reference in both the azd environment and `azure.yaml`. The beta
extension materializes resolved image substitutions into the manifest during
deployment, so pinning both surfaces keeps repeated releases deterministic.
Azd core also requires a Docker target before the Foundry extension selects
the prebuilt image, so both manifests retain a server-side fallback with an
explicit repository-root build context.
`scripts/deploy_images.sh` builds the application and support Hosted MAF images
through ACR and updates the Container Apps. Pass `--with-directive` to build the
directive Hosted image in the same run; omitting the flag preserves the
support-only default. Hosted release tags are always explicit:

```bash
./scripts/deploy_images.sh <app-release> \
  --support-agent-tag <support-release> \
  --with-directive \
  --directive-agent-tag <directive-release>
```

Omit both directive arguments for a support-only run.
`scripts/build_hosted_agent_image.sh --agent directive --tag <release>
--configure-azd` remains available for an independent directive build. After
each Hosted image build, run `azd deploy --no-prompt` from that agent's
directory; the exact manifest pin creates a new Hosted Agent version and leaves
prior versions available for rollback.
`scripts/assign_hosted_agent_access.sh` idempotently assigns `AgentTools.Invoke`
to both the shared project and published Agent Identities, assigns
`Agent365.Observability.OtelWrite` only to the published identity, then configures
the nested Hosted Agent azd environment with the application MCP connection ID and
tenant ID plus the concrete Foundry project endpoint required by `azd deploy`.
Use `--no-app-tools-connection` for the directive package because its local
function wrappers call the authenticated gateway and do not use the support MCP
connection. Pass `--agent-type directive`; before enablement, add both the
published principal and shared project Agent Identity to Terraform variable
`directive_hosted_agent_principal_ids`.
This role assignment does not license or onboard the tenant for Agent 365
ingestion. Verify that an eligible Microsoft 365 E7 or Microsoft Agent 365 license
is assigned to at least one tenant user before treating downstream telemetry as a
release gate.
The deployment input uses the concrete existing-project URL so `azd provision`
cannot replace the platform-generated `FOUNDRY_PROJECT_ENDPOINT` with a circular
reference.
The checked-in `agent.yaml` mirrors the Hosted Agent runtime contract and is
validated by `azd ai agent doctor` before deployment.

Hosted Agent source-code deployment without a container image is currently preview.
The implementation keeps the established container deployment and will reassess the
source option after general availability.

Hosted images use independent immutable release tags. Use a new support and
directive value for every release; the build helper rejects any tag already
present in ACR.
The active Hosted image repository is `customer-support-maf-hosted`;
`customer-support-maf` is retained only as a rollback artifact. Obsolete
`kb-setup` and `prompt-agent-release` repositories are not retained.

Exact deployed versions, image digests, rollback targets, and production
acceptance evidence are recorded in
[`.azure/deployment-plan.md`](.azure/deployment-plan.md).

## Repository layout

```text
agent_contracts/  Versioned prompts, strict tools, and runtime/event contracts
agents/           Foundry Hosted Microsoft Agent Framework source
backend/          Packaged FastAPI trust boundary, stores, gateway, and agent adapters
frontend/         Componentized Vite + Lit SPA and constrained A2UI tool cards
infra/            Terraform infrastructure, networking, identities, and RBAC
maf_hosting/      Shared Hosted MAF identity, gateway, and runtime foundation
setup/            Direct Foundry IQ and Prompt Agent release code
scripts/          Entra, direct Foundry release, image deployment, and role assignment
docs/             Current PRD and implementation delivery record
```
