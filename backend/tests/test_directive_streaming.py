from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_contracts import (
    AgentType,
    Citation,
    CitationsEvent,
    MandatoryStatus,
    RuntimeDescriptor,
    RuntimeState,
    TextDeltaEvent,
    ToolResultEvent,
    ToolStartedEvent,
    TurnContext,
    WorkflowHeartbeatEvent,
    WorkflowProgressEvent,
    WorkflowStage,
    WorkflowStatus,
)
from agent_memory_backend.chat_service import ChatTurnService
from agent_memory_backend.conversation_coordinator import PreparedConversation
from agent_memory_backend.conversation_registry import (
    ConversationRegistry,
    LiveConversation,
)
from agent_memory_backend.foundry_hosted_maf_runtime import (
    FoundryHostedMafRuntime,
)
from agent_memory_backend.foundry_runtime_base import stream_response
from agent_memory_backend.turn_accumulator import TurnAccumulator

_PROJECT_ENDPOINT = (
    "https://example.services.ai.azure.com/api/projects/directive-test"
)


def _state(agent_type: AgentType) -> RuntimeState:
    return RuntimeState(
        descriptor=RuntimeDescriptor(
            agent_type=agent_type,
            physical_agent_name=f"{agent_type.value}-hosted",
            release_id="release",
            prompt_version="prompt",
        ),
        foundry_conversation_id="foundry-conversation",
        hosted_session_id="hosted-session",
    )


def _runtime(agent_type: AgentType) -> FoundryHostedMafRuntime:
    name = f"{agent_type.value}-hosted"
    runtime = FoundryHostedMafRuntime(
        agent_type=agent_type,
        project_endpoint=_PROJECT_ENDPOINT,
        physical_agent_name=name,
        physical_agent_endpoint=(
            f"{_PROJECT_ENDPOINT}/agents/{name}/endpoint/protocols/openai"
        ),
        release_id="release",
        prompt_version="prompt",
        request_timeout_seconds=30,
        progress_heartbeat_seconds=0.005,
    )
    runtime._openai = object()
    return runtime


class DirectiveRuntimeStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_heartbeat_and_mandate_join_are_directive_only(
        self,
    ) -> None:
        content_citation = {
            "ref_id": "DIR-1:v2:s1",
            "source_name": "Travel directive",
            "directive_id": "DIR-1",
            "directive_version_id": "DIR-1:v2",
            "section_id": "s1",
            "page_from": 4,
        }
        response = SimpleNamespace(
            id="response-1",
            model_extra={},
            usage=None,
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="content-call",
                    name="get_directive_content",
                    output=json.dumps(
                        {
                            "status": "ok",
                            "data": {
                                "coverage": {
                                    "returned_section_count": 1,
                                    "selected_section_count": 1,
                                }
                            },
                            "citations": [content_citation],
                        }
                    ),
                ),
                SimpleNamespace(
                    type="function_call",
                    call_id="mandate-call",
                    name="get_user_directive_mandates",
                    output=json.dumps(
                        {
                            "status": "ok",
                            "data": {
                                "snapshot_id": "snapshot-1",
                                "statuses": {"DIR-1": "mandatory"},
                            },
                            "citations": [],
                        }
                    ),
                ),
            ],
        )

        async def fake_stream(*args, **kwargs):
            self.assertTrue(kwargs["emit_tool_lifecycle"])
            yield ToolStartedEvent(
                call_id="content-call",
                tool_name="get_directive_content",
            )
            await asyncio.sleep(0.02)
            yield TextDeltaEvent("message-1", "Answer")
            yield ("completed_response", response)

        runtime = _runtime(AgentType.DIRECTIVE_RAG)
        context = TurnContext(
            "application-1",
            "tenant:user",
            _state(AgentType.DIRECTIVE_RAG),
        )
        with patch(
            "agent_memory_backend.foundry_hosted_maf_runtime.stream_response",
            side_effect=fake_stream,
        ):
            events = [
                event
                async for event in runtime.stream_turn("Summarize", context)
            ]

        progress = [
            event
            for event in events
            if isinstance(event, WorkflowProgressEvent)
        ]
        self.assertEqual(progress[0].status, WorkflowStatus.STARTED)
        self.assertTrue(
            any(
                event.stage is WorkflowStage.LOADING_CONTENT
                for event in progress
            )
        )
        self.assertTrue(
            any(
                event.stage is WorkflowStage.PREPARING_ANSWER
                and event.status is WorkflowStatus.COMPLETED
                for event in progress
            )
        )
        self.assertTrue(
            any(
                isinstance(event, WorkflowHeartbeatEvent)
                for event in events
            )
        )
        final_citations = [
            event
            for event in events
            if isinstance(event, CitationsEvent)
        ][-1].citations
        self.assertEqual(
            final_citations[0].mandatory_status,
            MandatoryStatus.MANDATORY,
        )
        self.assertEqual(
            final_citations[0].mandate_snapshot_id,
            "snapshot-1",
        )
        content_result = next(
            event
            for event in events
            if isinstance(event, ToolResultEvent)
            and event.call_id == "content-call"
        )
        self.assertEqual(
            content_result.result.citations[0].mandatory_status,
            MandatoryStatus.MANDATORY,
        )

    async def test_support_hosted_stream_has_no_directive_progress(self) -> None:
        response = SimpleNamespace(
            id="response-1",
            model_extra={},
            output=[],
            usage=None,
        )

        async def fake_stream(*args, **kwargs):
            self.assertFalse(kwargs["emit_tool_lifecycle"])
            yield TextDeltaEvent("message-1", "Support answer")
            yield ("completed_response", response)

        runtime = _runtime(AgentType.AGENT_FRAMEWORK)
        context = TurnContext(
            "application-1",
            "tenant:user",
            _state(AgentType.AGENT_FRAMEWORK),
        )
        with patch(
            "agent_memory_backend.foundry_hosted_maf_runtime.stream_response",
            side_effect=fake_stream,
        ):
            events = [
                event
                async for event in runtime.stream_turn("Support", context)
            ]
        self.assertFalse(
            any(
                isinstance(
                    event,
                    (WorkflowProgressEvent, WorkflowHeartbeatEvent),
                )
                for event in events
            )
        )

    def test_directive_sections_are_not_collapsed_in_persistence(self) -> None:
        turn = TurnAccumulator("Compare sections")
        citations = tuple(
            Citation(
                ref_id="DIR-1:v2",
                source_name="Travel directive",
                directive_id="DIR-1",
                directive_version_id="DIR-1:v2",
                section_id=section_id,
                page_from=page,
                mandatory_status=MandatoryStatus.UNKNOWN,
            )
            for section_id, page in (("s1", 1), ("s2", 2))
        )
        turn.consume(CitationsEvent(citations))
        self.assertEqual(len(turn.assistant_citations), 2)


class StreamCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_stream_is_closed_when_consumer_is_cancelled(self) -> None:
        started = asyncio.Event()
        closed = asyncio.Event()

        class BlockingStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                started.set()
                await asyncio.Event().wait()
                raise StopAsyncIteration

            async def close(self):
                closed.set()

        client = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(return_value=BlockingStream())
            )
        )
        events = stream_response(
            client,
            input_value="question",
            conversation_id="conversation",
            timeout=30,
        )
        task = asyncio.create_task(anext(events))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())

    async def test_cancelled_chat_is_not_persisted_and_releases_lease(
        self,
    ) -> None:
        state = _state(AgentType.DIRECTIVE_RAG)
        conversation = LiveConversation(
            "conversation-1",
            user_id="tenant:user",
            title="Long summary",
            agent_type=AgentType.DIRECTIVE_RAG,
            runtime_state=state,
        )
        runtime_started = asyncio.Event()

        class Runtime:
            async def stream_turn(self, message: str, context: TurnContext):
                yield TextDeltaEvent("message-1", "Partial")
                runtime_started.set()
                await asyncio.Event().wait()

        coordinator = SimpleNamespace(
            prepare=AsyncMock(
                return_value=PreparedConversation(conversation, Runtime())
            )
        )
        registry = ConversationRegistry()
        history = SimpleNamespace(append_messages=AsyncMock())
        service = ChatTurnService(coordinator, registry, history)
        response = await service.create_response(
            message="Summarize",
            conversation_id="conversation-1",
            agent_type=AgentType.DIRECTIVE_RAG,
            user_id="tenant:user",
        )

        async def consume() -> None:
            async for _ in response.body_iterator:
                pass

        task = asyncio.create_task(consume())
        await runtime_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        history.append_messages.assert_not_awaited()
        next_lease = await registry.acquire("conversation-1")
        await next_lease.release()


if __name__ == "__main__":
    unittest.main()
