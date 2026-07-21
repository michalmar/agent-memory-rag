"""FastAPI trust boundary for selectable remote Foundry agents."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from ag_ui.core.events import RunErrorEvent, RunFinishedEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

load_dotenv()

from agent_contracts import (
    AgentType,
    TurnContext,
    render_instructions,
)
from .agent_tools import ToolExecutor
from .agent_mcp import application_tools_mcp_app
from .agent_runtime_contracts import AgentRuntime
from .agent_tool_gateway import AgentToolRequest, dispatch_agent_tool
from .agui_adapter import to_agui_events
from .auth import AgentCaller, User, get_agent_caller, get_current_user
from .azure_clients import close_azure_clients
from .config import get_settings
from .conversation_coordinator import ConversationCoordinator
from .conversation_history import (
    ConversationHistoryStore,
    public_conversation_detail,
)
from .conversation_memory import (
    ConversationMemoryStore,
    MemoryStoreUnavailable,
    public_memory,
)
from .conversation_registry import ConversationRegistry
from .foundry_hosted_maf_runtime import FoundryHostedMafRuntime
from .foundry_iq_health import FoundryIqHealthProbe
from .foundry_prompt_runtime import FoundryPromptRuntime
from .health import run_readiness_check
from .memory_agent import MemoryAgent
from .mock_agent_runtime import MockAgentRuntime
from .profile_agent import ProfileAgent
from .telemetry import configure_telemetry, span
from .turn_accumulator import TurnAccumulator
from .user_profile_memory import UserProfileMemoryStore, public_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("server")
logging.getLogger("azure").setLevel(logging.WARNING)
configure_telemetry()

conversation_registry = ConversationRegistry()
history_store = ConversationHistoryStore()
profile_store = UserProfileMemoryStore()
memory_store = ConversationMemoryStore()
foundry_iq_health = FoundryIqHealthProbe()
memory_agent = MemoryAgent()
profile_agent = ProfileAgent()
tool_executor = ToolExecutor(memory_store, profile_store)
runtime_registry: dict[AgentType, AgentRuntime] = {}
conversation_coordinator = ConversationCoordinator(
    conversation_registry,
    history_store,
    memory_store,
    runtime_registry,
)


async def _initialize_component(name: str, component: Any) -> bool:
    try:
        with span("component.initialize", {"component.name": name}):
            await component.initialize()
        return bool(getattr(component, "enabled", True))
    except Exception:
        logger.exception("[startup] component init failed: %s", name)
        return False


async def _close_component(name: str, component: Any) -> None:
    try:
        await component.close()
    except Exception:
        logger.exception("[shutdown] component close failed: %s", name)


async def _initialize_runtime(agent_type: AgentType, runtime: AgentRuntime) -> None:
    name = f"runtime_{agent_type.value}"
    if await _initialize_component(name, runtime):
        runtime_registry[agent_type] = runtime


@asynccontextmanager
async def _backend_lifespan():
    components = (
        ("cosmos_history", history_store),
        ("cosmos_profile", profile_store),
        ("cosmos_memory", memory_store),
        ("foundry_iq", foundry_iq_health),
    )
    await asyncio.gather(
        *(_initialize_component(name, component) for name, component in components)
    )
    settings = get_settings()
    if settings.resolve_llm_mode() == "mock":
        await asyncio.gather(
            *(
                _initialize_runtime(
                    agent_type, MockAgentRuntime(agent_type, tool_executor)
                )
                for agent_type in AgentType
            )
        )
    else:
        pending = []
        if settings.foundry_prompt_enabled:
            pending.append(
                _initialize_runtime(
                    AgentType.FOUNDRY_PROMPT, FoundryPromptRuntime()
                )
            )
        if settings.foundry_hosted_enabled:
            pending.append(
                _initialize_runtime(
                    AgentType.AGENT_FRAMEWORK, FoundryHostedMafRuntime()
                )
            )
        if pending:
            await asyncio.gather(*pending)

    logger.info(
        "[startup] backend initialized (history=%s profile=%s memory=%s runtimes=%s)",
        history_store.enabled,
        profile_store.enabled,
        memory_store.enabled,
        ",".join(runtime.value for runtime in runtime_registry),
    )
    yield
    await asyncio.gather(
        *(
            _close_component(f"runtime_{agent_type.value}", runtime)
            for agent_type, runtime in runtime_registry.items()
        ),
        *(
            _close_component(name, component)
            for name, component in reversed(components)
        ),
    )
    runtime_registry.clear()
    await close_azure_clients()
    conversation_registry.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    async with application_tools_mcp_app.router.lifespan_context(
        application_tools_mcp_app
    ):
        async with _backend_lifespan():
            yield


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


class ConversationPersistenceError(RuntimeError):
    pass


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
    }
    return {
        "retrieval": "Foundry IQ",
        "agents": [
            {
                "agent_type": agent_type.value,
                "label": labels[agent_type],
                "available": agent_type in runtime_registry,
            }
            for agent_type in AgentType
        ],
    }


@app.post("/chat")
async def chat(request: ChatRequest, user: User = Depends(get_current_user)):
    prepared = await conversation_coordinator.prepare(
        conversation_id=request.conversation_id,
        agent_type=request.agent_type,
        user_id=user.user_id,
        initial_title=request.message[:80],
    )
    conversation = prepared.conversation
    if conversation.runtime_state is None:
        raise HTTPException(status_code=500, detail="Runtime state is missing")
    conversation_id = conversation.conversation_id
    encoder = EventEncoder()
    run_id = str(uuid.uuid4())
    lease = await conversation_registry.acquire(conversation_id)

    async def event_stream():
        turn = TurnAccumulator(request.message)
        with span(
            "agent.run",
            {
                "agent.type": request.agent_type.value,
                "agent.release_id": conversation.runtime_state.descriptor.release_id,
                "session.id": conversation_id,
            },
        ) as current_span:
            try:
                yield encoder.encode(
                    RunStartedEvent(thread_id=conversation_id, run_id=run_id)
                )
                context = TurnContext(
                    application_conversation_id=conversation_id,
                    authenticated_user_id=user.user_id,
                    runtime_state=conversation.runtime_state,
                )
                async for normalized in prepared.runtime.stream_turn(
                    request.message, context
                ):
                    turn.consume(normalized)
                    for event in to_agui_events(normalized):
                        yield encoder.encode(event)

                records = turn.message_records()
                try:
                    await history_store.append_messages(
                        conversation_id,
                        user.user_id,
                        records,
                        conversation.runtime_state,
                        title=conversation.title,
                    )
                except Exception as exc:
                    raise ConversationPersistenceError from exc
                conversation.touch()
                current_span.set_attribute(
                    "agent.response_length", len(turn.assistant_text)
                )
                yield encoder.encode(
                    RunFinishedEvent(thread_id=conversation_id, run_id=run_id)
                )
            except ConversationPersistenceError:
                current_span.set_attribute(
                    "error.type", "ConversationPersistenceError"
                )
                logger.exception(
                    "[chat] conversation persistence failed (session=%s agent=%s)",
                    conversation_id,
                    request.agent_type.value,
                )
                yield encoder.encode(
                    RunErrorEvent(
                        message="Conversation could not be saved",
                        code="CONVERSATION_PERSISTENCE_FAILED",
                    )
                )
            except HTTPException as exc:
                current_span.set_attribute("error.type", "HTTPException")
                yield encoder.encode(
                    RunErrorEvent(message="Agent run failed", code=str(exc.detail))
                )
            except Exception as exc:
                current_span.set_attribute("error.type", type(exc).__name__)
                logger.exception(
                    "[chat] run failed (session=%s agent=%s)",
                    conversation_id,
                    request.agent_type.value,
                )
                yield encoder.encode(
                    RunErrorEvent(message="Agent run failed", code="RUN_ERROR")
                )
            finally:
                await lease.release()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Conversation-ID": conversation_id,
        },
        background=BackgroundTask(lease.release),
    )


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health")
async def health():
    return await health_live()


async def _gateway_health() -> None:
    settings = get_settings()
    if not (
        settings.agent_gateway_audience and settings.hosted_agent_principal_ids
    ):
        raise RuntimeError("Hosted Agent gateway authorization is not configured")


@app.get("/health/ready")
async def health_ready():
    settings = get_settings()
    checks: dict[str, Callable[[], Awaitable[None]]] = {}
    optional_checks: set[str] = set()
    if settings.cosmos_configured:
        checks["cosmos_history"] = history_store.health_check
        checks["cosmos_profile"] = profile_store.health_check
        checks["cosmos_memory"] = memory_store.health_check
        optional_checks.add("cosmos_memory")
    if settings.search_configured:
        checks["foundry_iq"] = foundry_iq_health.health_check
    if settings.foundry_prompt_enabled:
        runtime = runtime_registry.get(AgentType.FOUNDRY_PROMPT)
        checks["foundry_prompt"] = (
            runtime.health_check
            if runtime is not None
            else _unavailable_health_check
        )
    if settings.foundry_hosted_enabled:
        runtime = runtime_registry.get(AgentType.AGENT_FRAMEWORK)
        checks["foundry_hosted_maf"] = (
            runtime.health_check
            if runtime is not None
            else _unavailable_health_check
        )
        checks["hosted_tool_gateway"] = _gateway_health

    completed = await asyncio.gather(
        *(
            run_readiness_check(
                name, check, settings.readiness_timeout_seconds
            )
            for name, check in checks.items()
        )
    )
    results = dict(completed)
    for name, result in results.items():
        result["required"] = name not in optional_checks
    ready = all(
        result["status"] == "ok"
        for name, result in results.items()
        if name not in optional_checks
    )
    degraded = sorted(
        name
        for name in optional_checks
        if results.get(name, {}).get("status") != "ok"
    )
    payload = {
        "status": "ready" if ready else "not_ready",
        "dependencies": results,
        "degraded_dependencies": degraded,
        "agents": {
            agent_type.value: agent_type in runtime_registry
            for agent_type in AgentType
        },
    }
    return payload if ready else JSONResponse(status_code=503, content=payload)


async def _unavailable_health_check() -> None:
    raise RuntimeError("Runtime is unavailable")


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
        tool_name, request, caller, history_store, tool_executor
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
