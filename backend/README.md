# Backend — FastAPI + AG-UI SSE

FastAPI trust boundary for the support-chat app. Production invokes two remote
Foundry agents; local mode provides matching mock runtimes without Azure.

## Requirements

- Python **3.11** (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)

## Setup & run

```bash
uv venv --python 3.11
uv pip install --python .venv/bin/python -e ../agent_contracts -e .
.venv/bin/python -m uvicorn agent_memory_backend.server:app --port 8000 --reload
```

## Endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/chat` | AG-UI event stream (SSE). Accepts one new message and returns `X-Conversation-ID`. |
| GET | `/me` | Current authenticated user. |
| GET | `/prompts/customer-support` | Shared stable agent prompt. |
| GET | `/agents` | Available agent types and Foundry IQ capability. |
| GET/PUT/DELETE | `/conversations*` | Owner-scoped durable history. |
| POST | `/internal/agent-tools/{name}` | App-only Hosted Agent tool gateway. |
| GET | `/health/live` | Process liveness; does not call dependencies. |
| GET | `/health/ready` | Concurrent, bounded Cosmos/Search/Foundry IQ checks. |
| GET | `/health` | Compatibility alias for liveness. |

## End-user access

Production uses Entra ID with the delegated `access_as_user` scope. The backend
derives a tenant-scoped principal key (`tid:oid`) from the validated token and
applies it to every session and Cosmos partition. Client
requests never supply their own `user_id`.

Mock auth is local-only. The backend refuses `AUTH_MODE=mock` when
`APP_ENV=production`.

### Local mock mode

Send `X-Mock-User-ID: user-alice` (or `user-bob` / `user-charlie`). Missing or
unknown IDs return `401`.

```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'X-Mock-User-ID: user-alice' -H 'Content-Type: application/json' \
  -d '{"message":"track ORD-001","conversation_id":null,"agent_type":"agent-framework"}'
```

## Agent mode

`LLM_MODE=mock|real` selects local mock runtimes or the configured Foundry project.
Production backend traffic uses the Entra/RBAC-only public Foundry endpoint.
Production exposes `foundry-prompt` and `agent-framework`. Agent type is required
for new conversations and immutable afterward.

## Notes

- **In-memory runtime mappings and locks** do not survive a restart or coordinate
  across replicas. Durable Cosmos metadata restores mappings after restart, while
  Container Apps remains pinned to one backend replica (min=max=1).
- All Azure stores and retrieval clients are asynchronous and expose explicit
  initialization/close lifecycle methods.
- Conversation-history lists execute against the authenticated Cosmos partition;
  full documents and summaries never expose owner or Cosmos-internal fields.
- Production uses the Container App user-assigned managed identity for Foundry,
  AI Search, Cosmos DB, and Azure Monitor. Local Cosmos key settings remain
  available only for local development.
- Foundry IQ is the only production retrieval architecture; there is no retrieval
  mode request field or fallback.
- The native Prompt Agent exposes only Foundry IQ knowledge retrieval. The Hosted
  MAF Agent additionally calls application tools through the app-only public
  frontend proxy; the backend ingress remains internal.
- Public history DTOs expose safe agent labels/version metadata but never owner,
  physical routing, Foundry conversation, Hosted session, response, or ETag data.
