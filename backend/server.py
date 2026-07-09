"""FastAPI backend — vertical slice (Challenges 01 groundwork + chat).

Runs fully offline: with no Azure env set, `/chat` uses the mock LLM runner and the
in-memory SessionManager. AG-UI events are encoded with ag_ui's EventEncoder (§B4).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from ag_ui.core.events import (
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
)
from ag_ui.encoder import EventEncoder
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from agent_runner import build_runner
from auth import User, get_current_user
from session_manager import SessionManager

import agent_tools
from classic_rag_client import ClassicRAGClient
from config import get_settings
from conversation_history import ConversationHistoryStore
from conversation_memory import ConversationMemoryStore
from memory_agent import MemoryAgent
from profile_agent import ProfileAgent
from user_profile_memory import (
    UserProfileMemoryStore,
    profile_to_prompt_context,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("server")
# Quieten noisy libraries.
logging.getLogger("azure").setLevel(logging.WARNING)

PROMPTS_DIR = Path(__file__).parent / "prompts"
_jinja = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    autoescape=False,
)

VALID_RAG_MODES = {"none", "agentic", "classic"}

session_manager = SessionManager()
history_store = ConversationHistoryStore()
profile_store = UserProfileMemoryStore()
memory_store = ConversationMemoryStore()
classic_rag = ClassicRAGClient()
memory_agent = MemoryAgent()
profile_agent = ProfileAgent()


def _resolve_llm_mode() -> str:
    return get_settings().resolve_llm_mode()


def _render_system_prompt(user_profile: dict | None, rag_mode: str) -> str:
    template = _jinja.get_template("customer_support.j2")
    return template.render(user_profile=user_profile or {}, rag_mode=rag_mode)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await session_manager.connect()
    for store in (history_store, profile_store, memory_store):
        try:
            await store.initialize()
        except Exception:  # noqa: BLE001
            logger.exception("[startup] store init failed: %s", type(store).__name__)
    agent_tools.set_stores(
        memory_store=memory_store, profile_store=profile_store, classic_rag=classic_rag
    )
    logger.info(
        "[startup] backend ready (llm_mode=%s history=%s profile=%s memory=%s search=%s)",
        _resolve_llm_mode(),
        history_store.enabled,
        profile_store.enabled,
        memory_store.enabled,
        classic_rag.enabled,
    )
    yield
    for store in (history_store, profile_store, memory_store):
        try:
            await store.close()
        except Exception:  # noqa: BLE001
            pass
    await session_manager.close()


app = FastAPI(title="Agentic Memory Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.getenv(
            "CORS_ALLOW_ORIGINS", "http://localhost:5175,http://127.0.0.1:5175"
        ).split(",")
        if o.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-ID"],
)


# ---------------------------------------------------------------- models
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    thread_id: str | None = None
    rag_mode: str = "agentic"


# ---------------------------------------------------------------- meta
@app.get("/me")
async def me(user: User = Depends(get_current_user)):
    return user.to_dict()


@app.get("/prompts/{name}")
async def get_prompt(name: str, user: User = Depends(get_current_user)):
    safe = name if name.endswith(".j2") else f"{name}.j2"
    try:
        template = _jinja.get_template(safe)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {name}")
    rendered = template.render(user_profile={}, rag_mode="agentic")
    return {"name": name, "content": rendered}


# ---------------------------------------------------------------- chat (SSE)
@app.post("/chat")
async def chat(req: ChatRequest, user: User = Depends(get_current_user)):
    rag_mode = req.rag_mode if req.rag_mode in VALID_RAG_MODES else "agentic"

    user_message = ""
    for m in reversed(req.messages):
        if m.role == "user":
            user_message = m.content
            break

    session_id = req.thread_id or str(uuid.uuid4())
    await session_manager.assert_session_owner(session_id, user.user_id)
    session = await session_manager.get_session(session_id, auto_create=True)
    if session.user_id is None:
        session.user_id = user.user_id

    profile_ctx = {}
    try:
        profile = await profile_store.get_profile(user.user_id)
        profile_ctx = profile_to_prompt_context(profile)
    except Exception:  # noqa: BLE001
        logger.exception("[chat] profile fetch failed")

    instructions = _render_system_prompt(user_profile=profile_ctx, rag_mode=rag_mode)
    runner = build_runner(_resolve_llm_mode(), instructions, rag_mode)

    encoder = EventEncoder()
    run_id = str(uuid.uuid4())

    async def event_stream():
        logger.info("[IN] chat user=%s session=%s rag=%s", user.user_id, session_id, rag_mode)
        token = agent_tools.current_user_id.set(user.user_id)
        assistant_text = ""
        try:
            yield encoder.encode(RunStartedEvent(thread_id=session_id, run_id=run_id))
            async for ev in runner.stream(user_message, session, rag_mode):
                if isinstance(ev, TextMessageContentEvent):
                    assistant_text += ev.delta
                yield encoder.encode(ev)
            yield encoder.encode(RunFinishedEvent(thread_id=session_id, run_id=run_id))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[chat] run failed")
            yield encoder.encode(RunErrorEvent(message=str(exc), code="RUN_ERROR"))
            return
        finally:
            agent_tools.current_user_id.reset(token)

        # Persist the turn into the in-memory session + durable history store.
        session.messages.append({"role": "user", "content": user_message})
        session.messages.append({"role": "assistant", "content": assistant_text})
        await session_manager.increment_message_count(session_id, 2)
        await session_manager.save_session_state(session)
        await history_store._persist_turn(
            session_id, user.user_id, user_message, assistant_text,
            title=session.title, rag_mode=rag_mode,
        )
        logger.info("[OUT] chat session=%s chars=%d", session_id, len(assistant_text))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-ID": session_id,
        },
    )


# ---------------------------------------------------------------- sessions (minimal)
class CreateSessionRequest(BaseModel):
    title: str | None = None


@app.post("/sessions", status_code=201)
async def create_session(req: CreateSessionRequest, user: User = Depends(get_current_user)):
    session = await session_manager.create_session(title=req.title, user_id=user.user_id)
    return session.to_dict()


@app.get("/sessions")
async def list_sessions(user: User = Depends(get_current_user)):
    return await session_manager.list_sessions(user_id=user.user_id)


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, user: User = Depends(get_current_user)):
    await session_manager.assert_session_owner(session_id, user.user_id)
    info = await session_manager.get_session_info(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return info


@app.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str, user: User = Depends(get_current_user)):
    await session_manager.assert_session_owner(session_id, user.user_id)
    session = await session_manager.get_session(session_id, auto_create=False)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": session.messages}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: User = Depends(get_current_user)):
    await session_manager.assert_session_owner(session_id, user.user_id)
    await session_manager.delete_session(session_id)
    return {"deleted": session_id}


@app.get("/health")
async def health():
    return {"status": "ok", "llm_mode": _resolve_llm_mode()}


# ---------------------------------------------------------------- conversations (history)
@app.get("/conversations")
async def list_conversations(user: User = Depends(get_current_user)):
    return await history_store.list_conversations(user.user_id)


@app.get("/conversations/{session_id}")
async def get_conversation(session_id: str, user: User = Depends(get_current_user)):
    doc = await history_store.get_conversation(session_id, user.user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return doc


class UpdateTitleRequest(BaseModel):
    title: str


@app.put("/conversations/{session_id}/title")
async def update_conversation_title(
    session_id: str, req: UpdateTitleRequest, user: User = Depends(get_current_user)
):
    doc = await history_store.update_title(session_id, user.user_id, req.title)
    if doc is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return doc


@app.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str, user: User = Depends(get_current_user)):
    ok = await history_store.delete_conversation(session_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await memory_store.delete_by_conversation(session_id, user.user_id)
    return {"deleted": session_id}


# ---------------------------------------------------------------- user profile
@app.get("/profile")
async def get_profile(user: User = Depends(get_current_user)):
    profile = await profile_store.get_profile(user.user_id)
    return profile or {"user_id": user.user_id, "version": 0}


class ProfilePutRequest(BaseModel):
    sections: dict


@app.put("/profile")
async def put_profile(req: ProfilePutRequest, user: User = Depends(get_current_user)):
    doc = await profile_store.upsert_profile(user.user_id, req.sections)
    if doc is None:
        raise HTTPException(status_code=503, detail="Profile store unavailable")
    return doc


@app.delete("/profile")
async def delete_profile(user: User = Depends(get_current_user)):
    await profile_store.delete_profile(user.user_id)
    return {"deleted": user.user_id}


class ProfileGenerateRequest(BaseModel):
    session_id: str


@app.post("/profile/generate")
async def generate_profile(
    req: ProfileGenerateRequest, user: User = Depends(get_current_user)
):
    doc = await history_store.get_conversation(req.session_id, user.user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    sections = await profile_agent.extract(doc.get("messages", []), doc.get("title"))
    if not sections:
        return {"updated": False, "sections": {}}
    source = {"session_id": req.session_id, "title": doc.get("title")}
    updated = await profile_store.upsert_profile(user.user_id, sections, source)
    return {"updated": True, "profile": updated}


# ---------------------------------------------------------------- conversation memory
@app.get("/memories")
async def list_memories(user: User = Depends(get_current_user)):
    return await memory_store.list_memories(user.user_id)


class MemoryCreateRequest(BaseModel):
    conversation_id: str
    title: str | None = None


@app.post("/memories", status_code=201)
async def create_memory(
    req: MemoryCreateRequest, user: User = Depends(get_current_user)
):
    doc = await history_store.get_conversation(req.conversation_id, user.user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = doc.get("messages", [])
    result = await memory_agent.create_memory(messages, req.title or doc.get("title"))
    row = await memory_store.create_memory(
        conversation_id=req.conversation_id,
        user_id=user.user_id,
        summary=result.summary,
        embedding=result.embedding,
        source_title=req.title or doc.get("title"),
        message_count=len(messages),
    )
    if row is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    return row


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 3


@app.post("/memories/search")
async def search_memories(
    req: MemorySearchRequest, user: User = Depends(get_current_user)
):
    from azure_clients import embed_text

    embedding = await embed_text(req.query)
    return await memory_store.search(user.user_id, embedding, limit=req.limit)


@app.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, user: User = Depends(get_current_user)):
    ok = await memory_store.delete_memory(memory_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": memory_id}
