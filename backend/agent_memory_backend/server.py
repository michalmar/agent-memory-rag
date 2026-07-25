"""FastAPI trust boundary for selectable remote Foundry agents."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

load_dotenv()

from agent_contracts import (
    AgentType,
    render_instructions,
)
from directive_contracts import (
    DIRECTIVE_SOURCE_FILENAME_PATTERN,
    parse_directive_source_filename,
)
from .agent_mcp import application_tools_mcp_app
from .agent_tool_gateway import (
    AgentStateRequest,
    AgentStateTurnRequest,
    AgentToolRequest,
    complete_agent_state_turn,
    dispatch_agent_tool,
    fail_agent_state_turn,
    resolve_agent_state,
)
from .auth import (
    AgentCaller,
    User,
    can_manage_directive_sources,
    get_agent_caller,
    get_current_user,
    require_directive_source_manager,
)
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
from .directive_documents import DirectiveDocumentResponse
from .directive_errors import DirectiveDataUnavailable
from .directive_sources import (
    DirectiveSourceConflict,
    DirectiveSourceInvalid,
    DirectiveSourceItem,
    DirectiveSourceNotFound,
    DirectiveSourcePage,
    DirectiveSourceTooLarge,
)
from .telemetry import configure_telemetry, span
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
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
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
    result = user.to_dict()
    result["can_manage_directive_sources"] = can_manage_directive_sources(user)
    return result


DirectiveSourceFilename = Annotated[
    str,
    Path(
        pattern=DIRECTIVE_SOURCE_FILENAME_PATTERN,
        max_length=255,
    ),
]
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@app.get(
    "/directive-sources",
    response_model=DirectiveSourcePage,
)
async def list_directive_sources(
    request: Request,
    cursor: str | None = None,
    limit: int = 50,
    user: User = Depends(require_directive_source_manager),
):
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="Source page limit must be 1..100",
        )
    try:
        result = await services.directive_sources.list_sources(
            cursor=cursor,
            limit=limit,
        )
    except DirectiveSourceInvalid as exc:
        _record_source_audit(request, user, "list", result="invalid")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DirectiveDataUnavailable as exc:
        _record_source_audit(request, user, "list", result="unavailable")
        raise HTTPException(
            status_code=503,
            detail="Directive sources are temporarily unavailable",
        ) from exc
    _record_source_audit(request, user, "list", result="success")
    return result


@app.post(
    "/directive-sources/upload/{filename}",
    response_model=DirectiveSourceItem,
    status_code=201,
)
async def upload_directive_source(
    filename: DirectiveSourceFilename,
    request: Request,
    user: User = Depends(require_directive_source_manager),
):
    settings = get_settings()
    with tempfile.SpooledTemporaryFile(
        max_size=min(
            settings.directive_source_max_upload_bytes,
            4 * 1024 * 1024,
        ),
        mode="w+b",
    ) as upload:
        try:
            size_bytes = await _spool_source_upload(
                request,
                upload,
                settings.directive_source_max_upload_bytes,
            )
            result = await services.directive_sources.upload_source(
                filename,
                upload,
                size_bytes,
            )
        except DirectiveSourceTooLarge as exc:
            _record_source_audit(
                request,
                user,
                "upload",
                filename=filename,
                result="too_large",
            )
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except DirectiveSourceInvalid as exc:
            _record_source_audit(
                request,
                user,
                "upload",
                filename=filename,
                result="invalid",
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DirectiveSourceConflict as exc:
            _record_source_audit(
                request,
                user,
                "upload",
                filename=filename,
                size_bytes=size_bytes,
                result="conflict",
            )
            raise HTTPException(
                status_code=409,
                detail="Directive source already exists",
            ) from exc
        except DirectiveDataUnavailable as exc:
            _record_source_audit(
                request,
                user,
                "upload",
                filename=filename,
                result="unavailable",
            )
            raise HTTPException(
                status_code=503,
                detail="Directive source upload is temporarily unavailable",
            ) from exc
    _record_source_audit(
        request,
        user,
        "upload",
        filename=filename,
        size_bytes=result.size_bytes,
        result="success",
    )
    return result


@app.delete("/directive-sources/{filename}")
async def delete_directive_source(
    filename: DirectiveSourceFilename,
    request: Request,
    user: User = Depends(require_directive_source_manager),
):
    try:
        await services.directive_sources.delete_source(filename)
    except DirectiveSourceInvalid as exc:
        _record_source_audit(
            request,
            user,
            "delete",
            filename=filename,
            result="invalid",
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DirectiveSourceNotFound as exc:
        _record_source_audit(
            request,
            user,
            "delete",
            filename=filename,
            result="not_found",
        )
        raise HTTPException(
            status_code=404,
            detail="Directive source not found",
        ) from exc
    except DirectiveDataUnavailable as exc:
        _record_source_audit(
            request,
            user,
            "delete",
            filename=filename,
            result="unavailable",
        )
        raise HTTPException(
            status_code=503,
            detail="Directive source deletion is temporarily unavailable",
        ) from exc
    _record_source_audit(
        request,
        user,
        "delete",
        filename=filename,
        result="success",
    )
    return {"deleted": filename}


async def _spool_source_upload(
    request: Request,
    destination: BinaryIO,
    max_bytes: int,
) -> int:
    raw_content_length = request.headers.get("content-length")
    if raw_content_length:
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise DirectiveSourceInvalid(
                "Upload Content-Length is invalid"
            ) from exc
        if content_length < 0:
            raise DirectiveSourceInvalid(
                "Upload Content-Length is invalid"
            )
        if content_length > max_bytes:
            raise DirectiveSourceTooLarge(
                f"Directive source exceeds {max_bytes} bytes"
            )

    total = 0
    signature = b""
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise DirectiveSourceTooLarge(
                f"Directive source exceeds {max_bytes} bytes"
            )
        if len(signature) < 4:
            signature = (signature + chunk)[:4]
        destination.write(chunk)
    if not signature.startswith(b"%PDF"):
        raise DirectiveSourceInvalid(
            "Directive source content is not a PDF"
        )
    destination.seek(0)
    return total


def _record_source_audit(
    request: Request,
    user: User,
    operation: str,
    *,
    filename: str | None = None,
    size_bytes: int | None = None,
    result: str,
) -> None:
    correlation_id = request.headers.get("X-Correlation-ID", "")
    if _CORRELATION_ID.fullmatch(correlation_id) is None:
        correlation_id = uuid4().hex
    attributes: dict[str, str | int] = {
        "audit.actor_id": user.user_id,
        "audit.operation": operation,
        "audit.result": result,
        "trace.correlation_id": correlation_id,
    }
    if filename is not None:
        try:
            attributes["audit.filename"] = (
                parse_directive_source_filename(filename).filename
            )
        except ValueError:
            attributes["audit.filename"] = "invalid"
    if size_bytes is not None:
        attributes["audit.byte_size"] = size_bytes
    with span("directive_source.audit", attributes):
        pass


DirectiveId = Annotated[str, Path(pattern=r"^\d{8}$")]
DirectiveVersionId = Annotated[
    str,
    Path(pattern=r"^\d{8}:v\d+(?:\.\d+)?$"),
]


@app.get(
    "/directives/{directive_id}/versions/{directive_version_id}/document",
    response_model=DirectiveDocumentResponse,
)
async def get_directive_document(
    directive_id: DirectiveId,
    directive_version_id: DirectiveVersionId,
    _user: User = Depends(get_current_user),
):
    try:
        document = await services.directive_documents.get_document(
            directive_id,
            directive_version_id,
        )
    except DirectiveDataUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Directive document is temporarily unavailable",
        ) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Directive version not found")
    return document


@app.get(
    "/directives/{directive_id}/versions/{directive_version_id}/source",
)
async def get_directive_source(
    directive_id: DirectiveId,
    directive_version_id: DirectiveVersionId,
    _user: User = Depends(get_current_user),
):
    try:
        source = await services.directive_documents.get_source(
            directive_id,
            directive_version_id,
        )
    except DirectiveDataUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Directive document is temporarily unavailable",
        ) from exc
    if source is None:
        raise HTTPException(status_code=404, detail="Directive version not found")

    encoded_filename = quote(source.source_filename, safe="")
    return StreamingResponse(
        source.chunks,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, max-age=3600, immutable",
            "Content-Disposition": (
                f'inline; filename="{directive_id}.pdf"; '
                f"filename*=UTF-8''{encoded_filename}"
            ),
            "ETag": f'"{source.source_hash}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


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


@app.post("/internal/agent-state/resolve")
async def resolve_hosted_agent_state(
    request: AgentStateRequest,
    caller: AgentCaller = Depends(get_agent_caller),
):
    return await resolve_agent_state(request, caller, history_store)


@app.post("/internal/agent-state/turn-complete")
async def complete_hosted_agent_state_turn(
    request: AgentStateTurnRequest,
    caller: AgentCaller = Depends(get_agent_caller),
):
    return await complete_agent_state_turn(request, caller, history_store)


@app.post("/internal/agent-state/turn-failed")
async def fail_hosted_agent_state_turn(
    request: AgentStateTurnRequest,
    caller: AgentCaller = Depends(get_agent_caller),
):
    return await fail_agent_state_turn(request, caller, history_store)


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
