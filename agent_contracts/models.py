"""Runtime-neutral data models shared by both Foundry agent adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, TypeAlias


class AgentType(str, Enum):
    FOUNDRY_PROMPT = "foundry-prompt"
    AGENT_FRAMEWORK = "agent-framework"


@dataclass(frozen=True)
class Citation:
    ref_id: str
    source_name: str
    search_idx: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class ToolResultEnvelope:
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    citations: tuple[Citation, ...] = ()
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "data": self.data,
            "citations": [asdict(citation) for citation in self.citations],
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


@dataclass(frozen=True)
class RuntimeDescriptor:
    agent_type: AgentType
    physical_agent_name: str
    release_id: str
    prompt_version: str
    observed_agent_version: str | None = None

@dataclass
class RuntimeState:
    descriptor: RuntimeDescriptor
    foundry_conversation_id: str | None = None
    hosted_session_id: str | None = None
    last_response_id: str | None = None
    schema_version: int = 3

@dataclass(frozen=True)
class TurnContext:
    application_conversation_id: str
    authenticated_user_id: str
    runtime_state: RuntimeState


@dataclass(frozen=True)
class TextDeltaEvent:
    message_id: str
    delta: str


@dataclass(frozen=True)
class ToolStartedEvent:
    call_id: str
    tool_name: str


@dataclass(frozen=True)
class ToolResultEvent:
    call_id: str
    message_id: str
    result: ToolResultEnvelope


@dataclass(frozen=True)
class ToolEndedEvent:
    call_id: str


@dataclass(frozen=True)
class CitationsEvent:
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class UsageEvent:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True)
class RuntimeCompletedEvent:
    response_id: str | None = None


NormalizedAgentEvent: TypeAlias = (
    TextDeltaEvent
    | ToolStartedEvent
    | ToolResultEvent
    | ToolEndedEvent
    | CitationsEvent
    | UsageEvent
    | RuntimeCompletedEvent
)
