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
    ToolResultEnvelope,
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
    _enrich_directive_tool_events,
    _select_final_directive_citations,
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
        ][-1]
        self.assertTrue(final_citations.authoritative)
        final_citations = final_citations.citations
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

    def test_exact_ref_ids_take_precedence_in_reply_order(self) -> None:
        first = Citation(
            ref_id="first-section",
            source_name="First directive",
            directive_id="DIR-1",
        )
        second = Citation(
            ref_id="second-section",
            source_name="Second directive",
            directive_id="DIR-2",
        )

        selected = _select_final_directive_citations(
            [first, second],
            assistant_text=(
                "Second claim [second-section], then first [first-section]."
            ),
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected, (second, first))

    def test_mandate_lookup_selects_complex_directive_id(self) -> None:
        selected_section = Citation(
            ref_id="opaque-section-id",
            source_name="Selected directive",
            directive_id="AA/215/AF-DF/0277.1",
        )
        unrelated = Citation(
            ref_id="unrelated-section-id",
            source_name="Unrelated directive",
            directive_id="MP/25/0277",
        )

        selected = _select_final_directive_citations(
            [unrelated, selected_section],
            assistant_text="Answer without a machine-readable marker.",
            statuses={
                "AA/215/AF-DF/0277.1": MandatoryStatus.NON_MANDATORY,
            },
        )

        self.assertEqual(selected, (selected_section,))

    def test_mandate_fallback_selects_section_title_used_in_reply(self) -> None:
        section_three = Citation(
            ref_id="section-3",
            source_name="Microsoft 365 directive",
            directive_id="MP/23/0141",
            directive_version_id="MP/23/0141:v1",
            section_id="s3",
            section_number="3",
            section_title="UŽIVATELSKÝ NÁVOD PRO POUŽÍVÁNÍ APLIKACÍ M365",
        )
        section_eight = Citation(
            ref_id="section-8",
            source_name="Microsoft 365 directive",
            directive_id="MP/23/0141",
            directive_version_id="MP/23/0141:v1",
            section_id="s8",
            section_number="8",
            section_title="POŽADAVKY NA ZMĚNY",
        )

        selected = _select_final_directive_citations(
            [section_three, section_eight],
            assistant_text=(
                "Zdroj: MP/23/0141, čl. 8 – Požadavky na změny."
            ),
            statuses={"MP/23/0141": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(selected, (section_eight,))

    def test_ambiguous_mandate_fallback_emits_document_only(self) -> None:
        citations = [
            Citation(
                ref_id=f"section-{number}",
                source_name="Microsoft 365 directive",
                directive_id="MP/23/0141",
                directive_version_id="MP/23/0141:v1",
                version_label="1.0",
                section_id=f"s{number}",
                section_number=str(number),
                section_title=title,
                page_from=number,
            )
            for number, title in (
                (3, "UŽIVATELSKÝ NÁVOD"),
                (8, "POŽADAVKY NA ZMĚNY"),
            )
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text="Answer without identifiable section evidence.",
            statuses={"MP/23/0141": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[0].ref_id, "MP/23/0141:v1")
        self.assertIsNone(selected[0].section_id)
        self.assertIsNone(selected[0].page_from)

    def test_one_word_section_title_requires_its_section_number(self) -> None:
        scope = Citation(
            ref_id="section-2",
            source_name="Policy",
            directive_id="DIR-1",
            directive_version_id="DIR-1:v1",
            section_id="s2",
            section_number="2",
            section_title="Scope",
        )
        eligibility = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            directive_version_id="DIR-1:v1",
            section_id="s3",
            section_number="3",
            section_title="Eligibility",
        )

        selected = _select_final_directive_citations(
            [scope, eligibility],
            assistant_text="Source: DIR-1, section 2 – Scope.",
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected, (scope,))

    def test_source_line_matches_section_number_and_document_identity(
        self,
    ) -> None:
        source_name = (
            "Pravidla používání prostředí Microsoft 365 "
            "Metodický pokyn číslo: MP/23/0141"
        )
        section_three = Citation(
            ref_id="section-3",
            source_name=source_name,
            directive_id="30336958",
            directive_version_id="30336958:v1",
            section_id="s3",
            section_number="3",
            section_title="UŽIVATELSKÝ NÁVOD PRO POUŽÍVÁNÍ APLIKACÍ M365",
        )
        section_eight = Citation(
            ref_id="section-8",
            source_name=source_name,
            directive_id="30336958",
            directive_version_id="30336958:v1",
            section_id="s8",
            section_number="8",
            section_title="POŽADAVKY NA ZMĚNY",
        )

        selected = _select_final_directive_citations(
            [section_three, section_eight],
            assistant_text=(
                "O změnu požádejte přes ServiceDesk.\n\n"
                "Zdroj: MP/23/0141, Pravidla používání prostředí "
                "Microsoft 365, čl. 8."
            ),
            statuses={"30336958": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(selected, (section_eight,))

    def test_section_number_rejects_overlapping_directive_id(self) -> None:
        citations = [
            Citation(
                ref_id=f"section-{number}",
                source_name="Unmatched policy identity",
                directive_id="30336958",
                directive_version_id="30336958:v1",
                section_id=f"s{number}",
                section_number=str(number),
                section_title=title,
            )
            for number, title in (
                (3, "USER GUIDE"),
                (8, "CHANGE REQUESTS"),
            )
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text="Source: 1303369580, article 8.",
            statuses={"30336958": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_only_last_mandate_lookup_selects_final_directives(self) -> None:
        first = Citation(
            ref_id="first-section",
            source_name="First directive",
            directive_id="DIR-1",
        )
        second = Citation(
            ref_id="second-section",
            source_name="Second directive",
            directive_id="DIR-2",
        )
        events = [
            ToolResultEvent(
                "search-call",
                "search-result",
                ToolResultEnvelope(
                    status="ok",
                    data={},
                    citations=(first, second),
                ),
            ),
            ToolResultEvent(
                "mandate-call-1",
                "mandate-result-1",
                ToolResultEnvelope(
                    status="ok",
                    data={
                        "snapshot_id": "snapshot-1",
                        "statuses": {"DIR-1": "mandatory"},
                    },
                ),
            ),
            ToolResultEvent(
                "mandate-call-2",
                "mandate-result-2",
                ToolResultEnvelope(
                    status="ok",
                    data={
                        "snapshot_id": "snapshot-2",
                        "statuses": {"DIR-2": "non_mandatory"},
                    },
                ),
            ),
        ]

        enriched, selected, _ = _enrich_directive_tool_events(
            events,
            {
                "search-call": "search_directives",
                "mandate-call-1": "get_user_directive_mandates",
                "mandate-call-2": "get_user_directive_mandates",
            },
            "Marker-free answer.",
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].ref_id, "second-section")
        self.assertEqual(
            selected[0].mandatory_status,
            MandatoryStatus.NON_MANDATORY,
        )
        self.assertEqual(selected[0].mandate_snapshot_id, "snapshot-2")
        search_result = enriched[0]
        self.assertIsInstance(search_result, ToolResultEvent)
        self.assertEqual(
            search_result.result.citations[0].mandatory_status,
            MandatoryStatus.UNKNOWN,
        )

    def test_exact_ref_marker_does_not_match_ref_prefix(self) -> None:
        parent = Citation(
            ref_id="DIR-1:v2",
            source_name="Directive metadata",
            directive_id="DIR-1",
        )
        section = Citation(
            ref_id="DIR-1:v2:s1",
            source_name="Directive section",
            directive_id="DIR-1",
        )

        selected = _select_final_directive_citations(
            [parent, section],
            assistant_text="Grounded claim [DIR-1:v2:s1].",
            statuses={},
        )

        self.assertEqual(selected, (section,))

    def test_bare_corner_marker_selects_exact_ref_id(self) -> None:
        citation = Citation(
            ref_id=(
                "012869405198d310ea60607d3454a4823eefdf02eec8c995"
                "750639d26fc8afd5"
            ),
            source_name="AI directive metadata",
            directive_id="MP/25/0277",
            section_id="s0000-metadata",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text=f"Metadata evidence. 【{citation.ref_id}】",
            statuses={},
        )

        self.assertEqual(selected, (citation,))

    def test_singleton_fallback_is_unambiguous(self) -> None:
        citation = Citation(
            ref_id="only-section",
            source_name="Only directive",
            directive_id="DIR-1",
        )

        self.assertEqual(
            _select_final_directive_citations(
                [citation],
                assistant_text="Marker-free answer.",
                statuses={},
            ),
            (citation,),
        )

    def test_ambiguous_marker_free_results_are_not_published(self) -> None:
        citations = [
            Citation(
                ref_id=f"section-{index}",
                source_name=f"Directive {index}",
                directive_id=f"DIR-{index}",
            )
            for index in (1, 2)
        ]

        self.assertEqual(
            _select_final_directive_citations(
                citations,
                assistant_text="Marker-free answer.",
                statuses={},
            ),
            (),
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

    def test_final_citations_replace_provisional_tool_citations(self) -> None:
        turn = TurnAccumulator("Question")
        provisional = (
            Citation(ref_id="used", source_name="Used"),
            Citation(ref_id="unused", source_name="Unused"),
        )
        turn.consume(
            ToolResultEvent(
                "call-1",
                "result-1",
                ToolResultEnvelope(
                    status="ok",
                    data={},
                    citations=provisional,
                ),
            )
        )

        turn.consume(CitationsEvent((provisional[0],), authoritative=True))
        self.assertEqual(
            [citation["ref_id"] for citation in turn.assistant_citations],
            ["used"],
        )

        turn.consume(CitationsEvent((), authoritative=True))
        self.assertEqual(turn.assistant_citations, [])


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
