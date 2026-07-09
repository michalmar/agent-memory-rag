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
