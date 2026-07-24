"""Shared Responses stream parsing for Foundry runtime adapters."""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Collection
from typing import Any

from agent_contracts import (
    Citation,
    CitationsEvent,
    MandatoryStatus,
    NormalizedAgentEvent,
    RuntimeCompletedEvent,
    TextDeltaEvent,
    ToolEndedEvent,
    ToolResultEnvelope,
    ToolResultEvent,
    ToolStartedEvent,
    UsageEvent,
)


def _model_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _response_citations(response: Any) -> tuple[Citation, ...]:
    citations: list[Citation] = []
    for output in _model_value(response, "output", []) or []:
        if _model_value(output, "type") != "message":
            continue
        for content in _model_value(output, "content", []) or []:
            for annotation in _model_value(content, "annotations", []) or []:
                data = (
                    annotation.model_dump()
                    if hasattr(annotation, "model_dump")
                    else dict(annotation)
                    if isinstance(annotation, dict)
                    else {}
                )
                ref_id = (
                    data.get("file_id")
                    or data.get("url")
                    or data.get("container_id")
                    or f"citation-{len(citations) + 1}"
                )
                source_name = (
                    data.get("title")
                    or data.get("filename")
                    or data.get("text")
                    or "Foundry IQ"
                )
                citations.append(
                    Citation(
                        ref_id=str(ref_id),
                        source_name=str(source_name),
                        search_idx=len(citations),
                        url=str(data["url"]) if data.get("url") else None,
                    )
                )
    return tuple(citations)


def _payload_citations(payload: Any) -> tuple[Citation, ...]:
    citation_values = _model_value(payload, "citations")
    if not citation_values:
        citation_values = _model_value(_model_value(payload, "data", {}), "citations")
    if not isinstance(citation_values, (list, tuple)):
        return ()

    citations: list[Citation] = []
    for value in citation_values:
        ref_id = _model_value(value, "ref_id")
        source_name = _model_value(value, "source_name")
        if not ref_id or not source_name:
            continue
        search_idx = _model_value(value, "search_idx")
        try:
            parsed_search_idx = int(search_idx) if search_idx is not None else None
        except (TypeError, ValueError):
            parsed_search_idx = None
        url = _model_value(value, "url")
        mandatory_status = _model_value(value, "mandatory_status")
        try:
            parsed_mandatory_status = (
                MandatoryStatus(mandatory_status)
                if isinstance(mandatory_status, str)
                else None
            )
        except ValueError:
            parsed_mandatory_status = None
        directive_id = _optional_string(value, "directive_id")
        if directive_id and parsed_mandatory_status is None:
            parsed_mandatory_status = MandatoryStatus.UNKNOWN
        page_from = _model_value(value, "page_from")
        page_to = _model_value(value, "page_to")
        coverage = _model_value(value, "coverage")
        citations.append(
            Citation(
                ref_id=str(ref_id),
                source_name=str(source_name),
                search_idx=parsed_search_idx,
                url=str(url) if url else None,
                directive_id=directive_id,
                directive_version_id=_optional_string(
                    value,
                    "directive_version_id",
                ),
                version_label=_optional_string(value, "version_label"),
                section_id=_optional_string(value, "section_id"),
                section_number=_optional_string(value, "section_number"),
                section_title=_optional_string(value, "section_title"),
                page_from=(
                    page_from
                    if isinstance(page_from, int)
                    and not isinstance(page_from, bool)
                    and page_from >= 0
                    else None
                ),
                page_to=(
                    page_to
                    if isinstance(page_to, int)
                    and not isinstance(page_to, bool)
                    and page_to >= 0
                    else None
                ),
                effective_from=_optional_string(value, "effective_from"),
                mandatory_status=parsed_mandatory_status,
                mandate_snapshot_id=_optional_string(
                    value,
                    "mandate_snapshot_id",
                ),
                retrieval_strategy=_optional_string(
                    value,
                    "retrieval_strategy",
                ),
                coverage=coverage if isinstance(coverage, dict) else None,
            )
        )
    return tuple(citations)


def _optional_string(value: Any, name: str) -> str | None:
    candidate = _model_value(value, name)
    return candidate if isinstance(candidate, str) else None


def _usage_event(response: Any) -> UsageEvent | None:
    usage = _model_value(response, "usage")
    if not usage:
        return None
    input_details = _model_value(usage, "input_tokens_details")
    return UsageEvent(
        input_tokens=int(_model_value(usage, "input_tokens", 0) or 0),
        output_tokens=int(_model_value(usage, "output_tokens", 0) or 0),
        cached_tokens=int(_model_value(input_details, "cached_tokens", 0) or 0),
    )


async def stream_response(
    openai_client: Any,
    *,
    input_value: Any,
    conversation_id: str,
    extra_headers: dict[str, str] | None = None,
    extra_body: dict[str, Any] | None = None,
    timeout: float,
    emit_tool_lifecycle: bool = False,
) -> AsyncIterator[NormalizedAgentEvent | tuple[str, Any]]:
    """Stream one Responses API round and expose its completed response privately."""
    stream = await openai_client.responses.create(
        input=input_value,
        conversation=conversation_id,
        stream=True,
        extra_headers=extra_headers,
        extra_body=extra_body,
        timeout=timeout,
    )
    completed_response = None
    live_tool_calls: set[str] = set()
    try:
        async for event in stream:
            event_type = _model_value(event, "type", "")
            if event_type == "response.output_text.delta":
                delta = _model_value(event, "delta", "")
                if delta:
                    yield TextDeltaEvent(
                        message_id=str(
                            _model_value(event, "item_id", "assistant")
                        ),
                        delta=str(delta),
                    )
            elif emit_tool_lifecycle:
                tool_event = _live_tool_started_event(event, event_type)
                if tool_event and tool_event.call_id not in live_tool_calls:
                    live_tool_calls.add(tool_event.call_id)
                    yield tool_event
            if event_type == "response.completed":
                completed_response = _model_value(event, "response")
            elif event_type == "response.failed":
                error = _model_value(_model_value(event, "response"), "error")
                code = _model_value(
                    error,
                    "code",
                    "FOUNDRY_RESPONSE_FAILED",
                )
                raise RuntimeError(str(code))
    finally:
        await _close_stream(stream)

    if completed_response is None:
        raise RuntimeError("FOUNDRY_STREAM_INCOMPLETE")
    yield ("completed_response", completed_response)


def completed_events(response: Any) -> list[NormalizedAgentEvent]:
    events: list[NormalizedAgentEvent] = []
    citations = _response_citations(response)
    if citations:
        events.append(CitationsEvent(citations=citations))
    usage = _usage_event(response)
    if usage:
        events.append(usage)
    events.append(RuntimeCompletedEvent(response_id=_model_value(response, "id")))
    return events


def server_tool_events(
    response: Any,
    *,
    include_function_calls: bool = False,
    started_call_ids: Collection[str] = (),
) -> list[NormalizedAgentEvent]:
    events: list[NormalizedAgentEvent] = []
    output_items = _model_value(response, "output", []) or []
    function_outputs = {
        str(_model_value(item, "call_id")): _model_value(item, "output")
        for item in output_items
        if _model_value(item, "type") == "function_call_output"
        and _model_value(item, "call_id")
    }
    for item in output_items:
        item_type = _model_value(item, "type")
        supported_types = {"mcp_call", "file_search_call"}
        if include_function_calls:
            supported_types.add("function_call")
        if item_type not in supported_types:
            continue
        call_id = str(
            _model_value(item, "call_id")
            or _model_value(item, "id")
            or "server-tool"
        )
        tool_name = str(_model_value(item, "name") or "knowledge_base_retrieve")
        if call_id not in started_call_ids:
            events.append(
                ToolStartedEvent(call_id=call_id, tool_name=tool_name)
            )
        output = _model_value(item, "output")
        if item_type == "function_call" and output is None:
            output = function_outputs.get(call_id)
        if item_type == "function_call" and output is None:
            events.append(ToolEndedEvent(call_id=call_id))
            continue
        payload = _tool_payload(output)
        data = (
            payload["data"]
            if isinstance(payload.get("data"), dict)
            else payload
        )
        events.extend(
            (
                ToolResultEvent(
                    call_id=call_id,
                    message_id=f"{call_id}-result",
                    result=ToolResultEnvelope(
                        status=str(payload.get("status") or "ok"),
                        data=data,
                        citations=_payload_citations(payload),
                    ),
                ),
                ToolEndedEvent(call_id=call_id),
            )
        )
    return events


def _live_tool_started_event(
    event: Any,
    event_type: str,
) -> ToolStartedEvent | None:
    if event_type not in {
        "response.output_item.added",
        "response.mcp_call.in_progress",
        "response.file_search_call.in_progress",
    }:
        return None
    item = _model_value(event, "item", event)
    item_type = _model_value(item, "type")
    if item_type not in {"function_call", "mcp_call", "file_search_call"}:
        return None
    call_id = str(
        _model_value(item, "call_id")
        or _model_value(item, "id")
        or _model_value(event, "item_id")
        or "server-tool"
    )
    tool_name = str(
        _model_value(item, "name")
        or _model_value(event, "name")
        or "knowledge_base_retrieve"
    )
    return ToolStartedEvent(call_id=call_id, tool_name=tool_name)


def _tool_payload(output: Any) -> dict[str, Any]:
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
            return parsed if isinstance(parsed, dict) else {"content": output}
        except json.JSONDecodeError:
            return {"content": output}
    if isinstance(output, dict):
        return output
    return {"message": "Foundry tool execution completed"}


async def _close_stream(stream: Any) -> None:
    close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result
