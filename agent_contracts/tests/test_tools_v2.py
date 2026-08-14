import pytest
from pydantic import ValidationError

from agent_contracts.tools import (
    DirectiveContentArguments,
    ResolveDirectiveArguments,
    SearchDirectivesArguments,
    UserDirectiveMandatesArguments,
)


def test_tools_normalize_ids_and_validate_matching_version() -> None:
    arguments = DirectiveContentArguments(
        directive_id=" č / 12 ",
        directive_version_id="Č/12:v1",
    )
    assert arguments.directive_id == "Č/12"
    assert arguments.directive_version_id == "Č/12:v1"

    search = SearchDirectivesArguments(
        intents=["find the title"],
        directive_ids=[" č / 12 "],
        directive_version_id="Č/12:v1",
    )
    assert search.directive_ids == ["Č/12"]
    assert search.current_only is False


def test_tools_reject_mismatched_version_id() -> None:
    with pytest.raises(ValidationError, match="does not belong"):
        DirectiveContentArguments(
            directive_id="Č/12",
            directive_version_id="Č/13:v1",
        )

    with pytest.raises(ValidationError, match="does not belong"):
        ResolveDirectiveArguments(
            directive_id="Č/12",
            directive_version_id="Č/13:v1",
        )


def test_tool_limits_and_historical_safety_are_preserved() -> None:
    with pytest.raises(ValidationError):
        SearchDirectivesArguments(
            intents=["find"],
            current_only=False,
        )
    with pytest.raises(ValidationError):
        SearchDirectivesArguments(
            intents=["find"],
            directive_ids=["Č/12", "Č/13"],
            directive_version_id="Č/12:v1",
        )

    mandates = UserDirectiveMandatesArguments(directive_ids=["č/12"])
    assert mandates.directive_ids == ["Č/12"]
