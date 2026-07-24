"""Strict shared function-tool definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ResolveDirectiveArguments(StrictArguments):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    directive_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    directive_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    version_label: str | None = Field(default=None, min_length=1, max_length=100)
    as_of: date | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> ResolveDirectiveArguments:
        if not self.query and not self.directive_id:
            raise ValueError("query or directive_id is required")
        selectors = (
            self.directive_version_id,
            self.version_label,
            self.as_of,
        )
        if sum(value is not None for value in selectors) > 1:
            raise ValueError(
                "directive_version_id, version_label, and as_of are mutually exclusive"
            )
        if self.directive_version_id and not self.directive_id:
            raise ValueError("directive_id is required with directive_version_id")
        return self


class SearchDirectivesArguments(StrictArguments):
    intents: list[str] = Field(min_length=1, max_length=8)
    directive_ids: list[str] = Field(default_factory=list, max_length=100)
    directive_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    current_only: bool = True
    max_results: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_filters(self) -> SearchDirectivesArguments:
        if any(
            len(intent.strip()) == 0 or len(intent) > 500
            for intent in self.intents
        ):
            raise ValueError("intents must contain 1..500 non-whitespace characters")
        if any(
            len(value) != 8 or not value.isdigit()
            for value in self.directive_ids
        ):
            raise ValueError("directive_ids must contain eight-digit identifiers")
        if self.directive_version_id and len(self.directive_ids) != 1:
            raise ValueError(
                "exact version filtering requires exactly one directive_id"
            )
        if self.directive_version_id:
            self.current_only = False
        elif not self.current_only:
            raise ValueError(
                "historical search requires an exact directive_version_id"
            )
        return self


class DirectiveVersionArguments(StrictArguments):
    directive_id: str = Field(pattern=r"^\d{8}$")
    directive_version_id: str = Field(min_length=1, max_length=200)


class DirectiveContentArguments(DirectiveVersionArguments):
    section_ids: list[str] = Field(default_factory=list, max_length=100)
    cursor: int = Field(default=0, ge=0)
    max_tokens: int | None = Field(default=None, ge=1, le=900_000)


class SearchWithinDirectiveArguments(DirectiveVersionArguments):
    intents: list[str] = Field(min_length=1, max_length=8)
    section_ids: list[str] = Field(default_factory=list, max_length=100)
    max_results: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def validate_intents(self) -> SearchWithinDirectiveArguments:
        if any(
            len(intent.strip()) == 0 or len(intent) > 500
            for intent in self.intents
        ):
            raise ValueError("intents must contain 1..500 non-whitespace characters")
        return self


class RelatedDirectivesArguments(DirectiveVersionArguments):
    relation_types: list[
        Literal["parent", "sub_directive", "reference"]
    ] = Field(default_factory=list, max_length=3)
    depth: int = Field(default=1, ge=1, le=2)


class UserDirectiveMandatesArguments(StrictArguments):
    directive_ids: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_directive_ids(self) -> UserDirectiveMandatesArguments:
        if any(
            len(value) != 8 or not value.isdigit()
            for value in self.directive_ids
        ):
            raise ValueError("directive_ids must contain eight-digit identifiers")
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
    ToolDefinition("resolve_directive", ResolveDirectiveArguments),
    ToolDefinition("search_directives", SearchDirectivesArguments),
    ToolDefinition("get_directive_manifest", DirectiveVersionArguments),
    ToolDefinition("get_directive_content", DirectiveContentArguments),
    ToolDefinition("search_within_directive", SearchWithinDirectiveArguments),
    ToolDefinition("get_related_directives", RelatedDirectivesArguments),
    ToolDefinition("get_precomputed_summary", DirectiveVersionArguments),
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
