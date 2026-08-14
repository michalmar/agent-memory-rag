# Backend — FastAPI + AG-UI SSE

FastAPI trust boundary for the support-chat app. Production invokes three remote
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
| GET | `/agents` | Available agent types and project-level Foundry IQ capability. |
| GET | `/directive-sources` | Paginated source filename, size, and last-modified metadata. Requires `DirectiveSource.Manage`. |
| POST | `/directive-sources/upload/{filename}` | Create-only raw PDF upload. Requires `DirectiveSource.Manage`. |
| DELETE | `/directive-sources/{filename}` | Delete one exact source blob without changing published data. Requires `DirectiveSource.Manage`. |
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

Mock users can manage directive sources for local development. In production,
the delegated `access_as_user` scope authenticates the user, while all source
manager routes additionally require the user-assignable
`DirectiveSource.Manage` application role. The backend accesses the private
source container through its managed identity; browsers never receive storage
credentials or Blob coordinates.

## Agent mode

`LLM_MODE=mock|real` selects local mock runtimes or the configured Foundry project.
Production backend traffic uses the Entra/RBAC-only public Foundry endpoint.
Production exposes `foundry-prompt`, `agent-framework`, and the independently
gated `directive-rag` agent. Agent type is required for new conversations and
immutable afterward.

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
- Customer-support retrieval uses Foundry IQ with no request-selectable mode or
  fallback. Directive retrieval uses only its strict backend gateway tools.
- The native Prompt Agent exposes only Foundry IQ knowledge retrieval. The
  support Hosted MAF Agent additionally calls application tools through the
  app-only public frontend proxy. The directive Hosted MAF Agent has a separate
  gateway/tool allowlist; backend ingress remains internal.
- Public history DTOs expose safe agent labels/version metadata but never owner,
  physical routing, Foundry conversation, Hosted session, response, or ETag data.

## Directive v2 operations

Production Terraform supplies `DIRECTIVE_SEARCH_INDEX=directive-chunks-v2` and
`DIRECTIVE_PROCESSING_VERSION=directive-v2-czech-layout`. The manual ingestion
job remains the only publication trigger. A maintenance-window cutover must
run the guarded derived-data reset, image deployment, managed-identity
preflight, metadata-only validation with operator confirmation, full ingestion,
and cross-store verification in that order.
The deployment approval token is freshly derived from the exact validation
execution and its sanitized summary; reusable confirmation constants are
rejected. Reset maintenance mode is nonpublishing until that validated flow
deliberately starts the approved per-execution publication override.
Terraform keeps the job template in `maintenance`; validate and publish are
separate script phases, and only the approved publication execution receives a
per-execution `run-daily` override. The inventory operator receives only
source-container Blob Data Reader on the protected source container; artifact
cleanup remains separately scoped.

The reset automation is dry-run by default and protects the
`directive-source` container. It deletes and recreates only the derived
Cosmos containers with their existing partition keys and purges derived
artifact prefixes. Keep Search v1 until the sanitized v2 verification evidence
has been inspected; the separate guarded finalize command then deletes v1.
Recovery is always a rebuild from the preserved source container, not a backup
or data-conversion rollback.
