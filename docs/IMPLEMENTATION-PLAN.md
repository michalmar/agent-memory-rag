# Implementation Plan — Agentic AI Memory (Challenges 1–5) on Azure

## 1. Problem & Approach

Build the full customer-support chat app described in `docs/PRD-Solution-Challenges-1-5.md`
(5 memory layers: session, history, conversation memory, user profile, KB RAG) **from an
empty repo**, and — per this session's decisions — **provision all required Azure services
with Terraform and deploy backend + frontend to Azure Container Apps**, with LLM/embeddings
served from an **Azure AI Foundry project**.

The PRD is detailed and ~80% self-contained (prompts, framework contracts, algorithms, A2UI
templates, SSE contract are all verbatim). The two largest source files (`server.py`,
`app.ts`) and **all infrastructure** are specified by *contract/behavior only* and must be
authored. This plan sequences that work and flags every gap.

## 2. Decisions locked in this session

| Topic | Decision |
|---|---|
| Deliverable | Plan first, then implement the full stack |
| Environment | Truly greenfield; **no local emulators** — provision real Azure |
| Hosting | Backend + Frontend → **Azure Container Apps** (separate apps) |
| LLM/Embeddings | **Azure AI Foundry** (account + **project**) with `gpt-4o-mini` + `text-embedding-3-large` deployments |
| Session memory (F1) | **In-memory only** — no Azure Cache for Redis; **pin backend to 1 replica** (min=max=1) |
| IaC | **Terraform** (AzureRM + AzAPI); containers built/pushed/deployed **separately** from `terraform apply` |

## 3. Revised architecture (deltas from PRD)

- **Remove Redis** from the runtime and infra. `SessionManager` runs its documented in-memory
  fallback path; `REDIS_HOST` stays unset. Sessions do **not** survive an ACA revision restart
  or a second replica — hence the single-replica pin. (Update PRD §F1 acceptance expectations.)
- **Add infra layer** (`infra/` Terraform) the PRD explicitly left out of scope (§3, §14 note).
- **Add container build/deploy** (ACR + image build/push + ACA revision update) as a separate
  step from Terraform provisioning.
- Everything else (F2 Cosmos, F3 Postgres/pgvector, F4 Cosmos, F5 AI Search) stays as specified.

Target service set to provision:
1. Azure AI Foundry (Cognitive Services `AIServices` account) **+ project** + 2 model deployments.
2. Azure Cosmos DB (NoSQL) — DB + **history** container (`/user_id`) + **profiles** container (`/user_id`).
3. Azure Database for PostgreSQL Flexible Server + `vector` (pgvector) extension allow-listed.
4. Azure AI Search (**Basic tier+**, required for agentic retrieval / KB MCP).
5. Azure Container Registry (ACR).
6. Container Apps Environment + Log Analytics + 2 Container Apps (backend, frontend).
7. User-assigned managed identity + all cross-service **RBAC / data-plane role assignments**.

## 4. Build phases (todos tracked in SQL)

**Phase 0 — Repo scaffold & tooling**
- `p0-scaffold`: Create B1 file tree (backend/, frontend/, setup/, infra/), `.env.example`, READMEs.

**Phase 1 — Backend core (Challenge 01 + chat)**
- `p1-backend-app`: `server.py` FastAPI app, lifespan, CORS (expose `X-Session-ID`), logging.
- `p1-auth`: `auth.py` — mock header auth first; Entra JWT path scaffolded.
- `p1-agent-framework`: Verify `agent-framework-ag-ui` API surface; wire `AzureOpenAIResponsesClient`.
- `p1-session-mgr`: `SessionManager` in-memory only (skip Redis branches at runtime).
- `p1-chat-sse`: `/chat` streaming loop → AG-UI events (B3/B4 contract); `get_order_status` tool.

**Phase 2 — Frontend shell**
- `p2-frontend-shell`: Vite + Lit scaffold, `index.html`, `main.ts`, runtime config, `/api` proxy.
- `p2-agui-client`: `client.ts` REST+SSE parsing; `auth.ts` (mock + MSAL).
- `p2-a2ui`: `a2ui/` types + processor + `<a2ui-surface>` renderer (B8, verbatim behavior).
- `p2-templates`: `shipping-status.ts` + `rag-citations.ts` templates + `converters.ts`.
- `p2-app`: `app.ts` root component (layout, sidebar, chat stream, drawers) — **authored from §12**.

**Phase 3 — Conversation history (Challenge 02, Cosmos)**
- `p3-history-store`: `conversation_history.py` + `_persist_turn` + emulator-safe delete note.
- `p3-history-endpoints`: `/conversations*`, `/sessions/{id}/history`; sidebar History UI.

**Phase 4 — User profile (Challenge 04, Cosmos)**
- `p4-profile-store`: `user_profile_memory.py` (merge-patch, versioning, audit).
- `p4-profile-agent`: `profile_agent.py` + extraction prompt; `update_user_profile` tool.
- `p4-profile-prompts`: `user_profile.j2`, `profile_update.j2`; wire into system prompt per turn.
- `p4-profile-endpoints`: `/profile*` (get/put/delete/generate/generate-all) + Profile drawer UI.

**Phase 5 — Conversation memory (Challenge 03, Postgres/pgvector)**
- `p5-memory-store`: `conversation_memory.py` (asyncpg pool, table+extension, cosine `search`).
- `p5-memory-agent`: `memory_agent.py` + `conversation_memory.j2` summariser.
- `p5-memory-endpoints`: `/memories*` + `check_memory` tool + Memory list/search UI.

**Phase 6 — Knowledge Base RAG (Challenge 05, AI Search)**
- `p6-kb-seed`: Author verbatim seed JSON for orders (ord-001..003) + return-policy (4 sections).
- `p6-kb-setup`: `setup/knowledgebase/setup_search.py` — indexes, vectorizer, knowledge sources, KB.
- `p6-rag-agentic`: `rag_client.py` (`create_rag_mcp_tool`, MCP parser, `_derive_source_name`).
- `p6-rag-classic`: `classic_rag_client.py` (hybrid REST, 502 retry).
- `p6-rag-wire`: Tool registration per `rag_mode`; `customer_support.j2` RAG rules; RAG toggle + citation cards.

**Phase 7 — Infrastructure (Terraform)**
- `p7-tf-foundation`: providers, RG, Log Analytics, naming, `terraform.tfvars.example`.
- `p7-tf-foundry`: Foundry account + project + `gpt-4o-mini` + `text-embedding-3-large` deployments.
- `p7-tf-data`: Cosmos (DB+2 containers), Postgres Flexible + pgvector allow-list, AI Search (Basic+).
- `p7-tf-compute`: ACR, Container Apps Env, 2 Container Apps, ingress, env-var wiring.
- `p7-tf-identity`: user-assigned MI + RBAC (OpenAI User, Cosmos data-plane, Search roles, Search→OpenAI for vectorizer, Postgres AAD).

**Phase 8 — Containerize, deploy, run KB setup**
- `p8-dockerfiles`: Backend Dockerfile (B2), Frontend multi-stage nginx Dockerfile (B9) + config.js.
- `p8-build-push`: `az acr build` (or docker) for both images; update ACA revisions.
- `p8-kb-provision`: Run `setup_search.py` against provisioned Search + Foundry (one-time).
- `p8-smoke`: End-to-end verification of §16 acceptance criteria (minus Redis restart item).

**Phase 9 — Harden**
- `p9-entra`: Optional Entra auth path (backend JWKS + frontend MSAL) if selected.
- `p9-isolation`: Per-user ownership/403 checks across all stores; error handling; structured logs.

## 5. GAP ANALYSIS — Missing content the PRD does NOT provide verbatim

These must be **authored**; the PRD gives contracts/behavior but no source:

1. **`server.py`** — the single most important file. Only endpoint list, lifecycle steps, and the
   streaming-loop *shape* (B3) are given. The full SSE orchestration, endpoint bodies,
   dependency injection, `_build_personalized_agent`, global default-agent reuse, and error
   mapping must be written from §4/§8/§10.
2. **`app.ts`** — B12 explicitly says UI styling/layout is "the only judgment left to
   implementers." A large Lit component (sidebar, chat stream, history/memory/profile panels,
   RAG toggle, theme, mock-user switch) authored from §12 prose.
3. **`client.ts`, `auth.ts`, `converters.ts`, `ui-logger.ts`** — behavior described, no code.
4. **KB seed JSON** (B10) — prose only. `ord-002`/`ord-003` are just "analogous"; exact chunks,
   IDs, categories, and page text for all orders + the 4 policy sections must be written so the
   `_derive_source_name` and citation formats line up with the templates.
5. **`setup_search.py`** — behavior in §F5/B10 but no code: index schemas, integrated
   Azure OpenAI vectorizer config, semantic config, 2 knowledge sources, 1 knowledge base,
   embedding backfill.
6. **All Terraform** — nothing in the PRD (infra was out of scope). Entire `infra/` authored.
7. **Dockerfiles** — backend described in prose (B2); frontend nginx template partially given (B9);
   both need finalizing.
8. **`.env` / `terraform.tfvars`** — §14 lists variables but no filled example values.
9. **`pyproject.toml` / `package.json`** — dependency lists given (B2/B9) but exact lockable
   manifests and `uv.lock` / `package-lock.json` must be generated.
10. **Tests** — no test suite is specified anywhere; §16 acceptance is manual. Any automated
    tests are net-new.

## 6. GAP ANALYSIS — Technical risks & bleeding-edge dependencies (verified)

1. **Azure AI Search agentic retrieval / KB MCP endpoint (`api-version=2025-11-01-Preview`)** —
   *Verified*: real but **PREVIEW** (no SLA, "not recommended for production"), **region-limited**,
   **Basic tier or higher** required. This is the **highest-risk** external dependency. Agentic
   RAG (F5) may be unavailable in some regions and the exact API version may shift. **Mitigation:**
   pick a supported region; classic RAG (F5 hybrid) is a non-preview fallback; keep agentic path
   feature-flagged so the app degrades if the MCP endpoint is absent.
2. **`agent-framework-ag-ui>=1.0.0b260304`** — a **beta** build of Microsoft Agent Framework
   (date-stamped ~Feb 2026). API surface (`Agent`, `AgentSession`, `MCPStreamableHTTPTool`,
   `AzureOpenAIResponsesClient`, streaming `update.contents` shape) must be verified against the
   actually-published package; beta APIs can drift. **Mitigation:** pin exact version; validate
   imports in Phase 1 before building on them.
3. **Azure OpenAI Responses API via `AzureOpenAIResponsesClient` + `store=false`** — relatively
   new; region + api-version + Foundry-project routing must be confirmed. **Mitigation:** verify
   `base_url=.../openai/v1/` works against the Foundry deployment early.
4. **pgvector `vector(3072)`** — *Verified*: storable; the PRD query does a full-scan cosine sort
   (no ANN index), so it works, but **HNSW indexing is capped at 2000 dims** → won't scale.
   Azure Postgres Flexible Server must have a pgvector version supporting 3072-dim columns and
   `vector` must be in `azure.extensions` allow-list. **Mitigation:** fine for demo scale; note
   the scaling ceiling.
5. **Cosmos DB data-plane RBAC via Terraform** — AAD auth needs
   `azurerm_cosmosdb_sql_role_assignment` (data-plane), *not* just control-plane RBAC. Easy to get
   wrong. **Mitigation:** explicit data-plane role module; or use key auth to start.
6. **Postgres managed-identity auth** — requires registering the ACA managed identity as an AAD
   principal inside Postgres + `PG_AAD_PRINCIPAL_NAME`; non-trivial bootstrap not expressible
   purely in Terraform. **Mitigation:** start with `PG_AUTH_MODE=password`, move to MI later.
7. **AI Search integrated vectorizer → Azure OpenAI** — Search service needs a managed identity
   with **Cognitive Services OpenAI User** on the Foundry resource, and embedding deployment name
   must match (`text-embedding-3-large`, 3072). Cross-service RBAC dependency.
8. **ACA image chicken-and-egg** — a Container App needs an image at creation, but images are
   built/pushed separately. **Mitigation:** create apps with a placeholder image, then update the
   revision after `az acr build`; or gate the compute module on images existing.
9. **`text-embedding-3-large` = 3072 dims** must be identical across AI Search vector fields,
   pgvector column, and the embedding client — any mismatch breaks search silently.
10. **Model/region availability** — `gpt-4o-mini` + `text-embedding-3-large` + AI Search agentic
    retrieval must all be available in **one** region. May force a compromise region.

## 7. OPEN DECISIONS still needed (with recommendations)

1. **Auth mode for the deployed app** — mock header vs Entra ID.
   *Recommendation:* ship **mock** first (no app registration needed), keep Entra code path;
   add Entra in Phase 9 if real users are expected. **Needs your call.**
2. **Azure region** — must satisfy risk #10 (Foundry models + AI Search agentic + Postgres +
   Cosmos). *Recommendation:* choose from a Foundry+agentic-retrieval-supported region
   (e.g., `eastus2` / `swedencentral` pending verification). **Needs your call.**
3. **Cosmos auth & capacity** — key vs AAD; serverless vs provisioned throughput.
   *Recommendation:* **serverless + AAD data-plane** (cheap, least-privilege). **Confirm.**
4. **Postgres auth mode** — password vs managed identity (see risk #6).
   *Recommendation:* **password** to start.
5. **Container build/deploy mechanism** — local `az acr build` + `az containerapp update`, or a
   **GitHub Actions** pipeline. *Recommendation:* scripted `az acr build` now; CI later.
6. **Naming / resource group / tags / subscription** — need target subscription ID, RG name,
   name prefix, and tag policy. **Needs your input.**
7. **Secrets handling** — Key Vault vs ACA secrets for any keys. *Recommendation:* prefer
   managed identity; ACA secrets for the few remaining keys; Key Vault optional.
8. **Frontend RAG default & policy coverage** — PRD defaults `rag_mode=agentic`; if agentic is
   region-blocked, default to `classic`. **Depends on region (decision #2).**
9. **Cost ceiling** — AI Search Basic, Postgres Flexible, Cosmos, Foundry, ACA, Log Analytics all
   accrue cost. Any budget cap or auto-shutdown expectation? **Needs your input.**

## 8. Notes

- The PRD's §16 acceptance criterion #2 (session survives backend restart via Redis) is
  intentionally **descoped** by the in-memory decision; treat single-replica in-memory as
  the accepted behavior and document it.
- Suggested build order (PRD §17) is preserved but **extended** with Phases 0, 7, 8, 9 for
  scaffold + infra + deploy + harden.
- Prompts, algorithms (B6), SSE contract (B4), A2UI templates (B8), and framework contract (B3)
  are copied **verbatim** — do not paraphrase.
