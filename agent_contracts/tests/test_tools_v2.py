import pytest
from pydantic import ValidationError

from agent_contracts.tools import (
    DirectiveContentArguments,
    GetDirectiveArguments,
    SearchDirectivesArguments,
    UserDirectiveMandatesArguments,
)


def test_tools_normalize_ids_for_current_only_contracts() -> None:
    arguments = DirectiveContentArguments(directive_id=" č / 12 ")
    assert arguments.directive_id == "Č/12"

    search = SearchDirectivesArguments(intents=["find the title"], directive_ids=[" č / 12 "])
    assert search.directive_ids == ["Č/12"]

    directive = GetDirectiveArguments(directive_id=" č / 12 ")
    assert directive.directive_id == "Č/12"
    assert directive.view == "metadata"


def test_tools_reject_removed_historical_selectors() -> None:
    with pytest.raises(ValidationError):
        GetDirectiveArguments(
            directive_id="Č/12",
            directive_version_id="Č/12:v1",
        )
    with pytest.raises(ValidationError):
        SearchDirectivesArguments(
            intents=["find"],
            directive_ids=["Č/12"],
            directive_version_id="Č/12:v1",
        )
    with pytest.raises(ValidationError):
        DirectiveContentArguments(
            directive_id="Č/12",
            directive_version_id="Č/12:v1",
        )


def test_tool_limits_and_current_scope_are_preserved() -> None:
    with pytest.raises(ValidationError):
        SearchDirectivesArguments(
            intents=["find"],
            section_ids=["s1"],
        )
    with pytest.raises(ValidationError):
        SearchDirectivesArguments(
            intents=["find"],
            directive_ids=["Č/12", "Č/13"],
            section_ids=["s1"],
        )

    mandates = UserDirectiveMandatesArguments(directive_ids=["č/12"])
    assert mandates.directive_ids == ["Č/12"]


@pytest.mark.parametrize("value", ["Č/12", {"id": "Č/12"}, ("Č/12",)])
def test_directive_id_arrays_reject_non_json_arrays(value: object) -> None:
    with pytest.raises(ValidationError, match="JSON array"):
        SearchDirectivesArguments(intents=["find"], directive_ids=value)
    with pytest.raises(ValidationError, match="JSON array"):
        UserDirectiveMandatesArguments(directive_ids=value)


@pytest.mark.parametrize("value", ["s1", {"id": "s1"}, ("s1",)])
def test_section_id_arrays_reject_non_json_arrays(value: object) -> None:
    with pytest.raises(ValidationError, match="JSON array"):
        SearchDirectivesArguments(
            intents=["find"],
            directive_ids=["Č/12"],
            section_ids=value,
        )
    with pytest.raises(ValidationError, match="JSON array"):
        DirectiveContentArguments(directive_id="Č/12", section_ids=value)
