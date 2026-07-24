"""Runtime-neutral data models shared by Foundry agent adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeAlias


class AgentType(str, Enum):
    FOUNDRY_PROMPT = "foundry-prompt"
    AGENT_FRAMEWORK = "agent-framework"
    DIRECTIVE_RAG = "directive-rag"


class MandatoryStatus(str, Enum):
    MANDATORY = "mandatory"
    NON_MANDATORY = "non_mandatory"
    UNKNOWN = "unknown"


class WorkflowStage(str, Enum):
    RESOLVING = "resolving"
    SEARCHING = "searching"
    LOADING_CONTENT = "loading_content"
    FOLLOWING_REFERENCES = "following_references"
    COMPARING_VERSIONS = "comparing_versions"
    CHECKING_MANDATORY_STATUS = "checking_mandatory_status"
    VERIFYING_COVERAGE = "verifying_coverage"
    PREPARING_ANSWER = "preparing_answer"


class WorkflowStatus(str, Enum):
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Citation:
    ref_id: str
    source_name: str
    search_idx: int | None = None
    url: str | None = None
    directive_id: str | None = None
    directive_version_id: str | None = None
    version_label: str | None = None
    section_id: str | None = None
    section_number: str | None = None
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    effective_from: str | None = None
    mandatory_status: MandatoryStatus | None = None
    mandate_snapshot_id: str | None = None
    retrieval_strategy: str | None = None
    coverage: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ref_id": self.ref_id,
            "source_name": self.source_name,
            "search_idx": self.search_idx,
            "url": self.url,
        }
        for name in (
            "directive_id",
            "directive_version_id",
            "version_label",
            "section_id",
            "section_number",
            "section_title",
            "page_from",
            "page_to",
            "effective_from",
            "mandatory_status",
            "mandate_snapshot_id",
            "retrieval_strategy",
            "coverage",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value.value if isinstance(value, Enum) else value
        return payload


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
            "citations": [citation.to_dict() for citation in self.citations],
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
class WorkflowProgressEvent:
    stage: WorkflowStage
    status: WorkflowStatus
    message: str | None = None
    completed_count: int | None = None
    total_count: int | None = None


@dataclass(frozen=True)
class WorkflowHeartbeatEvent:
    stage: WorkflowStage
    message: str


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
    | WorkflowProgressEvent
    | WorkflowHeartbeatEvent
    | UsageEvent
    | RuntimeCompletedEvent
)
