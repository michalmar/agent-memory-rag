# Backend — FastAPI + AG-UI SSE

Vertical-slice backend for the support-chat app. Runs **fully offline**: with no
Azure environment variables set, `/chat` uses a mock LLM runner and an in-memory
session store, so no Azure resources are required to develop or demo.

## Requirements

- Python **3.11** (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)

## Setup & run

```bash
uv venv --python 3.11
uv pip install --python .venv/bin/python -e .
.venv/bin/python -m uvicorn server:app --port 8000 --reload
```

## Endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/chat` | AG-UI event stream (SSE). Returns `X-Session-ID` header. |
| GET | `/me` | Current user (mock header auth). |
| GET | `/prompts/{name}` | Rendered Jinja prompt. |
| POST/GET/DELETE | `/sessions*` | Minimal session management. |
| GET | `/health` | Reports LLM mode (`mock`/`real`). |

## Auth (mock mode)

Send `X-Mock-User-ID: user-alice` (or `user-bob` / `user-charlie`). Missing or
unknown IDs return `401`.

```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'X-Mock-User-ID: user-alice' -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"track ORD-001"}],"rag_mode":"agentic"}'
```

## LLM mode

`LLM_MODE=mock|real` forces the runner. When unset it defaults to `real` only if
`AZURE_OPENAI_ENDPOINT` is present, otherwise `mock`. See `.env.example`.

## Notes

- **In-memory sessions only** — they do not survive a restart or a second
  replica. When deployed to Container Apps the backend must be pinned to a single
  replica (min=max=1).
- The real Azure AI Foundry runner (`RealAgentRunner`) is scaffolded and imports
  `agent-framework` lazily, so offline startup never fails even without the
  optional `azure` extra installed.
