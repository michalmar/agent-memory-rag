"""FastAPI trust boundary for selectable remote Foundry agents."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

load_dotenv()

from agent_contracts import (
    AgentType,
    render_instructions,
)
from .agent_mcp import application_tools_mcp_app
from .agent_tool_gateway import AgentToolRequest, dispatch_agent_tool
from .auth import AgentCaller, User, get_agent_caller, get_current_user
from .backend_services import BackendServices, visible_agent_types
from .chat_service import ChatTurnService
from .config import get_settings
from .conversation_history import (
    public_conversation_detail,
)
from .conversation_memory import (
    MemoryStoreUnavailable,
    public_memory,
)
from .telemetry import configure_telemetry
from .user_profile_memory import public_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("azure").setLevel(logging.WARNING)
configure_telemetry()

services = BackendServices.build()
conversation_registry = services.conversation_registry
history_store = services.history_store
profile_store = services.profile_store
memory_store = services.memory_store
memory_agent = services.memory_agent
profile_agent = services.profile_agent
tool_executor = services.tool_executor
tool_executors = services.tool_executors
runtime_registry = services.runtime_registry
conversation_coordinator = services.conversation_coordinator


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    async with application_tools_mcp_app.router.lifespan_context(
        application_tools_mcp_app
    ):
        try:
            await services.start(get_settings())
            yield
        finally:
            await services.close()


app = FastAPI(title="Agentic Memory Backend", lifespan=lifespan)
app.mount("/mcp", application_tools_mcp_app)


@app.exception_handler(MemoryStoreUnavailable)
async def memory_store_unavailable(
    request: Request, exc: MemoryStoreUnavailable
) -> JSONResponse:
    del request, exc
    return JSONResponse(
        status_code=503,
        content={"detail": "Semantic memory store unavailable"},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOW_ORIGINS", "http://localhost:5175,http://127.0.0.1:5175"
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Conversation-ID"],
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    conversation_id: str | None = None
    agent_type: AgentType

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message must not be empty")
        return message


@app.get("/me")
async def me(user: User = Depends(get_current_user)):
    return user.to_dict()


@app.get("/prompts/customer-support")
async def get_customer_support_prompt(user: User = Depends(get_current_user)):
    del user
    return {"name": "customer-support", "content": render_instructions()}


@app.get("/agents")
async def list_agents(user: User = Depends(get_current_user)):
    del user
    labels = {
        AgentType.FOUNDRY_PROMPT: "Foundry Prompt Agent",
        AgentType.AGENT_FRAMEWORK: "Hosted Agent Framework",
        AgentType.DIRECTIVE_RAG: "Directive Assistant",
    }
    return {
        "retrieval": "Foundry IQ",
        "agents": [
            {
                "agent_type": agent_type.value,
                "label": labels[agent_type],
                "available": services.agent_available(
                    agent_type,
                    get_settings(),
                ),
            }
            for agent_type in visible_agent_types(get_settings())
        ],
    }


@app.post("/chat")
async def chat(request: ChatRequest, user: User = Depends(get_current_user)):
    chat_service = ChatTurnService(
        conversation_coordinator,
        conversation_registry,
        history_store,
    )
    return await chat_service.create_response(
        message=request.message,
        conversation_id=request.conversation_id,
        agent_type=request.agent_type,
        user_id=user.user_id,
    )


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health")
async def health():
    return await health_live()


@app.get("/health/ready")
async def health_ready():
    payload = await services.readiness(get_settings())
    return (
        payload
        if payload["status"] == "ready"
        else JSONResponse(status_code=503, content=payload)
    )


@app.get("/conversations")
async def list_conversations(user: User = Depends(get_current_user)):
    return await history_store.list_conversations(user.user_id)


@app.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, user: User = Depends(get_current_user)
):
    document = await history_store.get_conversation(
        conversation_id, user.user_id
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return public_conversation_detail(document)


class UpdateTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)


@app.put("/conversations/{conversation_id}/title")
async def update_conversation_title(
    conversation_id: str,
    request: UpdateTitleRequest,
    user: User = Depends(get_current_user),
):
    document = await history_store.update_title(
        conversation_id, user.user_id, request.title
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return public_conversation_detail(document)


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str, user: User = Depends(get_current_user)
):
    await conversation_coordinator.delete(conversation_id, user.user_id)
    return {"deleted": conversation_id}


@app.post("/internal/agent-tools/{tool_name}")
async def invoke_agent_tool(
    tool_name: str,
    request: AgentToolRequest,
    caller: AgentCaller = Depends(get_agent_caller),
):
    result = await dispatch_agent_tool(
        tool_name,
        request,
        caller,
        history_store,
        tool_executors,
    )
    return result.to_dict()


@app.get("/profile")
async def get_profile(user: User = Depends(get_current_user)):
    profile = await profile_store.get_profile(user.user_id)
    return (
        public_profile(profile)
        if profile
        else {"version": 0}
    )


class ProfilePutRequest(BaseModel):
    sections: dict


@app.put("/profile")
async def put_profile(
    request: ProfilePutRequest, user: User = Depends(get_current_user)
):
    document = await profile_store.upsert_profile(
        user.user_id, request.sections
    )
    if document is None:
        raise HTTPException(status_code=503, detail="Profile store unavailable")
    return public_profile(document)


@app.delete("/profile")
async def delete_profile(user: User = Depends(get_current_user)):
    await profile_store.delete_profile(user.user_id)
    return {"deleted": True}


class ProfileGenerateRequest(BaseModel):
    conversation_id: str


@app.post("/profile/generate")
async def generate_profile(
    request: ProfileGenerateRequest, user: User = Depends(get_current_user)
):
    document = await history_store.get_conversation(
        request.conversation_id, user.user_id
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    sections = await profile_agent.extract(
        document.get("messages", []), document.get("title")
    )
    if not sections:
        return {"updated": False, "sections": {}}
    source = {
        "conversation_id": request.conversation_id,
        "title": document.get("title"),
    }
    updated = await profile_store.upsert_profile(
        user.user_id, sections, source
    )
    if updated is None:
        raise HTTPException(status_code=503, detail="Profile store unavailable")
    return {
        "updated": True,
        "profile": public_profile(updated),
    }


@app.get("/memories")
async def list_memories(user: User = Depends(get_current_user)):
    rows = await memory_store.list_memories(user.user_id)
    return [public_memory(row) for row in rows]


class MemoryCreateRequest(BaseModel):
    conversation_id: str
    title: str | None = None


@app.post("/memories", status_code=201)
async def create_memory(
    request: MemoryCreateRequest, user: User = Depends(get_current_user)
):
    if not memory_store.enabled:
        raise MemoryStoreUnavailable("Semantic memory store is not initialized")
    document = await history_store.get_conversation(
        request.conversation_id, user.user_id
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = document.get("messages", [])
    result = await memory_agent.create_memory(
        messages, request.title or document.get("title")
    )
    row = await memory_store.create_memory(
        conversation_id=request.conversation_id,
        user_id=user.user_id,
        summary=result.summary,
        embedding=result.embedding,
        source_title=request.title or document.get("title"),
        message_count=len(messages),
    )
    return public_memory(row)


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = Field(default=3, ge=1, le=50)


@app.post("/memories/search")
async def search_memories(
    request: MemorySearchRequest, user: User = Depends(get_current_user)
):
    from .azure_clients import embed_text

    embedding = await embed_text(request.query)
    rows = await memory_store.search(
        user.user_id, embedding, limit=request.limit
    )
    return [public_memory(row) for row in rows]


@app.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str, user: User = Depends(get_current_user)
):
    if not await memory_store.delete_memory(memory_id, user.user_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": memory_id}
