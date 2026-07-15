"""Shared Responses stream parsing for Foundry runtime adapters."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from agent_contracts import (
    Citation,
    CitationsEvent,
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
        citations.append(
            Citation(
                ref_id=str(ref_id),
                source_name=str(source_name),
                search_idx=parsed_search_idx,
                url=str(url) if url else None,
            )
        )
    return tuple(citations)


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
    async for event in stream:
        event_type = _model_value(event, "type", "")
        if event_type == "response.output_text.delta":
            delta = _model_value(event, "delta", "")
            if delta:
                yield TextDeltaEvent(
                    message_id=str(_model_value(event, "item_id", "assistant")),
                    delta=str(delta),
                )
        elif event_type == "response.completed":
            completed_response = _model_value(event, "response")
        elif event_type == "response.failed":
            error = _model_value(_model_value(event, "response"), "error")
            code = _model_value(error, "code", "FOUNDRY_RESPONSE_FAILED")
            raise RuntimeError(str(code))

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
    response: Any, *, include_function_calls: bool = False
) -> list[NormalizedAgentEvent]:
    events: list[NormalizedAgentEvent] = []
    for item in _model_value(response, "output", []) or []:
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
        events.append(ToolStartedEvent(call_id=call_id, tool_name=tool_name))
        if item_type == "function_call":
            events.append(ToolEndedEvent(call_id=call_id))
            continue

        output = _model_value(item, "output")
        if isinstance(output, str):
            try:
                parsed = json.loads(output)
                payload = parsed if isinstance(parsed, dict) else {"content": output}
            except json.JSONDecodeError:
                payload = {"content": output}
        elif isinstance(output, dict):
            payload = output
        else:
            payload = {"message": "Foundry IQ retrieval completed"}
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
