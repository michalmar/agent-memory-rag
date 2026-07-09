# PRD — Agentic AI Memory: Chat + RAG + Memory (Challenges 1–5)

> **Status:** Standalone build specification. **Self-contained** — it assumes a fresh,
> empty project where **none** of the files described here exist yet. Parts 1–18 are the
> product/architecture spec; **Part B (Appendices)** provides the verbatim artifacts
> (file tree, prompts, templates, framework contracts, build config) needed to build it
> without any access to the original codebase.
> **Scope:** Challenges 01–05 only (Session Memory, Conversation History, Conversation
> Memory, User Memory, Knowledge Base RAG). Challenges 06 (Agents Scratchpad) and 07
> (Knowledge Graph) are **out of scope**.
> **External prerequisites (must be provisioned separately):** Azure OpenAI (chat +
> embedding deployments), Azure Cache for Redis, Azure Cosmos DB, Azure Database for
> PostgreSQL Flexible Server (pgvector), Azure AI Search, and (for production auth)
> an Entra ID app registration. See §14 for the exact configuration contract.

---

## 1. Product Vision & Goals

Build a single, cohesive **customer-support chat application** whose AI agent
demonstrates a layered memory architecture. The agent must:

1. Hold a natural, streaming conversation over a web chat UI.
2. Remember the **current** conversation across turns (short-term / session memory).
3. Persist **every** conversation durably and let users browse ~recent history.
4. Distil past conversations into **semantically searchable** long-term memories.
5. Learn durable **facts about the user** and personalise responses automatically.
6. Ground answers in a **company knowledge base** (orders + policies) via RAG, with
   inline citations.

The re-implementation target is a **whole working solution** — not a series of
fill-in-the-blank exercises. All five memory layers are implemented and wired together.

### Success definition
A user can chat with the agent, get grounded/cited answers, see order-status cards,
have their profile learned automatically, browse & re-open past conversations,
"memorise" a conversation and later find it by meaning, and have all short-term
session state survive a backend restart.

---

## 2. Personas & Primary Use Cases

| Persona | Need | Use case |
|---|---|---|
| End customer | Support answers | "What's the status of ORD-001?", "What's your return policy?" |
| Returning customer | Continuity | Agent greets by name, recalls "what we talked about last time" |
| Workshop operator | Local/dev runs | Mock auth, switch between mock users, inspect system prompt |

Representative prompts the solution must handle well:
- "What is the status of order ORD-001?" → order-status card (tool call).
- "What products were in my order ORD-001?" → RAG (orders index) with citations.
- "What is your return policy?" → RAG (policies) with citations (agentic mode only).
- "Hi, my name is Alice and I live in Prague." → profile learned silently.
- "What did we talk about last time?" → semantic recall from conversation memory.

---

## 3. Scope

### In scope (Challenges 1–5)
- Streaming chat backend (SSE, AG-UI event protocol).
- Session memory backed by Redis with graceful in-memory fallback.
- Durable conversation history in Cosmos DB + history browsing UI.
- Conversation memory (LLM summary + embedding) in PostgreSQL/pgvector + semantic search.
- User profile memory in Cosmos DB (tool-write during chat + batch extraction endpoints).
- Knowledge-base RAG via Azure AI Search: **agentic** (MCP) and **classic** (hybrid) modes.
- Web UI: chat, tool-result surfaces (A2UI), history list, memory list/search, profile drawer, RAG toggle, system-prompt viewer, theme toggle, mock-user switch.
- Auth: Entra ID JWT (production) and mock header auth (local).

### Out of scope
- Challenge 06 (Agents Scratchpad / Redis stack, multi-agent).
- Challenge 07 (Knowledge Graph / GraphRAG).
- Infra provisioning (Terraform) — treated as an assumed prerequisite; env contract documented.

---

## 4. System Architecture

```
┌──────────────────────────── Frontend (Lit + Vite + TS) ───────────────────────────┐
│  <a2ui-native-app>                                                                  │
│  • Chat stream (SSE)   • History list   • Memory list + semantic search             │
│  • Profile drawer      • RAG toggle     • A2UI surface renderer (tool results)      │
│  AGUIClient ──HTTP/SSE──▶                                                            │
└───────────────────────────────────────┬─────────────────────────────────────────────┘
                                         │  Bearer JWT  (or X-Mock-User-ID)
                                         ▼
┌──────────────────────────── Backend (FastAPI, Python 3.11+) ───────────────────────┐
│  server.py                                                                          │
│   POST /chat (SSE, AG-UI events)   REST: /sessions /conversations /memories /profile│
│   Auth dependency (auth.py)                                                         │
│                                                                                     │
│  Agents (Microsoft Agent Framework)                                                 │
│   • CustomerSupportAgent  (AzureOpenAIResponsesClient, store=false, streaming)      │
│   • MemoryAgent           (summarise + embed)                                       │
│   • ProfileAgent          (extract profile JSON)                                    │
│                                                                                     │
│  Tools (agent_tools.py): get_order_status, check_memory, update_user_profile,       │
│                          knowledge_base (MCP), do_classic_rag                        │
│                                                                                     │
│  Stores:  SessionManager(Redis)   ConversationHistoryStore(Cosmos)                  │
│           ConversationMemoryStore(Postgres+pgvector)  UserProfileMemoryStore(Cosmos)│
└───────┬───────────────┬───────────────┬───────────────┬───────────────┬─────────────┘
        ▼               ▼               ▼               ▼               ▼
   Azure Cache     Azure Cosmos    Azure DB for     Azure Cosmos    Azure AI Search
   for Redis       DB (history)    PostgreSQL       DB (profiles)   (orders + policies KB)
   (session)                       + pgvector                       + Azure OpenAI (LLM+embeds)
```

### Request lifecycle (chat turn)
1. Frontend `POST /chat` with `{messages:[{role:user,content}], thread_id, rag_mode}` + auth header.
2. Backend resolves user, gets/creates the session (Redis-backed), verifies ownership.
3. Fetches the user profile → renders the Jinja2 system prompt (profile + rag_mode injected).
4. Builds a per-request `Agent` with the correct tool set for `rag_mode`.
5. Runs the agent **streaming**; emits AG-UI SSE events: `RUN_STARTED`, `TEXT_MESSAGE_CONTENT` deltas, `TOOL_CALL_START/RESULT/END`, `RUN_FINISHED` (or `RUN_ERROR`).
6. On completion: serialises session state to Redis, increments message count, and appends the user+assistant turn to Cosmos (`_persist_turn`).
7. Frontend renders streamed text (Markdown) and converts tool results into A2UI surfaces.

---

## 5. Technology Stack

| Layer | Technology |
|---|---|
| Agent runtime | Microsoft Agent Framework (`agent-framework-ag-ui`), `Agent`, `AgentSession`, `MCPStreamableHTTPTool` |
| LLM | Azure OpenAI — chat/completions via `AzureOpenAIResponsesClient` (main agent, `store=false`) and `AzureOpenAIChatClient` (memory/profile agents). Default deployment `gpt-4o-mini` |
| Embeddings | Azure OpenAI `text-embedding-3-large` (3072 dims) via `openai.AsyncAzureOpenAI` |
| API framework | FastAPI + Uvicorn; SSE via `StreamingResponse` |
| Streaming protocol | AG-UI events (`ag_ui.core.events`, `ag_ui.encoder.EventEncoder`) |
| Prompts | Jinja2 (`StrictUndefined`), templates in `backend/prompts/*.j2` |
| Session store | Azure Cache for Redis via `redis.asyncio` (TLS 6380) — in-memory fallback |
| History / Profile store | Azure Cosmos DB (`azure-cosmos` async), AAD or key auth |
| Memory store | Azure Database for PostgreSQL Flexible Server + `pgvector`, via `asyncpg`; AAD (managed identity) or password auth |
| Knowledge base | Azure AI Search (knowledge base + 2 indexes), MCP endpoint + classic hybrid REST |
| Auth | Entra ID JWT (`pyjwt[crypto]`, JWKS) or mock header; MSAL Browser on frontend |
| Frontend | TypeScript, Lit 3 web components, Vite 6, `marked` (Markdown), `dompurify`, `@azure/msal-browser` |
| UI rendering | Custom A2UI v0.8 processor + `<a2ui-surface>` Lit renderer |

Backend Python deps (pin-compatible): `agent-framework-ag-ui`, `aiohttp`, `asyncpg`,
`azure-cosmos`, `azure-identity`, `jinja2`, `openai`, `pyjwt[crypto]`, `python-dotenv`,
plus `redis>=5.0.0` (Challenge 01), `httpx` (MCP), `azure-search-documents` (setup only).

> **As-built deltas (this deployment).** The reference implementation in this repo diverges
> from the spec above in a few deliberate ways, driven by package availability and Azure
> subscription constraints:
> - **Agent runtime:** `agent-framework` is not on public PyPI, so the real runner uses the
>   stock `openai` `AsyncAzureOpenAI` SDK (Chat Completions + tool calling + streaming),
>   mapped to the same AG-UI event stream. Contracts (§B3/§B4) are unchanged.
> - **Session store:** Redis is **not** provisioned; `SessionManager` runs in-memory only and
>   the backend is pinned to a single replica (§F1 acceptance descoped accordingly).
> - **RAG:** default `rag_mode=classic` (app-side embeddings → AI Search hybrid query), which
>   keeps AI Search fully private with no Search→OpenAI vectorizer link. Agentic MCP retrieval
>   is left as a future enhancement.
> - **Foundry resource:** provisioned as `azurerm_cognitive_account` (kind `AIServices`,
>   `project_management_enabled = true`) + `azurerm_cognitive_account_project` — the current
>   Microsoft Foundry resource shape, replacing the deprecated `azurerm_ai_services` +
>   preview `azapi` project.
> - **Regions (fully-private, cross-region private endpoints):** core stack + VNet in
>   **eastus2**; **PostgreSQL Flexible Server in northcentralus** (eastus2 is offer-restricted
>   for Postgres on this subscription) reached via a cross-region private endpoint (no VNet
>   injection); **Azure AI Search in westeurope** (eastus2 was out of Search capacity) reached
>   via a cross-region private endpoint. All data-plane access is private; the local machine
>   cannot reach Cosmos/Postgres/Search directly — KB setup runs in-VNet or with a temporary
>   public toggle.

---

## 6. Memory Taxonomy (the 5 layers)

| # | Layer | Lifetime | Store | Written by | Read by |
|---|---|---|---|---|---|
| 01 | Session memory | Current session (survives restart) | Redis | Framework after each turn | Agent (as chat history) |
| 02 | Conversation history | Durable, raw | Cosmos DB | `_persist_turn` after each turn | History UI, memory/profile pipelines |
| 03 | Conversation memory | Long-term, distilled | PostgreSQL + pgvector | `POST /memories` (user "Memorise") | `check_memory` tool + semantic search UI |
| 04 | User memory (profile) | Long-term, per user | Cosmos DB | `update_user_profile` tool + batch endpoints | System-prompt injection every turn |
| 05 | Knowledge base | Static reference | Azure AI Search | Setup script (one-time) | RAG tools (agentic MCP / classic hybrid) |

---

## 7. Functional Requirements by Feature

### F1 — Session Memory (Challenge 01, Redis)

**Purpose:** Keep live conversation state per session so turns are coherent and sessions
survive backend restarts / scale across instances.

**Component:** `SessionManager` in `server.py`.

- Holds an in-memory hot cache of live `AgentSession` objects: `_sessions: dict[str, AgentSession]`.
- Durable state persisted to Redis; `_session_metadata` / `_message_counts` dicts are the **fallback** when Redis is not connected.
- `AgentSession` is the framework object that stores full conversation history for a session; serialised via `.to_dict()` / restored via `AgentSession.from_dict()`.

**Redis key schema:**

| Key | Type | Value |
|---|---|---|
| `session:{id}:state` | STRING | JSON of `AgentSession.to_dict()` (full history) |
| `session:{id}:metadata` | HASH | `title`, `created_at`, `last_activity`, `user_id` |
| `session:{id}:message_count` | STRING | integer counter (via `INCRBY`, +2 per user+assistant turn) |

**Methods (async):** `connect()`, `close()`, `create_session(session_id?, title?, user_id?)`,
`get_session(session_id, auto_create=True)` (restores from `state` JSON if not hot),
`delete_session`, `update_session(title)`, `save_session_state` (serialise → Redis `state`),
`increment_message_count(n=2)`, `get_session_info`, `list_sessions(user_id?)`.
Max sessions cap (e.g. 1000) with LRU-style eviction by `last_activity`.

**Graceful degradation (`connect()`):**
1. If `REDIS_HOST` unset → log warning, stay in-memory. (`"REDIS_HOST not set — session manager running in-memory only"`)
2. If set → connect + `PING`; on failure log warning and stay in-memory.
3. Only assign the live client on success (`"Redis connected: {host}:{port} (ssl=...)"`).
Every method branches on `if self._redis is not None`.

**Lifespan:** `await session_manager.connect()` on startup, `await session_manager.close()` on shutdown.

**Ownership:** `_assert_session_owner(session_id, user_id)` (async when Redis-backed) raises 403 if a session's `user_id` differs.

**Acceptance:**
- Works in-memory when `REDIS_HOST` unset (no crash).
- Connects when reachable; falls back with warning when unreachable.
- Chatting creates the three `session:*` keys.
- Sessions resume after a backend restart when Redis is connected.
- No external API changes.

---

### F2 — Conversation History (Challenge 02, Cosmos DB)

**Purpose:** Durable, replayable record of every conversation; powers the History list and
feeds the memory/profile pipelines.

**Component:** `ConversationHistoryStore` (`conversation_history.py`), async CRUD over a
Cosmos container. Auth: `COSMOS_KEY` if present else `DefaultAzureCredential` (AAD).
Partition key `/user_id`; document id = `session_id`.

**Document schema:**
```json
{
  "id": "<session_id>",
  "user_id": "<user_id>",
  "title": "string|null",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "message_count": 4,
  "messages": [ { "role": "user|assistant", "content": "..." } ],
  "metadata": { "agent_name": "CustomerSupportAgent", "model_deployment": "...",
                "api": "responses", "store": false, "rag_mode": "agentic" }
}
```

**Store methods:** `initialize()`, `close()`, `save_conversation(session_id, user_id, messages, title?, metadata?)`
(upsert; preserves `created_at`), `get_conversation`, `list_conversations(user_id, limit=50, offset=0)`
(lightweight projection, newest first by `updated_at`), `delete_conversation`, `update_title`.

**Turn persistence:** `_persist_turn(session_id, user_id, user_message, assistant_message, title?, rag_mode?)`
appends `[{user},{assistant}]` to the existing doc (or creates it), merges metadata, and upserts.
Called at the end of every successful agent run. Failures are logged, never break the stream.

**Emulator note:** Cosmos vNext-preview emulator returns malformed DELETE responses; delete
verifies success with a follow-up point-read (workaround).

**Acceptance:** turns persist after each turn; `GET /conversations` lists saved convos;
`GET /conversations/{id}` returns full doc; deleting a conversation cascades to its memory.

---

### F3 — Conversation Memory (Challenge 03, PostgreSQL + pgvector)

**Purpose:** Turn a raw conversation into a concise, PII-light, semantically searchable
summary for long-term recall.

**Components:**
- `ConversationMemoryStore` (`conversation_memory.py`) — async CRUD + cosine search over Postgres.
- `MemoryAgent` (`memory_agent.py`) — summariser Agent + embedding client.
- Prompt `prompts/conversation_memory.j2` — summariser system prompt.

**Table (auto-created on `initialize()`):**
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE conversation_memory (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  embedding vector(3072) NOT NULL,
  source_title TEXT,
  message_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_conversation_memory UNIQUE (conversation_id, user_id)
);
```

**Store methods:** `create_memory` (UPSERT on `(conversation_id,user_id)`),
`get_memory`, `list_memories(user_id, limit, offset)` (newest first, no embedding),
`delete_memory`, `search(user_id, query_embedding, limit)` returning
`1 - (embedding <=> $query::vector) AS similarity`, ordered by distance, scoped to the user.

**Auth modes (asyncpg pool):** `PG_AUTH_MODE=password` (uses `PG_USER`/`PG_PASSWORD`, sslmode
`prefer`) or `managed_identity` (AAD token as password via `ManagedIdentityCredential`/
`DefaultAzureCredential`, scope `https://ossrdbms-aad.database.windows.net/.default`, sslmode `require`).

**MemoryAgent pipeline (`create_memory(messages, title?)`):**
1. Format transcript (`role: content` lines, plus tool call/result lines).
2. Summarise via streaming Agent (system prompt = `conversation_memory.j2`).
3. Embed the summary (`text-embedding-3-large`).
4. Return `MemoryResult(summary, embedding)`.

**Summariser prompt requirements (`conversation_memory.j2`):** 2–4 sentences, third person /
past tense, capture main topic + intent + resolution + key entities (order IDs, products,
dates), exclude PII/greetings/filler, output only the summary text.

**Endpoint orchestration (`POST /memories`):** fetch conversation (ownership + non-empty) →
`memory_agent.create_memory` → `memory_store.create_memory` → return summary row.

**Lifespan:** `await memory_store.initialize()` / `.close()`.

**Acceptance:** "Memorise" creates a summary+embedding; memories appear in UI; semantic
search finds relevant memories; `check_memory` tool answers "what did we talk about last time".

---

### F4 — User Memory / Profile (Challenge 04, Cosmos DB)

**Purpose:** Learn durable facts about the user and inject them into the system prompt so the
agent personalises responses (e.g., greets by name).

**Components:**
- `UserProfileMemoryStore` (`user_profile_memory.py`) — Cosmos CRUD, one doc per user (`/user_id`).
- `update_user_profile` tool (`agent_tools.py`) — live, silent write during chat.
- `ProfileAgent` (`profile_agent.py`) — batch extraction from conversation transcripts.
- Prompts `prompts/user_profile.j2` (inject) and `prompts/profile_update.j2` (write instructions).

**Profile document schema:**
```json
{
  "id": "<user_id>", "user_id": "<user_id>", "version": 3,
  "basic_info": { "name": "Alice", "location": "Prague", "job": "Developer" },
  "interests": ["hiking", "coffee"],
  "habits": ["morning jogger"],
  "preferences": { "communication": "casual" },
  "status": { "current_project": "microservices migration" },
  "facts": ["has a golden retriever named Max", "allergic to peanuts"],
  "source_conversations": [ { "conversation_id": "...", "extracted_at": "...",
                             "facts_added": 2, "facts_updated": 0, "facts_removed": 0 } ],
  "created_at": "ISO", "updated_at": "ISO"
}
```
Six profile sections: **objects** `basic_info`, `preferences`, `status`; **arrays** `interests`, `habits`, `facts`.

**Store methods:** `get_profile`, `upsert_profile(user_id, profile_sections, source_conversation?)`
(bumps `version`, preserves `created_at`, appends audit), `patch_profile(user_id, updates)`
(partial merge of provided sections), `delete_profile`.

**Live tool `update_user_profile(basic_info?, interests?, habits?, preferences?, status?, facts?)`:**
- Reads current profile, applies **RFC 7396 JSON Merge Patch** (`null` removes a key), upserts.
- Objects: pass only changed keys. Arrays: pass the **full** desired array (agent merges).
- Returns a short "Profile updated: <keys>" string. Must be **registered** in `all` and `for_rag_mode` tool lists.

**`profile_update.j2` (write instructions):** watch for explicitly stated personal info; call only
on new/changed facts; never infer/guess; never mention the update; one combined call per turn;
pass only changed fields.

**`user_profile.j2` (injection):** render only when `user_profile` is non-empty; header
`== USER PROFILE ==`; conditionally render each populated section (`tojson` for dicts,
`join` for lists); instruct the agent to use it naturally, not repeat verbatim.

**ProfileAgent (batch):** returns strict JSON `{profile_changed, change_summary, facts_added/updated/removed, profile:{...6 sections}}`; conservative extraction, deduplicated, values <100 chars, arrays ≤~20 items; strips markdown fences before parsing.

**Endpoints:** `GET /profile`, `PUT /profile` (manual edit, partial), `DELETE /profile`,
`POST /profile/generate` (from one conversation), `POST /profile/generate-all` (batch across
recent conversations, skipping already-processed `source_conversations`).

**Chat integration:** before every turn the server fetches the profile and renders it into the
system prompt via `_build_personalized_agent(user_profile, rag_mode)`.

**Acceptance:** agent detects & silently stores facts; profile persists to Cosmos; system prompt
includes profile; agent greets by name / uses facts naturally; incremental merge (no overwrite).

---

### F5 — Knowledge Base RAG (Challenge 05, Azure AI Search)

**Purpose:** Ground answers about products, shipping, and return/refund policies in indexed
company documents, with inline citations. Two selectable modes.

**Modes:**

| | Agentic RAG (MCP) | Classic RAG (hybrid) |
|---|---|---|
| Retrieval control | LLM decides when/what (tool call) | App code runs one search |
| Sources | KB spanning **orders + policies** | Single **orders** index |
| Transport | `MCPStreamableHTTPTool` → AI Search KB MCP endpoint | REST hybrid (keyword+vector+semantic) |
| Component | `rag_client.create_rag_mcp_tool()` | `classic_rag_client.ClassicRAGClient` |

**Agentic (`rag_client.py`):** `create_rag_mcp_tool()` builds an `MCPStreamableHTTPTool` named
`knowledge_base` at URL `{AZURE_SEARCH_ENDPOINT}/knowledgebases/{KB}/mcp?api-version=2025-11-01-Preview`,
authed via `_AzureSearchAuth` (httpx auth injecting AAD bearer for scope `https://search.azure.com/.default`),
`approval_mode="never_require"`, `load_prompts=False`, and a `parse_tool_results=_parse_mcp_rag_result`
callback that converts MCP output to `{content, citations[]}`. Connected in lifespan
(`await rag_mcp_tool.connect()` / `.close()`), guarded so `None` skips connection.

**Classic (`classic_rag_client.py`):** POST to `{endpoint}/indexes/{orders}/docs/search?api-version=2024-07-01`
with hybrid payload (`search` + text `vectorQueries` on `page_embedding` + `queryType=semantic`,
`semanticConfiguration=semantic_config`, select `id, order_id, category, page_chunk`, `top=5`),
retrying 502s (integrated-vectorizer cold start). Returns `content` + citations.

**Citation contract:** each citation = `{search_idx, ref_id, source_name, content, annotation}`
with `annotation = 【search_idx:ref_id†source_name】`. The agent must append these annotations
inline for every KB-sourced fact (per prompt rules).

**Tool registration (`agent_tools.py`):** `all` includes the MCP tool; `for_rag_mode(mode)`
returns base tools (`get_order_status`, `check_memory`, `update_user_profile`) plus the MCP tool
for `agentic`, `do_classic_rag` for `classic`, and nothing extra for `none`.

**Prompt (`customer_support.j2`):** Jinja2 conditionals describe KB tools differently for
`agentic` vs `classic`, extend the TOOL SELECTION GUIDE, and define the citation/annotation rules.

**Knowledge base setup (one-time, `setup/knowledgebase/setup_search.py`):**
- Index `orders`: fields `id`(key), `order_id`, `category`, `page_chunk`(searchable),
  `page_embedding`(vector 3072, HNSW, integrated Azure OpenAI vectorizer), semantic config `semantic_config`.
- Index `return-policy`: `id`(key), `section`, `page_chunk`, `page_embedding`, same vector/semantic setup.
- Two knowledge sources (orders, policies) + one knowledge base `customer-support-kb`
  (`output_mode=EXTRACTIVE_DATA`, minimal reasoning effort).
- Embeddings generated with `text-embedding-3-large`; documents in `documents/orders/*.json` and `documents/policies/*.json`.

**Acceptance:** MCP tool connects on startup; product/shipping and policy questions answered with
citations (agentic); order-status still uses `get_order_status`; classic mode covers orders only
and tells the user policies need agentic mode.

---

## 8. Agent & Tools Architecture

**Main agent — `CustomerSupportAgent`:**
- Client: `AzureOpenAIResponsesClient` (Responses API, `base_url = {endpoint}/openai/v1/`), `default_options={"store": False}` (history is client-side via `AgentSession`).
- Instructions: rendered `customer_support.j2` (profile + rag_mode injected per request).
- Tools: per-request set from `AgentTools.for_rag_mode(rag_mode)`.
- A global default agent (all tools, agentic) is reused when no profile and `rag_mode==agentic` to avoid re-rendering.

**Auxiliary agents** use `AzureOpenAIChatClient` (no conversation state needed):
- `MemoryAgent` (`MemorySummarizer`) — summarisation.
- `ProfileAgent` (`ProfileExtractor`) — profile JSON extraction.

**`AgentTools`** (dependency-injected: memory_store, memory_agent, profile_store, rag_mcp_tool,
classic_rag_client). Active `user_id` is passed via a `contextvars.ContextVar` set per request
(`set_user_id`) so `@tool` functions can scope to the caller.

| Tool | Signature | Returns | Notes |
|---|---|---|---|
| `get_order_status` | `(order_id)` | `{trackingNumber, currentStepIcon, eta}` | Mock data for ORD-001/002/003; status→Material icon map; renders Shipping Status A2UI card |
| `check_memory` | `(query)` | top-3 memory summaries (text) | Embeds query, cosine search scoped to user; only on explicit past-conversation references |
| `update_user_profile` | `(basic_info?, interests?, habits?, preferences?, status?, facts?)` | "Profile updated: …" | JSON Merge Patch upsert, silent |
| `knowledge_base` (MCP) | LLM-driven | `{content, citations[]}` | Agentic RAG across orders+policies |
| `do_classic_rag` | `(query)` | `{content, citations[]}` | Classic hybrid over orders index only |

**Mock order data:** ORD-001 shipped (tracking 1Z999AA1, ETA Jan 25 2026), ORD-002 processing
(no tracking, ETA Jan 23 2026), ORD-003 delivered (Jan 20). Unknown → "Order not found".

---

## 9. Prompt System (Jinja2)

Templates in `backend/prompts/`, loaded with `StrictUndefined`.

| Template | Role | Key inputs |
|---|---|---|
| `customer_support.j2` | Master system prompt | `user_profile`, `rag_mode`; `{% include %}`s the two profile partials |
| `user_profile.j2` | Inject stored profile | `user_profile` dict |
| `profile_update.j2` | Instruct `update_user_profile` usage | (static) |
| `conversation_memory.j2` | Summariser system prompt | (static) |

`customer_support.j2` covers: order-status tool usage, `check_memory` usage (explicit only),
RAG tool descriptions (branching on `rag_mode`), TOOL SELECTION GUIDE, and citation/annotation rules.

`GET /prompts/{name}` renders and returns a template (with the caller's profile) for the UI's
"view system prompt" modal.

---

## 10. REST + SSE API Contract

All endpoints require auth (Entra bearer or mock header). Base path is proxied as `/api` in the UI.

### Auth / meta
| Method | Path | Purpose |
|---|---|---|
| GET | `/me` | Current authenticated `User` |
| GET | `/prompts/{name}` | Rendered system prompt (personalised) |

### Chat (SSE)
| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/chat` | `{messages:[{role,content}], thread_id?, rag_mode}` | `text/event-stream` of AG-UI events; `X-Session-ID` header |

AG-UI event types emitted: `RUN_STARTED`, `TEXT_MESSAGE_CONTENT` (`{messageId, delta}`),
`TOOL_CALL_START` (`{toolCallId, toolCallName}`), `TOOL_CALL_RESULT` (`{toolCallId, content}`),
`TOOL_CALL_END` (`{toolCallId}`), `RUN_FINISHED`, `RUN_ERROR` (`{message, code}`).
`rag_mode ∈ {none, agentic, classic}` (defaults to `agentic`; invalid → `agentic`).

### Sessions (Redis)
`POST /sessions` (201), `GET /sessions`, `GET /sessions/{id}`, `GET /sessions/{id}/history`
(served from Cosmos), `PUT /sessions/{id}` (title), `DELETE /sessions/{id}` (also deletes Cosmos doc). Ownership enforced (403).

### Conversation history (Cosmos)
`GET /conversations?limit&offset`, `GET /conversations/{id}`, `PUT /conversations/{id}` (title),
`DELETE /conversations/{id}` (cascades to memory delete).

### Conversation memory (Postgres)
`POST /memories` `{conversation_id}` (201), `GET /memories?limit&offset`, `GET /memories/{conversation_id}`,
`DELETE /memories/{conversation_id}`, `POST /memories/search` `{query, limit}` → `{query, results:[{…, similarity}]}`.

### User profile (Cosmos)
`GET /profile` (404 if none), `PUT /profile` (partial edit), `DELETE /profile`,
`POST /profile/generate` `{conversation_id}`, `POST /profile/generate-all` `{limit}`.

Standard error codes: 400 (bad input), 401 (unauthenticated), 403 (ownership), 404 (missing),
422 (empty conversation), 501 must **not** remain (endpoints fully implemented).

---

## 11. Consolidated Data Models

- **User** `{user_id, display_name, email, avatar_url?, initials}`.
- **ConversationHistory doc** — see §F2.
- **conversation_memory row** — see §F3 table.
- **UserProfile doc** — see §F4.
- **KB order chunk** `{id, order_id, category, page_chunk, page_embedding}`.
- **KB policy chunk** `{id, section, page_chunk, page_embedding}`.
- **Citation** `{search_idx, ref_id, source_name, content, annotation}`.
- **AG-UI ChatMessage (frontend)** `{role, content, toolCalls?, ragMode?}`.

---

## 12. Frontend Specification

Single Lit web component `<a2ui-native-app>` (`app.ts`) with `AGUIClient` (`client.ts`) and an
A2UI rendering pipeline. Config injected at runtime via `window.__APP_CONFIG__` (`/config.js`)
with `apiBaseUrl`, auth mode, Entra IDs/scope; dev falls back to `/api` proxy.

### Layout
- **Header:** app title, mock-user `<select>` (mock mode only), "New Chat", "view system prompt" (info), theme toggle.
- **Sidebar** (collapsible) with sections:
  - **Sessions** — live sessions (rename/delete).
  - **History** — Cosmos conversations (click to open, rename, delete, "Memorise").
  - **Memory** — a search box (semantic search via `POST /memories/search`, shows similarity %) and the memory list; clicking a memory opens its detail.
  - **User card** at the bottom → opens the **Profile drawer**.
- **Main pane:** welcome screen (suggestion chips) or the message stream; **memory detail** view when a memory is selected.
- **Input area:** text input + send, **RAG toggle** (Off / Agentic / Classic radios), footer note.

### Chat rendering
- Streams SSE; appends `TEXT_MESSAGE_CONTENT` deltas; renders assistant text as **Markdown** (`marked`) sanitised with `dompurify`.
- Assistant messages show a **RAG badge** (Agentic/Classic) when applicable.
- Tool calls render a compact tool-call indicator and, when a converter exists, an **A2UI surface**.

### A2UI tool-result rendering (`converters.ts` + `a2ui/`)
- `A2UIProcessor` buffers `surfaceUpdate` / `dataModelUpdate` / `beginRendering` messages into per-surface state.
- `<a2ui-surface>` (Lit) recursively renders the standard catalog: Card, Column, Row, Text, Icon, Button, Image, Divider, List.
- Converters map tool JSON → A2UI messages via reusable declarative templates:
  - `get_order_status` → `SHIPPING_STATUS_TEMPLATE` (pass-through data model `{trackingNumber, currentStepIcon, eta}`).
  - `do_classic_rag` / MCP KB result (auto-detected by `{content, citations}` shape) → `RAG_CITATIONS_TEMPLATE` (citation cards; skipped if no citations).
  - Fallback: generic Card+Text dump for unknown tools.

### Behaviours
- New chat, open/rename/delete sessions & conversations, open a past conversation (rebuilds messages + rag badge from stored metadata).
- "Memorise" a conversation (`POST /memories`) with in-flight state and a memorised set.
- Profile drawer: view/generate/edit profile; toast notifications.
- Theme (light/dark) toggle; suggestion chips seed prompts.
- Auth: mock (`X-Mock-User-ID`, switchable user) or Entra (MSAL popup, silent token, `Authorization: Bearer`).

---

## 13. Authentication & Authorization

**Backend (`auth.py`), `get_current_user` dependency:**
- `AUTH_MODE=mock`: requires `X-Mock-User-ID` header; resolves from a mock user table (user-alice/bob/charlie); unknown → 401.
- `AUTH_MODE=entra`: validates RS256 JWT via JWKS from the tenant OpenID config; checks `aud`/`iss`/`exp`/`iat`; optional required scopes/roles (403 on mismatch); builds `User` from `oid/sub`, `preferred_username/email`, `name`.
- Any unauthenticated request → 401.

**Frontend (`auth.ts`):** mock sends `X-Mock-User-ID` (from `?mockUser`, localStorage, or default `user-alice`); entra uses MSAL Browser (`loginPopup` → `acquireTokenSilent`/`acquireTokenPopup`) and sends the bearer token.

**Authorization:** every session/conversation/memory/profile record is keyed/partitioned by
`user_id`; the backend enforces ownership (403) and scopes all reads/writes/searches to the caller.

---

## 14. Configuration (Environment Variables)

| Variable | Feature | Notes |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | LLM/embeds | **required** |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | LLM | **required** (e.g. `gpt-4o-mini`) |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME` | embeds | default `text-embedding-3-large` (3072 dims) |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_SSL` | F1 | empty host → in-memory mode; TLS on 6380 |
| `COSMOS_ENDPOINT` / `COSMOS_KEY` | F2/F4 | empty key → AAD auth |
| `COSMOS_DATABASE_NAME` / `COSMOS_CONTAINER_NAME` | F2 | history container |
| `COSMOS_UPM_DATABASE_NAME` / `COSMOS_UPM_CONTAINER_NAME` | F4 | profiles container |
| `COSMOS_EMULATOR_DISABLE_SSL_VERIFY` | local | `1` for emulator |
| `PG_HOST` / `PG_PORT` / `PG_DATABASE` | F3 | |
| `PG_AUTH_MODE` | F3 | `password` or `managed_identity` |
| `PG_USER` / `PG_PASSWORD` | F3 | password mode |
| `PG_AAD_PRINCIPAL_NAME` / `AZURE_CLIENT_ID` / `PG_SSLMODE` | F3 | managed-identity mode |
| `AZURE_SEARCH_ENDPOINT` | F5 | |
| `AZURE_SEARCH_KNOWLEDGE_BASE_NAME` | F5 | default `customer-support-kb` |
| `AZURE_SEARCH_ORDERS_INDEX` | F5 | default `orders` (classic) |
| `AUTH_MODE` | auth | `entra` (default) or `mock` |
| `ENTRA_TENANT_ID` / `ENTRA_AUDIENCE` / `ENTRA_ISSUER` / `ENTRA_REQUIRED_SCOPES` / `ENTRA_REQUIRED_ROLES` | auth | entra mode |

Frontend runtime config (`__APP_CONFIG__`): `apiBaseUrl`, `authMode`, `entraTenantId`, `entraClientId`, `entraApiScope`.

---

## 15. Non-Functional Requirements

- **Streaming latency:** first token streamed as soon as available; tool-call events surfaced live.
- **Resilience:** each store degrades or fails safe — Redis falls back to in-memory; `_persist_turn` never breaks the stream; classic RAG retries 502s; Cosmos emulator delete workaround.
- **Isolation:** strict per-user scoping across all stores; ownership checks return 403.
- **Idempotency:** memory upsert on `(conversation_id,user_id)`; history upsert on `session_id`; profile upsert bumps `version`.
- **Security:** no secrets in code; AAD/managed-identity preferred; JWT signature + claim validation; frontend Markdown sanitised (`dompurify`).
- **Observability:** structured logging with `[IN]/[OUT]` markers and event names; noisy Azure SDK HTTP logs suppressed.
- **Config-driven:** all endpoints/creds via env; sensible local defaults.
- **Performance:** history/memory list endpoints use lightweight projections + `limit/offset`; pgvector cosine ordering; asyncpg pool (min 2 / max 10).

---

## 16. Acceptance Criteria (solution-level)

1. Chat streams responses; order-status questions render a Shipping Status card.
2. Session state persists across a backend restart when Redis is configured (else in-memory, no crash).
3. Every turn is saved to Cosmos; History list & re-open work; delete cascades to memory.
4. "Memorise" produces a summary+embedding; Memory list shows it; semantic search returns it with a similarity score; `check_memory` recalls it in chat.
5. Agent silently learns profile facts during chat; profile persists (versioned, merged); system prompt injects it; agent personalises (e.g., greets by name) without echoing it verbatim.
6. Agentic RAG answers product/shipping **and** policy questions with inline `【…】` citations; classic RAG answers orders-only and defers policy questions to agentic mode.
7. Auth works in both mock and Entra modes; all data is per-user isolated (403 on cross-user access).

---

## 17. Suggested Build Order (re-implementation)

1. Scaffold FastAPI app + auth (mock first) + `/me` + streaming `/chat` with a bare agent and `get_order_status`.
2. Frontend chat shell (Lit) + SSE client + A2UI processor/renderer + shipping-status template.
3. F1 Redis `SessionManager` (with in-memory fallback) + session endpoints.
4. F2 Cosmos history store + `_persist_turn` + history endpoints + sidebar History.
5. F4 profile store + `update_user_profile` tool + profile partials + profile endpoints + drawer.
6. F3 Postgres memory store + MemoryAgent + summariser prompt + `/memories` + `check_memory` + Memory UI/search.
7. F5 KB setup script, then `create_rag_mcp_tool` (agentic) + classic client + tool registration + prompt RAG rules + RAG toggle + citation rendering.
8. Harden: Entra auth, error handling, logging, per-user isolation tests.

---

## 18. Appendix — Reference Sample Data

**Orders KB (chunks per order: overview / shipping / product).** e.g. ORD-001: 2 items
(WH-1000XM5 headphones $349.99, USB-C cable ×2), total $421.16, shipped UPS Ground, tracking
1Z999AA1, ETA Jan 25 2026.

**Return-policy KB sections:** `eligibility` (30-day window, condition rules), `process`
(RMA, prepaid label, $7.99 non-defective fee), `refunds` (5–7 business days, restocking fee,
exchange for 110% store credit), `exceptions` (holiday window, bulk/international, price match).

**Mock users:** user-alice (Alice Johnson, AJ), user-bob (Bob Smith, BS), user-charlie (Charlie Lee, CL).

---
---

# Part B — Self-Contained Build Reference (Appendices)

> Everything below lets a team build the solution in a brand-new repository with no access
> to the original code. Parts 1–18 above are the *what/why*; Part B is the *exact how*.

## B1. Project Structure (create these files)

```
repo/
├── backend/                       # FastAPI + Microsoft Agent Framework (Python 3.11)
│   ├── pyproject.toml             # deps (see B2)
│   ├── .python-version            # "3.11"
│   ├── Dockerfile                 # see B2
│   ├── .env                       # runtime config (see §14)
│   ├── server.py                  # FastAPI app: /chat SSE + REST + SessionManager + lifespan
│   ├── auth.py                    # Entra JWT + mock auth; get_current_user dependency
│   ├── agent_tools.py             # AgentTools class: all @tool functions + tool-list accessors
│   ├── conversation_history.py    # ConversationHistoryStore (Cosmos)
│   ├── conversation_memory.py     # ConversationMemoryStore (Postgres + pgvector)
│   ├── user_profile_memory.py     # UserProfileMemoryStore (Cosmos)
│   ├── memory_agent.py            # MemoryAgent (summarise + embed)
│   ├── profile_agent.py           # ProfileAgent (extract profile JSON)
│   ├── rag_client.py              # create_rag_mcp_tool() + MCP result parsing (agentic RAG)
│   ├── classic_rag_client.py      # ClassicRAGClient (hybrid search over orders index)
│   └── prompts/
│       ├── customer_support.j2     # master system prompt (includes the two partials)
│       ├── user_profile.j2         # inject stored profile
│       ├── profile_update.j2       # instruct update_user_profile usage
│       └── conversation_memory.j2  # summariser system prompt
├── frontend/                      # Lit 3 + Vite 6 + TypeScript
│   ├── package.json               # deps (see B9)
│   ├── vite.config.ts             # dev server + /api proxy (see B9)
│   ├── tsconfig.json              # (see B9)
│   ├── index.html                 # shell: <a2ui-native-app>, loads /config.js then main.ts
│   ├── nginx.conf.template        # prod runtime-config injection (see B9)
│   ├── Dockerfile                 # multi-stage build → nginx (see B9)
│   └── src/
│       ├── main.ts                # entry: import './app.js'
│       ├── app.ts                 # <a2ui-native-app> root component (UI + orchestration)
│       ├── client.ts              # AGUIClient: REST + SSE parsing
│       ├── auth.ts                # mock / MSAL Entra auth headers
│       ├── converters.ts          # tool-result JSON → A2UI messages
│       ├── ui-logger.ts           # optional in-UI debug logger
│       ├── a2ui/
│       │   ├── types.ts           # A2UI v0.8 message/component types (see B8)
│       │   ├── processor.ts       # A2UIProcessor: buffers surfaces (see B8)
│       │   └── surface-renderer.ts# <a2ui-surface> Lit renderer (see B8)
│       └── templates/
│           ├── shipping-status.ts  # SHIPPING_STATUS_TEMPLATE (see B8)
│           └── rag-citations.ts    # RAG_CITATIONS_TEMPLATE (see B8)
└── setup/
    └── knowledgebase/
        ├── requirements.txt        # azure-search-documents, azure-identity, openai, python-dotenv
        ├── setup_search.py         # one-time KB provisioning (see §F5 + B7 for behavior)
        └── documents/
            ├── orders/ord-001.json … ord-003.json   # order chunks (see B10)
            └── policies/return-policy.json           # policy chunks (see B10)
```

## B2. Backend dependency manifest, container & run

`backend/pyproject.toml`:
```toml
[project]
name = "agentic-memory-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "agent-framework-ag-ui>=1.0.0b260304",  # Microsoft Agent Framework + AG-UI
  "aiohttp>=3.9.0",                         # classic RAG HTTP
  "asyncpg>=0.30.0",                        # PostgreSQL
  "azure-cosmos>=4.9.0",                    # Cosmos DB (async)
  "azure-identity>=1.26.0b1",               # DefaultAzureCredential / ManagedIdentity
  "jinja2>=3.1.0",                          # prompt templates
  "openai>=1.40.0",                         # embeddings client
  "pyjwt[crypto]>=2.10.1",                  # Entra JWT validation
  "python-dotenv>=1.2.1",
  "redis>=5.0.0",                           # session memory (Challenge 01)
  "httpx>=0.27.0",                          # MCP transport auth
]
```
Runtime: `uv run uvicorn server:app --reload` (dev) or `uvicorn server:app --host 0.0.0.0 --port 8000`.
`backend/Dockerfile` (python:3.11-slim; install `uv`; `uv export --frozen --no-dev -o requirements.txt`
then `uv pip install --system -r requirements.txt`; `COPY . .`; `EXPOSE 8000`;
`CMD ["uvicorn","server:app","--host","0.0.0.0","--port","8000"]`).

## B3. Microsoft Agent Framework — integration contract

The backend depends on `agent-framework` (installed via `agent-framework-ag-ui`). Concrete usage:

**Imports**
```python
from agent_framework import Agent, AgentSession, tool, MCPStreamableHTTPTool
from agent_framework.azure import AzureOpenAIChatClient, AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential
from ag_ui.core.events import (
    RunStartedEvent, RunFinishedEvent, TextMessageContentEvent,
    ToolCallStartEvent, ToolCallEndEvent, ToolCallResultEvent, RunErrorEvent,
)
from ag_ui.encoder import EventEncoder
```

**Clients**
```python
# Main agent — Responses API, client-side history (store=false)
responses_client = AzureOpenAIResponsesClient(
    credential=DefaultAzureCredential(), endpoint=OPENAI_ENDPOINT,
    base_url=f"{OPENAI_ENDPOINT.rstrip('/')}/openai/v1/", deployment_name=MODEL_DEPLOYMENT)
# Aux agents (summarise/extract) — Chat client, no conversation state
chat_client = AzureOpenAIChatClient(
    credential=DefaultAzureCredential(), endpoint=OPENAI_ENDPOINT, deployment_name=MODEL_DEPLOYMENT)
```

**Agent construction**
```python
agent = Agent(name="CustomerSupportAgent", instructions=rendered_prompt,
              client=responses_client, tools=[...], default_options={"store": False})
```

**Tools** — instance methods decorated with `@tool`; parameters described via
`typing.Annotated[T, pydantic.Field(description=...)]`; a tool may be `def` or `async def`
and return `str` or a JSON-serialisable `dict`. Tool functions read the active user via a
`contextvars.ContextVar` set once per request. An `MCPStreamableHTTPTool` instance is *also*
a valid entry in the `tools` list.

**Session** — `AgentSession()` holds full conversation history for a session; serialise with
`session.to_dict()` and restore with `AgentSession.from_dict(d)`.

**Streaming run** (this is how AG-UI events are produced):
```python
async for update in agent.run(user_message, stream=True, session=session):
    if update.text:                       # assistant text delta → TEXT_MESSAGE_CONTENT
        ...
    for content in update.contents:       # tool activity
        d = content.to_dict() if hasattr(content, "to_dict") else {}
        if d.get("type") == "function_call":    # → TOOL_CALL_START (d["call_id"], d["name"])
            ...
        elif d.get("type") == "function_result":# → TOOL_CALL_RESULT + TOOL_CALL_END (d["call_id"], d["result"])
            ...
```
De-duplicate by `call_id` (a tool may appear across multiple updates). Accumulate `update.text`
into the final assistant message used for persistence.

**MCP tool lifecycle** — `await mcp_tool.connect()` on startup, `await mcp_tool.close()` on
shutdown; guard with `if mcp_tool is not None`. `MCPStreamableHTTPTool(...)` accepts:
`name`, `url`, `description`, `http_client` (an authed `httpx.AsyncClient`),
`approval_mode="never_require"`, `load_prompts=False`, `parse_tool_results=<callable>`.

## B4. AG-UI SSE encoding contract

Endpoint returns `media_type="text/event-stream"`, headers `Cache-Control: no-cache`,
`Connection: keep-alive`, `X-Session-ID: <session_id>` (exposed via CORS `expose_headers`).
Each event is `EventEncoder().encode(<Event>)` yielded as an SSE `data: {json}\n` line.

| Backend event | Emitted fields | Frontend `event.type` | Frontend fields |
|---|---|---|---|
| `RunStartedEvent(thread_id, run_id)` | thread/run ids | `RUN_STARTED` | — |
| `TextMessageContentEvent(messageId, delta)` | text delta | `TEXT_MESSAGE_CONTENT` | `delta` |
| `ToolCallStartEvent(toolCallId, toolCallName)` | tool id+name | `TOOL_CALL_START` | `toolCallId`, `toolCallName` |
| `ToolCallResultEvent(messageId, toolCallId, content)` | result JSON string | `TOOL_CALL_RESULT` | `toolCallId`, `content` |
| `ToolCallEndEvent(toolCallId)` | — | `TOOL_CALL_END` | `toolCallId` |
| `RunFinishedEvent(thread_id, run_id)` | — | `RUN_FINISHED` | — |
| `RunErrorEvent(message, code)` | error | `RUN_ERROR` | `message` |

Tool `content` for `TOOL_CALL_RESULT` is `json.dumps(result)` when result is dict/list, else `str(result)`.

## B5. Redis SessionManager — concrete construction

```python
import redis.asyncio as redis
client = redis.Redis(host=REDIS_HOST, port=int(REDIS_PORT), password=REDIS_PASSWORD or None,
                     ssl=(REDIS_SSL.lower() == "true"), decode_responses=True)
await client.ping()  # verify before assigning self._redis
```
Keys per §F1. `create_session`: set `metadata` HASH + `message_count`=0. `save_session_state`:
`SET session:{id}:state <session.to_dict() as JSON>`. `get_session`: if not hot-cached, `GET`
the `state` key and `AgentSession.from_dict(json.loads(state))`, else `auto_create`.
`increment_message_count`: `INCRBY session:{id}:message_count 2`. `delete_session`: `DEL` all three
keys. If `self._redis is None`, every method uses the in-memory dict fallback. Cap live hot cache
(e.g. 1000) with LRU eviction by `last_activity`.

## B6. Helper algorithms (implement verbatim behavior)

**JSON Merge Patch (RFC 7396)** — used by `update_user_profile`:
```python
def _json_merge_patch(target, patch):
    result = dict(target)
    for k, v in patch.items():
        if v is None: result.pop(k, None)
        elif isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _json_merge_patch(result[k], v)
        else: result[k] = v
    return result
```

**Transcript formatting** (MemoryAgent & ProfileAgent input) — join lines as `"{role}: {content}"`;
for each message also append `"[tool call: {name}({arguments})]"` and `"[tool result: {result}]"`
lines when present; prefix with `"Conversation title: {title}"` if a title exists.

**Source-name derivation** (`_derive_source_name(chunk, ref_id)`) — used to label citations:
1. Take the first line of `chunk`; if it matches `^([A-Z][^:]{2,40}):` return that captured label.
2. Else split `ref_id` on `-`: if `parts[0]=="ord"` and ≥3 parts → `"Order {ORD-###} {Titlecased rest}"`;
   if `parts[0]=="policy"` and ≥2 parts → `"Policy: {Titlecased rest}"`; else Title-case the parts (≤40 chars).
3. Fallback `"Source {ref_id}"`.

**MCP result parser** (`_parse_mcp_rag_result`) — concatenate `item.text` of the MCP
`CallToolResult.content`; parse as a JSON array of `{ref_id, content}`; produce
`{"content": "\n\n".join(chunks), "citations": [{search_idx:i, ref_id, source_name:<derived>,
content: chunk[:300], annotation: "【{i}:{ref_id}†{source_name}】"}]}`. If not JSON, return
`{"content": raw, "citations": []}`.

**get_order_status status→icon map:** `shipped→local_shipping`, `processing→pending`,
`delivered→check_circle`, `not_found→error`, default `help`.

## B7. Full prompt templates (verbatim)

### `prompts/customer_support.j2`
```jinja2
You are a helpful customer support assistant.

You have access to a get_order_status tool that can look up order information.

IMPORTANT: When a user mentions an order ID (like ORD-001, ORD-002, etc.),
you MUST call the get_order_status tool to retrieve the actual order details.
Do NOT make up or guess order information.

You also have access to a check_memory tool that can search past conversation
memories. When the user explicitly asks you to recall, look up, or reference
something from a previous conversation, call check_memory with a relevant
query and the current user's ID. Use the returned summaries to inform your
response. Only use this tool when the user explicitly asks about past
conversations — do NOT call it proactively.

{%- if rag_mode is not defined or rag_mode == 'agentic' %}
You also have access to knowledge base tools provided via MCP (Model Context Protocol)
that search the company knowledge base using agentic retrieval (AI-driven reasoning
across multiple knowledge sources — orders and policies). Use the knowledge base tools
when the user asks about:
- Detailed product descriptions, specifications, or features
- Shipping details (carrier, weight, packaging, dimensions)
- Return policy rules, eligibility windows, or refund timelines
- Refund methods, processing times, or exceptions
- Any detailed information beyond a simple order status check
{%- elif rag_mode == 'classic' %}
You also have access to a do_classic_rag tool that searches the orders knowledge
base using classic hybrid search (keyword + vector + semantic ranking). This tool
searches ONLY the orders index. Use do_classic_rag when the user asks about:
- Detailed product descriptions, specifications, or features
- Shipping details (carrier, weight, packaging, dimensions)
- Any order-specific information beyond the basic status

NOTE: This tool does NOT cover return/refund policies. If the user asks about
policies, inform them that classic search does not cover policy documents and
suggest they enable agentic RAG for that.
{%- endif %}

TOOL SELECTION GUIDE:
- For "What's the status of my order?" → use get_order_status
{%- if rag_mode is not defined or rag_mode == 'agentic' %}
- For "What products were in my order?" → use the knowledge base tools
- For "What is your return policy?" → use the knowledge base tools
- For "Can I return my order?" → use knowledge base tools (to check policy) then get_order_status (for dates)
{%- elif rag_mode == 'classic' %}
- For "What products were in my order?" → use do_classic_rag
- For "What is your return policy?" → NOT available via classic search
{%- endif %}
- For "What did we talk about last time?" → use check_memory
{%- set _rag_tool = 'do_classic_rag' if (rag_mode is defined and rag_mode == 'classic') else 'knowledge base' %}

{%- if rag_mode is not defined or rag_mode in ['agentic', 'classic'] %}
CITATION / ANNOTATION RULES ({{ _rag_tool }} results):
When using information from {{ _rag_tool }}, you MUST include inline annotations in
your response. The {{ _rag_tool }} tool returns a "citations" array; each citation has
a "search_idx", "ref_id", and "source_name". For every fact you use from the
knowledge base, append an annotation in this exact format:

  【search_idx:ref_id†source_name】

For example, if the tool returns a citation with search_idx=0, ref_id="return-policy_0",
source_name="return-policy", write your sentence followed by 【0:return-policy_0†return-policy】

Rules:
- Every claim sourced from the knowledge base MUST have at least one annotation.
- Place annotations immediately after the relevant sentence or phrase.
- You may cite multiple sources for a single statement.
- If no citations were returned, do not fabricate annotations.

When presenting information from {{ _rag_tool }}, incorporate it naturally into your
response. Do not dump raw citation data to the user — use the annotations.
{%- endif %}

After calling get_order_status, provide the actual results to the user in a friendly format.

Remember the conversation context - if user refers to "it" or "the order",
they are referring to previously discussed orders in this conversation.
{% include 'user_profile.j2' %}
{% include 'profile_update.j2' %}
```

### `prompts/user_profile.j2`
```jinja2
{%- if user_profile is defined and user_profile %}

== USER PROFILE ==
You have access to the following personal information about the current user.
Use it to personalise your responses — greet them by name when appropriate,
respect their stated preferences, and reference their interests or habits
when relevant. Do NOT repeat the profile back verbatim; use it naturally.

{%- if user_profile.basic_info %}
Basic info: {{ user_profile.basic_info | tojson }}
{%- endif %}
{%- if user_profile.interests %}
Interests: {{ user_profile.interests | join(", ") }}
{%- endif %}
{%- if user_profile.habits %}
Habits: {{ user_profile.habits | join(", ") }}
{%- endif %}
{%- if user_profile.preferences %}
Preferences: {{ user_profile.preferences | tojson }}
{%- endif %}
{%- if user_profile.status %}
Current status: {{ user_profile.status | tojson }}
{%- endif %}
{%- if user_profile.facts %}
Other facts: {{ user_profile.facts | join("; ") }}
{%- endif %}
{%- endif %}
```

### `prompts/profile_update.j2`
```jinja2
== PROFILE UPDATE ==
You have access to an update_user_profile tool. As you converse, watch for
any NEW personal information the user reveals:
- Name, location, job, company
- Hobbies, interests, topics they care about
- Behavioral habits or routines
- Preferences (communication, shipping, contact method, etc.)
- Current status or life events
- Personal facts (pets, allergies, family, birthday, etc.)

Rules for calling update_user_profile:
- ONLY call when the user EXPLICITLY states new or changed personal info.
- Do NOT call for information already in the profile above.
- Do NOT infer or guess — only use clearly stated facts.
- Do NOT mention the update to the user. Continue naturally.
- Most messages will NOT warrant a call.
- Make ONE single call per turn with ALL new info combined in one patch.
  Never split updates into multiple parallel calls.

Pass only the parameters that changed — omit unchanged ones:
- For objects (basic_info, preferences, status): pass a dict with only changed keys.
- For arrays (interests, habits, facts): pass the FULL desired array
  (merge new items with existing ones yourself).

Example — user says "I just moved to Portland and I love sushi":
  → basic_info={"location": "Portland, OR"}, interests=["<existing>", "sushi"]
```

### `prompts/conversation_memory.j2`
```jinja2
You are a conversation memory assistant. Your job is to create a concise,
meaningful summary of a customer support conversation.

Rules:
- Capture the main topic, customer intent, and resolution (if any).
- Include key entities: order IDs, product names, dates, names.
- Keep the summary to 2-4 sentences maximum.
- Write in third person, past tense.
- Do NOT include greetings, filler, or meta-commentary.
- Output ONLY the summary text, nothing else.

Example:
  "Customer inquired about order ORD-001 shipping status. The order was
   confirmed as shipped with tracking number 1Z999AA1, estimated delivery
   Jan 25, 2026."
```

### ProfileAgent extraction prompt (Python string constant in `profile_agent.py`)
Instruct an LLM that receives (1) the user's EXISTING profile JSON and (2) a CONVERSATION
TRANSCRIPT to: extract personal facts about the USER only; ADD new, UPDATE changed, REMOVE
contradicted, LEAVE UNCHANGED unmentioned; update transient `status`. Output **only** JSON:
```json
{"profile_changed": true, "change_summary": "...", "facts_added": 0, "facts_updated": 0,
 "facts_removed": 0,
 "profile": {"basic_info": {}, "interests": [], "habits": [], "preferences": {}, "status": {}, "facts": []}}
```
Rules: valid JSON only (no markdown); `profile_changed=false` + unchanged profile when no personal
info; be conservative (no inference); exclude assistant/system info; string values <100 chars;
arrays ≤~20 items; deduplicate. The agent strips ```` ```json ```` fences before parsing and
falls back to the existing sections on parse failure.

## B8. A2UI protocol reference (frontend rendering)

A minimal client-side implementation of the A2UI v0.8 "standard catalog". Three moving parts:

**(1) Message types (`a2ui/types.ts`)** — `BoundValue` = one of
`{literalString|literalNumber|literalBoolean|path}`. `ComponentDef` = `{id, weight?, component}`
where `component` has exactly one of: `Text{text:BoundValue, usageHint?}`, `Icon{name:BoundValue}`,
`Image{url}`, `Button{child, primary?, action?}`, `Card{child}`, `Column{children, gap?}`,
`Row{children, alignment?, distribution?, gap?}`, `List{children, direction?}`, `Divider{axis?}`.
`children: {explicitList: string[]}` **or** `{template:{dataBinding, componentId}}`.
Server→client messages: `surfaceUpdate{surfaceId, components[]}`,
`dataModelUpdate{surfaceId, path?, contents: DataEntry[]}`,
`beginRendering{surfaceId, root}`, `deleteSurface{surfaceId}`. `DataEntry` =
`{key, valueString?|valueNumber?|valueBoolean?|valueMap?: DataEntry[]}`.

**(2) Processor (`a2ui/processor.ts`)** — per surface keeps `components: Map<id,def>`,
`dataModel` (nested object built from `DataEntry[]` adjacency lists), `rootId`, `ready`.
`surfaceUpdate` merges components; `dataModelUpdate` applies contents at `path`;
`beginRendering` sets `rootId` + `ready=true`; `deleteSurface` drops it. Path resolution splits
on `/`.

**(3) Renderer (`a2ui/surface-renderer.ts`)** — a Lit `<a2ui-surface>` that, when
`surface.ready`, recursively renders from `rootId`. Binding rules a builder MUST replicate:
- `resolveBoundValue`: literals returned directly; a `path` is resolved against the surface
  `dataModel`. If a **context path** is active and the path is **relative** (no leading `/`),
  the effective path is `{ctx}/{path}`.
- `Card` renders `child`; `Column`/`Row`/`List` render `children`; `explicitList` maps ids;
  `template` resolves `dataBinding` (array **or** object-map → `Object.values`) and renders
  `componentId` once per item with context path `"{dataBinding}/{index}"`.
- `Button` dispatches a bubbling `a2ui-action` CustomEvent `{name, context}`.
- Material Symbols icons render as `<span class="material-symbols-outlined">{name}</span>`.

**Converters (`converters.ts`)** transform a tool-result JSON string + `surfaceId` into
`A2UIMessage[]` via `inflateSurfaceTemplate(template, dataModel, surfaceId)` which emits
`[surfaceUpdate(components=template), dataModelUpdate(contents=flatten(dataModel)), beginRendering(root='root')]`.
Registry keyed by tool name:
- `get_order_status` → `SHIPPING_STATUS_TEMPLATE`, data model passed through unchanged
  (`{trackingNumber, currentStepIcon, eta}`).
- `do_classic_rag` and any result with a `citations` array (agentic MCP) → `RAG_CITATIONS_TEMPLATE`;
  build `{citationCount: "N sources", citations: {"0": {sourceName: "{annotation}  {source_name}",
  snippet: content[:150]}, …}}`; **return `[]` (no surface) when there are no citations**.
- Unknown tools → generic Card+Text dump of pretty JSON.

**`SHIPPING_STATUS_TEMPLATE`** (verbatim `ComponentDef[]`): `root` Card→`main-column` Column(gap medium)
with children `[header, tracking-number, divider, steps, eta]`. `header` = Row(center, small gap)
`[package-icon(Icon package_2), title(Text "Package Status" h3)]`. `tracking-number` =
Text bound `/trackingNumber` (caption). `divider` = Divider{}. `steps` = Column(small gap)
`[step1..step4]`, each a Row(center,small) of `{stepN-icon, stepN-text}`: step1 (Icon check_circle,
Text "Order Placed" body), step2 (Icon check_circle, Text "Shipped" body), step3 (**Icon bound
`/currentStepIcon`**, Text "Out for Delivery" h4), step4 (Icon circle, Text "Delivered" caption).
`eta` = Row(center,small) `[eta-icon(Icon schedule), eta-text(Text bound `/eta` body)]`.

**`RAG_CITATIONS_TEMPLATE`** (verbatim): `root` Card→`main-col` Column(gap medium)
`[header, divider, citation-list]`. `header` = Row(center,small) `[header-icon(Icon menu_book),
header-title(Text "Sources" h3), header-count(Text bound `/citationCount` caption)]`.
`divider` Divider{}. `citation-list` = List{direction vertical, children.template:{dataBinding:'/citations',
componentId:'citation-row'}}. `citation-row` = Row(start,small) `[cite-icon(Icon description),
cite-details]`; `cite-details` = Column(small) `[cite-source(Text bound relative `sourceName` h5),
cite-snippet(Text bound relative `snippet` caption)]`. (Note relative paths inside the template item.)

## B9. Frontend build & runtime config

`package.json` deps: `@azure/msal-browser`, `dompurify`, `lit`, `marked`; dev: `typescript`, `vite`.
Scripts: `dev: vite`, `build: tsc && vite build`, `preview: vite preview`.

`vite.config.ts`:
```ts
import { defineConfig } from 'vite';
export default defineConfig({
  envPrefix: ['VITE_', 'AUTH_'],
  server: { port: 5175, proxy: { '/api': {
    target: 'http://localhost:8000', changeOrigin: true,
    rewrite: (p) => p.replace(/^\/api/, '') } } },
  build: { target: 'esnext' },
});
```
`tsconfig.json`: target ES2022, module ES2022, `moduleResolution: bundler`, `strict`,
`experimentalDecorators: true`, `useDefineForClassFields: false` (**required for Lit decorators**),
`rootDir: src`, `outDir: dist`.

`index.html` shell: sets CSS variables + Material Symbols + Inter fonts, renders
`<a2ui-native-app></a2ui-native-app>`, loads `<script src="/config.js" onerror="">` **then**
`<script type="module" src="./src/main.ts">`. `main.ts` = `import './app.js';`.

**Runtime config** — the app reads `window.__APP_CONFIG__` = `{apiBaseUrl, buildId, authMode,
entraTenantId, entraClientId, entraApiScope}`. In dev this is absent → `apiBaseUrl` defaults to
`/api` (proxied) and `authMode` defaults to `mock`. In prod, nginx serves `/config.js` from
`nginx.conf.template` via `envsubst`:
```nginx
location = /config.js {
  default_type application/javascript;
  return 200 'window.__APP_CONFIG__={apiBaseUrl:"${BACKEND_URL}",buildId:"${BUILD_ID}",authMode:"${AUTH_MODE}",entraTenantId:"${ENTRA_TENANT_ID}",entraClientId:"${ENTRA_CLIENT_ID}",entraApiScope:"${ENTRA_API_SCOPE}"};';
}
location / { try_files $uri $uri/ /index.html; }   # listen 8080; SPA fallback
```
`frontend/Dockerfile`: `node:20-alpine` builder → `npx vite build`; `nginx:alpine` serving `dist`,
template copied to `/etc/nginx/templates/default.conf.template`, backend/auth env defaults set,
`EXPOSE 8080`.

## B10. Reference seed documents (verbatim for KB setup)

Orders index docs = objects `{id, order_id, category, page_chunk}` (embedding added by setup):
- `ord-001`: 3 chunks (categories `order_detail`, `shipping`, `product`). Overview: placed
  Jan 15 2026, 2 items — WH-1000XM5 headphones (Black, $349.99) + USB-C cable 2m ×2 ($14.99 ea),
  subtotal $379.97, shipping $9.99, tax $31.20, total $421.16, Visa 4242. Shipping: shipped
  Jan 18 via UPS Ground, tracking 1Z999AA1, Seattle FC, ETA Jan 25 2026, in transit (Portland).
  Product: WH-1000XM5 specs (30h battery, multipoint BT, warranty 1 yr).
- `ord-002`, `ord-003`: analogous chunks (processing / delivered variants).

Return-policy index docs = `{id, section, page_chunk}` with sections `eligibility` (30-day window,
original packaging, electronics need accessories; final-sale/software non-returnable; furniture
30-day comfort guarantee), `process` (RMA in 1 business day, prepaid label, free for defective,
$7.99 non-defective fee, freight pickup), `refunds` (5–7 business days to original payment,
CC +3–5 days, up to 15% restocking if damaged, exchange for 110% store credit),
`exceptions` (holiday window to Jan 31, warranty beyond 30 days, bulk 20%/manager approval,
international customer-paid shipping, 14-day price match).

## B11. Local dev & prod run

**Local (mock auth):**
1. Provision or point env at Azure OpenAI + Redis + Cosmos + Postgres + AI Search (or use emulators where available). Set `backend/.env` per §14 with `AUTH_MODE=mock`.
2. `cd setup/knowledgebase && pip install -r requirements.txt && python setup_search.py` (one-time; requires `az login`).
3. Backend: `cd backend && uv sync && uv run uvicorn server:app --reload` (port 8000).
4. Frontend: `cd frontend && npm install && npm run dev` (port 5175; `/api`→8000 proxy).
5. Open the app; use the header **Mock user** switch (`?mockUser=user-bob` also works).

**Production:** build both Docker images; run backend (`AUTH_MODE=entra` + Entra vars, managed
identity for Cosmos/Postgres/Search); run frontend nginx image with `BACKEND_URL`, `AUTH_MODE=entra`,
`ENTRA_*` envs so `/config.js` drives MSAL login.

## B12. Self-sufficiency checklist

A team can build the whole solution from this document alone, provided they supply the
**external Azure resources** (§14 contract) and the **niche packages** (`agent-framework-ag-ui`,
`ag_ui`) from their registries. Everything code-shaped that is specific to this app — file layout
(B1), dependency pins (B2), framework/SSE contracts (B3–B4), store construction (B5), algorithms
(B6), all prompt texts (B7), the A2UI protocol + both surface templates + converter rules (B8),
build/runtime config (B9), and seed data (B10) — is specified here without reference to any
pre-existing file. The only judgment left to implementers is UI styling/layout of `app.ts`
(a standard Lit component whose behavior and panels are fully described in §12).
