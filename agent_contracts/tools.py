"""Strict shared function-tool definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from directive_contracts import (
    normalize_directive_id,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserContextArguments(StrictArguments):
    pass


class OrderStatusArguments(StrictArguments):
    order_id: str = Field(description="Order ID such as ORD-001", min_length=1)


class MemorySearchArguments(StrictArguments):
    query: str = Field(description="What the user explicitly asked to recall", min_length=1)


class ProfileUpdateArguments(StrictArguments):
    basic_info: dict[str, Any] | None = None
    interests: list[str] | None = None
    habits: list[str] | None = None
    preferences: dict[str, Any] | None = None
    status: dict[str, Any] | None = None
    facts: list[str] | None = None


class GetDirectiveArguments(StrictArguments):
    directive_id: str = Field(min_length=1)
    view: Literal["metadata", "manifest", "summary"] = "metadata"

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_directive_id(cls, value: str) -> str:
        return normalize_directive_id(value)


class SearchDirectivesArguments(StrictArguments):
    intents: list[str] = Field(min_length=1, max_length=8)
    directive_ids: list[str] = Field(default_factory=list, max_length=100)
    section_ids: list[str] = Field(default_factory=list, max_length=100)
    max_results: int = Field(default=10, ge=1, le=100)

    @field_validator("directive_ids", mode="before")
    @classmethod
    def normalize_directive_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("directive_ids must be a JSON array")
        return [normalize_directive_id(item) for item in value]

    @field_validator("section_ids", mode="before")
    @classmethod
    def validate_section_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("section_ids must be a JSON array")
        return [str(item) for item in value]

    @model_validator(mode="after")
    def validate_filters(self) -> SearchDirectivesArguments:
        if any(
            len(intent.strip()) == 0 or len(intent) > 500
            for intent in self.intents
        ):
            raise ValueError("intents must contain 1..500 non-whitespace characters")
        if self.section_ids and len(self.directive_ids) != 1:
            raise ValueError(
                "section filtering requires exactly one directive_id"
            )
        return self


class DirectiveContentArguments(StrictArguments):
    directive_id: str = Field(min_length=1)
    section_ids: list[str] = Field(default_factory=list, max_length=100)
    cursor: int = Field(default=0, ge=0)
    max_tokens: int | None = Field(default=None, ge=1, le=900_000)

    @field_validator("directive_id", mode="before")
    @classmethod
    def normalize_directive_id(cls, value: str) -> str:
        return normalize_directive_id(value)

    @field_validator("section_ids", mode="before")
    @classmethod
    def validate_section_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("section_ids must be a JSON array")
        return [str(item) for item in value]


class UserDirectiveMandatesArguments(StrictArguments):
    directive_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("directive_ids", mode="before")
    @classmethod
    def normalize_directive_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("directive_ids must be a JSON array")
        return [normalize_directive_id(item) for item in value]

    @model_validator(mode="after")
    def validate_directive_ids(self) -> UserDirectiveMandatesArguments:
        return self


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    arguments_model: type[StrictArguments]

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.arguments_model.model_validate(arguments).model_dump(exclude_none=True)


COMMON_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="get_user_context",
        arguments_model=UserContextArguments,
    ),
    ToolDefinition(
        name="get_order_status",
        arguments_model=OrderStatusArguments,
    ),
    ToolDefinition(
        name="check_memory",
        arguments_model=MemorySearchArguments,
    ),
    ToolDefinition(
        name="update_user_profile",
        arguments_model=ProfileUpdateArguments,
    ),
)

DIRECTIVE_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition("get_directive", GetDirectiveArguments),
    ToolDefinition("search_directives", SearchDirectivesArguments),
    ToolDefinition("get_directive_content", DirectiveContentArguments),
    ToolDefinition(
        "get_user_directive_mandates",
        UserDirectiveMandatesArguments,
    ),
)

_BY_NAME = {definition.name: definition for definition in COMMON_TOOL_DEFINITIONS}
_DIRECTIVE_BY_NAME = {
    definition.name: definition for definition in DIRECTIVE_TOOL_DEFINITIONS
}


def tool_definition(name: str) -> ToolDefinition:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown tool: {name}") from exc


def directive_tool_definition(name: str) -> ToolDefinition:
    try:
        return _DIRECTIVE_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown directive tool: {name}") from exc
