# Agentic AI Memory — Support Chat (Challenges 1–5)

Reference implementation of the customer-support chat app described in
[`docs/PRD-Solution-Challenges-1-5.md`](docs/PRD-Solution-Challenges-1-5.md).
The full design and phased delivery plan live in
[`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md).

## What's implemented (P0–P2 vertical slice)

A working, **fully offline** slice of the stack:

- **Backend** (`backend/`) — FastAPI app exposing `/chat` (AG-UI SSE stream),
  `/me`, `/prompts/{name}`, `/sessions*`, `/health`. Session memory is
  **in-memory only** (no Redis). Ships with a **mock LLM runner** so it runs
  with zero Azure access; a real Azure AI Foundry runner is scaffolded behind an
  env flag.
- **Frontend** (`frontend/`) — Vite + Lit single-page app with an A2UI surface
  renderer. Streams the assistant response, renders Markdown, and inflates tool
  results into A2UI cards (shipping status, RAG citations).

Later phases (Cosmos history/profile, Postgres/pgvector memory, AI Search RAG,
Terraform infra, Container Apps deploy) are described in the implementation plan
and not yet built.

## Run it offline (two terminals)

**Backend** — needs [uv](https://docs.astral.sh/uv/) and Python 3.11:

```bash
cd backend
uv venv --python 3.11
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m uvicorn server:app --port 8000
```

**Frontend** — needs Node 20+:

```bash
cd frontend
npm install
npm run dev   # http://localhost:5175  (proxies /api → :8000)
```

Open http://localhost:5175 and try: **“Where is my order ORD-001?”**
Switch mock users (alice/bob/charlie) and toggle RAG/theme from the header.

Mock orders: `ORD-001` (shipped), `ORD-002` (processing), `ORD-003` (delivered).

## Repo layout

```
backend/    FastAPI app, prompts, mock + real agent runners
frontend/   Vite + Lit app, A2UI renderer, tool→surface converters
docs/        PRD spec + implementation plan
```

## Authentication (mock vs Entra ID)

The app supports two auth modes, selected by the backend `AUTH_MODE` env var:

- **`mock`** (default, used for local dev and the live demo): the frontend sends an
  `X-Mock-User-ID` header; the backend resolves a fixed user table
  (`user-alice` / `user-bob` / `user-charlie`). No directory setup required.
- **`entra`** (production): the frontend signs in with MSAL and sends an
  `Authorization: Bearer <JWT>`; the backend validates the RS256 token against the
  tenant JWKS (audience / issuer / expiry, plus optional required scopes/roles).

### The Entra app registration is a **manual** step — *not* Terraform

The `infra/` Terraform provisions all Azure **resources** (Foundry, Cosmos, Postgres,
AI Search, ACA, networking, RBAC) using subscription-scoped permissions. The Entra
**app registration** is intentionally kept **out of Terraform** because creating one
requires **Entra directory permissions** (Application Administrator /
`Application.ReadWrite.All`) — a different, often separately-governed grant than the
subscription Contributor role used for everything else. Keeping it manual lets the
infra apply cleanly for operators who don't hold directory rights, and keeps the app
runnable in `mock` mode with zero directory setup.

Provision it with the helper script (requires directory rights):

```bash
AZURE_CONFIG_DIR="$HOME/.azure-365" \
  ./scripts/create_entra_app.sh \
    --frontend-url https://<frontend-fqdn> \
    --localhost            # also allow http://localhost:5175 for dev
```

The script creates one SPA app registration that exposes an `access_as_user` scope,
issues **v2** access tokens, registers the SPA redirect URI, and pre-authorizes the
Azure CLI (so you can fetch a test token). It prints the exact env values to set.
Note: for v2 tokens `aud` is the **client-id GUID**, so `ENTRA_AUDIENCE=<clientId>`
(not `api://<clientId>`).

Then flip both apps to Entra mode (backend `AUTH_MODE=entra` + `ENTRA_TENANT_ID` /
`ENTRA_AUDIENCE` / `ENTRA_REQUIRED_SCOPES`; frontend `/config.js` `authMode=entra` +
`ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID` / `ENTRA_API_SCOPE`) and redeploy. Verify:

```bash
TOKEN=$(az account get-access-token --scope api://<clientId>/access_as_user --query accessToken -o tsv)
curl -H "Authorization: Bearer $TOKEN" https://<frontend-fqdn>/api/me   # -> 200
curl https://<frontend-fqdn>/api/me                                     # -> 401
```
