"""Strict shared function-tool definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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

_BY_NAME = {definition.name: definition for definition in COMMON_TOOL_DEFINITIONS}


def tool_definition(name: str) -> ToolDefinition:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"unknown tool: {name}") from exc
