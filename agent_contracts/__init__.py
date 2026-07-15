"""Shared contracts for the native Prompt Agent and Hosted MAF agent."""

from .models import (
    AgentType,
    Citation,
    CitationsEvent,
    NormalizedAgentEvent,
    RuntimeCompletedEvent,
    RuntimeDescriptor,
    RuntimeState,
    TextDeltaEvent,
    ToolEndedEvent,
    ToolResultEnvelope,
    ToolResultEvent,
    ToolStartedEvent,
    TurnContext,
    UsageEvent,
)
from .prompts import (
    FOUNDRY_PROMPT_VERSION,
    PROMPT_VERSION,
    render_foundry_prompt_instructions,
    render_instructions,
)
from .tools import COMMON_TOOL_DEFINITIONS, ToolDefinition, tool_definition

__all__ = [
    "AgentType",
    "Citation",
    "CitationsEvent",
    "COMMON_TOOL_DEFINITIONS",
    "FOUNDRY_PROMPT_VERSION",
    "NormalizedAgentEvent",
    "PROMPT_VERSION",
    "RuntimeCompletedEvent",
    "RuntimeDescriptor",
    "RuntimeState",
    "TextDeltaEvent",
    "ToolDefinition",
    "ToolEndedEvent",
    "ToolResultEnvelope",
    "ToolResultEvent",
    "ToolStartedEvent",
    "TurnContext",
    "UsageEvent",
    "render_foundry_prompt_instructions",
    "render_instructions",
    "tool_definition",
]
