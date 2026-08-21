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

    def test_exact_markers_are_rejected_when_section_labels_disagree(self) -> None:
        section_three_one = Citation(
            ref_id="section-3-1",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-1",
            section_number="3.1",
        )
        section_six_one = Citation(
            ref_id="section-6-1",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s6-1",
            section_number="6.1",
        )

        selected = _select_final_directive_citations(
            [section_three_one, section_six_one],
            assistant_text=(
                "Podle sekcí 3.2 a 6.2 platí omezení "
                "[section-3-1][section-6-1]. "
                "Výjimka se schvaluje podle sekce 8."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(len(selected), 2)
        self.assertTrue(
            all(citation.citation_scope == "document" for citation in selected)
        )

    def test_exact_markers_allow_matching_section_labels(self) -> None:
        section_three_two = Citation(
            ref_id="section-3-2",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-2",
            section_number="3.2",
        )
        section_six_two = Citation(
            ref_id="section-6-2",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s6-2",
            section_number="6.2",
        )
        section_eight = Citation(
            ref_id="section-8",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [section_three_two, section_six_two, section_eight],
            assistant_text=(
                "Podle sekcí 3.2 a 6.2 platí omezení "
                "[section-3-2][section-6-2]. "
                "Výjimka se schvaluje podle sekce 8 [section-8]."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(
            selected,
            (section_three_two, section_six_two, section_eight),
        )

    def test_wrong_title_matches_are_replaced_by_explicit_sections(self) -> None:
        citations = [
            Citation(
                ref_id=f"section-{number.replace('.', '-')}",
                source_name="Pravidla používání nástrojů umělé inteligence (AI)",
                directive_id="MP/25/0277",
                directive_version_id="MP/25/0277:v1.1",
                section_id=f"s{number.replace('.', '-')}",
                section_number=number,
                section_title=title,
                retrieval_strategy="focused",
            )
            for number, title in (
                ("3.2", "OSTATNÍ AI NÁSTROJE"),
                ("6.2", "OSTATNÍ AI NÁSTROJE"),
                ("3.1", "PODNIKOVÝ COPILOT"),
                ("6.1", "PODNIKOVÝ COPILOT"),
                ("8", "SCHVÁLENÍ OSTATNÍCH AI NÁSTROJŮ"),
            )
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text=(
                "Podle MP/25/0277, sekcí 3.2 a 6.2, lze do ChatGPT "
                "vkládat pouze veřejná data.\n\n"
                "Pro interní dokumenty je možné použít Podnikový Copilot. "
                "Jiný nástroj lze použít pouze po schválení dle sekce 8."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(
            [citation.section_number for citation in selected],
            ["3.2", "6.2", "8"],
        )

    def test_marker_free_czech_source_line_selects_all_listed_sections(
        self,
    ) -> None:
        section_three_two = Citation(
            ref_id="section-3-2",
            source_name="Pravidla používání nástrojů umělé inteligence (AI)",
            directive_id="MP/25/0277",
            directive_version_id="MP/25/0277:v1.1",
            section_id="s3-2",
            section_number="3.2",
        )
        section_six_two = Citation(
            ref_id="section-6-2",
            source_name="Pravidla používání nástrojů umělé inteligence (AI)",
            directive_id="MP/25/0277",
            directive_version_id="MP/25/0277:v1.1",
            section_id="s6-2",
            section_number="6.2",
        )

        selected = _select_final_directive_citations(
            [section_three_two, section_six_two],
            assistant_text=(
                "Zdroj: Pravidla používání nástrojů AI, "
                "MP/25/0277, sekce 3.2 a 6.2."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(selected, (section_three_two, section_six_two))

    def test_wrong_exact_marker_keeps_resolvable_document_fallback(self) -> None:
        section_three_one = Citation(
            ref_id="section-3-1",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-1",
            section_number="3.1",
        )
        section_three_two = Citation(
            ref_id="section-3-2",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-2",
            section_number="3.2",
        )

        selected = _select_final_directive_citations(
            [section_three_one, section_three_two],
            assistant_text=(
                "Podle MP/25/0277, sekce 3.2, platí omezení "
                "[section-3-1]."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].ref_id, section_three_one.ref_id)
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], section_three_two)

    def test_rejected_marker_falls_back_to_its_own_document(self) -> None:
        policy_a = Citation(
            ref_id="policy-a-section-1",
            source_name="Policy A",
            directive_id="POLICY-A",
            directive_version_id="POLICY-A:v1",
            section_id="a1",
            section_number="1",
        )
        policy_b = Citation(
            ref_id="policy-b-section-1",
            source_name="Policy B",
            directive_id="POLICY-B",
            directive_version_id="POLICY-B:v1",
            section_id="b1",
            section_number="1",
        )

        selected = _select_final_directive_citations(
            [policy_a, policy_b],
            assistant_text=(
                "Policy A section 1 applies [policy-a-section-1].\n\n"
                "Policy B requires section 2 [policy-b-section-1]."
            ),
            statuses={
                "POLICY-A": MandatoryStatus.MANDATORY,
                "POLICY-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].directive_id, "POLICY-B")
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], policy_a)

    def test_each_reused_marker_is_validated_in_its_local_claim(self) -> None:
        section_three = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s3",
            section_number="3",
        )
        section_four = Citation(
            ref_id="section-4",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s4",
            section_number="4",
        )

        selected = _select_final_directive_citations(
            [section_three, section_four],
            assistant_text=(
                "Section 3 applies [section-3].\n\n"
                "Section 4 applies [section-3]."
            ),
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(
            [
                citation.section_number
                for citation in selected
                if citation.citation_scope != "document"
            ],
            ["3", "4"],
        )
        self.assertTrue(
            any(
                citation.citation_scope == "document"
                and citation.ref_id == section_three.ref_id
                for citation in selected
            )
        )
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[0].ref_id, section_three.ref_id)

    def test_rejected_marker_without_mandate_status_stays_resolvable(
        self,
    ) -> None:
        citation = Citation(
            ref_id="other-section-3",
            source_name="Other policy",
            directive_id="DIR-2",
            directive_version_id="DIR-2:v1",
            section_id="s3",
            section_number="3",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="Section 4 applies [other-section-3].",
            statuses={},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].ref_id, citation.ref_id)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_exact_marker_directive_extends_explicit_correction_scope(
        self,
    ) -> None:
        section_three = Citation(
            ref_id="b-section-3",
            source_name="Policy B",
            directive_id="DIR-B",
            section_id="b3",
            section_number="3",
        )
        section_eight = Citation(
            ref_id="b-section-8",
            source_name="Policy B",
            directive_id="DIR-B",
            section_id="b8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [section_three, section_eight],
            assistant_text="Policy B section 8 applies [b-section-3].",
            statuses={"DIR-A": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], section_eight)

    def test_external_article_does_not_suppress_matching_directive_section(
        self,
    ) -> None:
        section_three_one = Citation(
            ref_id="section-3-1",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-1",
            section_number="3.1",
        )

        selected = _select_final_directive_citations(
            [section_three_one],
            assistant_text=(
                "Podle článku 6 GDPR a sekce 3.1 interního předpisu "
                "platí omezení [section-3-1]."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(selected, (section_three_one,))

    def test_external_article_does_not_select_same_numbered_section(
        self,
    ) -> None:
        section_three_one = Citation(
            ref_id="section-3-1",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-1",
            section_number="3.1",
        )
        section_six = Citation(
            ref_id="section-6",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s6",
            section_number="6",
        )

        selected = _select_final_directive_citations(
            [section_three_one, section_six],
            assistant_text=(
                "Podle sekce 3.1 interního předpisu a článku 6 GDPR "
                "platí omezení [section-3-1]."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(selected, (section_three_one,))

    def test_external_article_alone_does_not_reject_exact_marker(self) -> None:
        section_three_one = Citation(
            ref_id="section-3-1",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-1",
            section_number="3.1",
        )

        selected = _select_final_directive_citations(
            [section_three_one],
            assistant_text=(
                "Požadavek vyplývá z článku 6 GDPR "
                "[section-3-1]."
            ),
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(selected, (section_three_one,))

    def test_unqualified_article_rejects_mismatched_exact_marker(self) -> None:
        citation = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s3",
            section_number="3",
        )

        for answer in (
            "Article 8 applies [section-3].",
            "Art. 8 applies [section-3].",
            "Chapter 8 applies [section-3].",
            "kap. 8 platí [section-3].",
        ):
            with self.subTest(answer=answer):
                selected = _select_final_directive_citations(
                    [citation],
                    assistant_text=answer,
                    statuses={"DIR-1": MandatoryStatus.MANDATORY},
                )
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].citation_scope, "document")

    def test_post_punctuation_marker_uses_preceding_claim(self) -> None:
        citation = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s3",
            section_number="3",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="Article 8 applies. [section-3]",
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_marker_free_fallback_rejects_mismatched_section_label(self) -> None:
        section_three_one = Citation(
            ref_id="section-3-1",
            source_name="AI directive",
            directive_id="MP/25/0277",
            section_id="s3-1",
            section_number="3.1",
        )

        selected = _select_final_directive_citations(
            [section_three_one],
            assistant_text="Podle sekce 3.2 platí omezení.",
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_preceding_foreign_identity_blocks_singleton_fallback(self) -> None:
        citations = [
            Citation(
                ref_id=f"a-section-{number}",
                source_name="Policy A",
                directive_id="DIR-A",
                directive_version_id="DIR-A:v1",
                section_id=f"a{number}",
                section_number=number,
            )
            for number in ("3", "8")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text="According to DIR-B, section 8 applies.",
            statuses={"DIR-A": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_hyphenated_foreign_identity_rejects_exact_marker(self) -> None:
        citation = Citation(
            ref_id="b-section-8",
            source_name="Policy B",
            directive_id="DIR-B",
            directive_version_id="DIR-B:v1",
            section_id="b8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="DIR-A section 8 applies [b-section-8].",
            statuses={"DIR-B": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

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

    def test_source_line_selects_section_named_in_reply(self) -> None:
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

    def test_source_article_replaces_wrong_exact_marker(self) -> None:
        section_three = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s3",
            section_number="3",
        )
        section_eight = Citation(
            ref_id="section-8",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [section_three, section_eight],
            assistant_text="Source: DIR-1, article 8 [section-3].",
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected[0].ref_id, section_three.ref_id)
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], section_eight)

    def test_czech_article_abbreviation_replaces_wrong_marker(self) -> None:
        section_three = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s3",
            section_number="3",
        )
        section_eight = Citation(
            ref_id="section-8",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [section_three, section_eight],
            assistant_text="Zdroj: DIR-1, čl. 8 [section-3].",
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected[0].ref_id, section_three.ref_id)
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], section_eight)

    def test_narrative_article_with_following_identity_is_validated(
        self,
    ) -> None:
        section_three = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s3",
            section_number="3",
        )
        section_eight = Citation(
            ref_id="section-8",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [section_three, section_eight],
            assistant_text=(
                "Podle čl. 8 směrnice DIR-1 platí omezení [section-3]."
            ),
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], section_eight)

    def test_section_identity_cannot_fall_back_to_other_directive(self) -> None:
        section_a = Citation(
            ref_id="a-section-8",
            source_name="Policy A",
            directive_id="DIR-A",
            section_id="a8",
            section_number="8",
        )
        section_b = Citation(
            ref_id="b-section-8",
            source_name="Policy B",
            directive_id="DIR-B",
            section_id="b8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [section_a, section_b],
            assistant_text="Section 8 of DIR-B applies [a-section-8].",
            statuses={"DIR-A": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].directive_id, "DIR-A")
        self.assertEqual(selected[0].citation_scope, "document")

    def test_unretrieved_foreign_identity_cannot_use_singleton_fallback(
        self,
    ) -> None:
        section_three = Citation(
            ref_id="a-section-3",
            source_name="Policy A",
            directive_id="DIR-A",
            section_id="a3",
            section_number="3",
        )
        section_eight = Citation(
            ref_id="a-section-8",
            source_name="Policy A",
            directive_id="DIR-A",
            section_id="a8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [section_three, section_eight],
            assistant_text=(
                "Section 3 of DIR-A applies [a-section-3]. "
                "Section 8 of DIR-B has a separate rule."
            ),
            statuses={"DIR-A": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected, (section_three,))

    def test_section_identity_does_not_cross_sentence_boundary(self) -> None:
        citations = [
            Citation(
                ref_id=ref_id,
                source_name=f"Policy {directive_id[-1]}",
                directive_id=directive_id,
                section_id=ref_id,
                section_number=number,
            )
            for ref_id, directive_id, number in (
                ("opaque-a3", "DIR-A", "3"),
                ("opaque-b3", "DIR-B", "3"),
                ("opaque-b8", "DIR-B", "8"),
            )
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text=(
                "Section 3 applies [opaque-a3]. "
                "Section 8 of DIR-B applies [opaque-b8]."
            ),
            statuses={
                "DIR-A": MandatoryStatus.MANDATORY,
                "DIR-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(
            [
                (citation.directive_id, citation.section_number)
                for citation in selected
                if citation.citation_scope != "document"
            ],
            [("DIR-A", "3"), ("DIR-B", "8")],
        )

    def test_section_identity_does_not_cross_later_reference(self) -> None:
        citations = [
            Citation(
                ref_id=f"{directive_id}-{number}",
                source_name=f"Policy {directive_id[-1]}",
                directive_id=directive_id,
                section_id=f"{directive_id[-1].lower()}{number}",
                section_number=number,
            )
            for directive_id in ("DIR-A", "DIR-B")
            for number in ("3", "8")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text=(
                "Section 3 of DIR-A and section 8 of DIR-B apply."
            ),
            statuses={
                "DIR-A": MandatoryStatus.MANDATORY,
                "DIR-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(
            [
                (citation.directive_id, citation.section_number)
                for citation in selected
            ],
            [("DIR-A", "3"), ("DIR-B", "8")],
        )

    def test_same_section_number_can_apply_to_multiple_directives(self) -> None:
        citations = [
            Citation(
                ref_id=f"{directive_id}-3",
                source_name=f"Policy {directive_id[-1]}",
                directive_id=directive_id,
                section_id=f"{directive_id[-1].lower()}3",
                section_number="3",
            )
            for directive_id in ("DIR-A", "DIR-B")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text=(
                "Section 3 of DIR-A applies. "
                "Section 3 of DIR-B also applies."
            ),
            statuses={
                "DIR-A": MandatoryStatus.MANDATORY,
                "DIR-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(
            [citation.directive_id for citation in selected],
            ["DIR-A", "DIR-B"],
        )

    def test_foreign_identity_blocks_singleton_section_fallback(self) -> None:
        citation = Citation(
            ref_id="a-section-8",
            source_name="Policy A",
            directive_id="DIR-A",
            directive_version_id="DIR-A:v1",
            section_id="a8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="Section 8 of DIR-B has a separate rule.",
            statuses={"DIR-A": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_spaced_foreign_identity_blocks_singleton_section_fallback(
        self,
    ) -> None:
        citation = Citation(
            ref_id="policy-a-section-8",
            source_name="Policy A",
            directive_id="POLICY A",
            section_id="a8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="Section 8 of Policy B applies.",
            statuses={"POLICY A": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_punctuated_foreign_identity_blocks_singleton_fallback(
        self,
    ) -> None:
        citations = [
            Citation(
                ref_id=f"b-section-{number}",
                source_name="Policy B",
                directive_id="DIR-B",
                directive_version_id="DIR-B:v1",
                section_id=f"b{number}",
                section_number=number,
            )
            for number in ("3", "8")
        ]

        for assistant_text in (
            "Section 8 (DIR-X) has a separate rule.",
            "Section 8, DIR-X has a separate rule.",
        ):
            with self.subTest(assistant_text=assistant_text):
                selected = _select_final_directive_citations(
                    citations,
                    assistant_text=assistant_text,
                    statuses={"DIR-B": MandatoryStatus.MANDATORY},
                )
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].citation_scope, "document")

    def test_unicode_and_unseparated_foreign_ids_block_fallback(self) -> None:
        citations = [
            Citation(
                ref_id=f"b-section-{number}",
                source_name="Policy B",
                directive_id="DIR-B",
                directive_version_id="DIR-B:v1",
                section_id=f"b{number}",
                section_number=number,
            )
            for number in ("3", "8")
        ]

        for assistant_text in (
            "Section 8 of DIRB has a separate rule.",
            "Section 8 of ČD/42-A has a separate rule.",
        ):
            with self.subTest(assistant_text=assistant_text):
                selected = _select_final_directive_citations(
                    citations,
                    assistant_text=assistant_text,
                    statuses={"DIR-B": MandatoryStatus.MANDATORY},
                )
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].citation_scope, "document")

    def test_short_source_name_validates_exact_marker(self) -> None:
        citation = Citation(
            ref_id="b-section-8",
            source_name="Policy B",
            directive_id="DIR-B",
            section_id="b8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="Section 8 of Policy B applies [b-section-8].",
            statuses={"DIR-B": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected, (citation,))

    def test_overlapping_titles_prefer_most_specific_directive(self) -> None:
        base_policy = Citation(
            ref_id="base-section-8",
            source_name="Information Security Policy",
            directive_id="BASE",
            section_id="base8",
            section_number="8",
        )
        supplier_policy = Citation(
            ref_id="supplier-section-8",
            source_name="Information Security Policy for Suppliers",
            directive_id="SUPPLIERS",
            section_id="supplier8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [base_policy, supplier_policy],
            assistant_text=(
                "Section 8 of Information Security Policy for Suppliers "
                "applies [base-section-8]."
            ),
            statuses={
                "BASE": MandatoryStatus.MANDATORY,
                "SUPPLIERS": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(selected[0].ref_id, "base-section-8")
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], supplier_policy)

    def test_generic_directive_word_does_not_create_identity_conflict(
        self,
    ) -> None:
        citation = Citation(
            ref_id="section-8",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="Section 8 of the directive applies [section-8].",
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(selected, (citation,))

    def test_foreign_identity_rejects_exact_marker_without_foreign_result(
        self,
    ) -> None:
        citation = Citation(
            ref_id="a-section-8",
            source_name="Policy A",
            directive_id="DIR-A",
            directive_version_id="DIR-A:v1",
            section_id="a8",
            section_number="8",
        )

        selected = _select_final_directive_citations(
            [citation],
            assistant_text="Section 8 of DIR-B applies [a-section-8].",
            statuses={"DIR-A": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].ref_id, citation.ref_id)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_unavailable_source_article_downgrades_wrong_marker(self) -> None:
        section_three = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            directive_version_id="DIR-1:v1",
            section_id="s3",
            section_number="3",
        )

        selected = _select_final_directive_citations(
            [section_three],
            assistant_text="Source: DIR-1, article 8 [section-3].",
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_duplicate_title_in_prose_does_not_select_sections(self) -> None:
        citations = [
            Citation(
                ref_id=f"section-{number}",
                source_name="AI directive",
                directive_id="MP/25/0277",
                directive_version_id="MP/25/0277:v1.1",
                section_id=f"s{number}",
                section_number=number,
                section_title="PODNIKOVÝ COPILOT",
            )
            for number in ("3.1", "6.1")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text="Pro interní dokumenty použijte Podnikový Copilot.",
            statuses={"MP/25/0277": MandatoryStatus.NON_MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].citation_scope, "document")

    def test_article_with_following_identity_selects_section(self) -> None:
        citations = [
            Citation(
                ref_id=f"section-{number}",
                source_name="Policy",
                directive_id="DIR-1",
                directive_version_id="DIR-1:v1",
                section_id=f"s{number}",
                section_number=number,
            )
            for number in ("6", "7")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text="Article 6 of DIR-1 defines the external requirement.",
            statuses={"DIR-1": MandatoryStatus.MANDATORY},
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].section_number, "6")

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

    def test_qualified_and_abbreviated_section_labels_are_supported(
        self,
    ) -> None:
        citation = Citation(
            ref_id="section-8",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s8",
            section_number="8",
        )

        for source_line in (
            "Source: DIR-1, section No. 8.",
            "Source: DIR-1, section number 8.",
            "Source: DIR-1, Art. 8.",
            "Source: DIR-1, Sec. 8.",
            "Zdroj: DIR-1, kap. 8.",
        ):
            with self.subTest(source_line=source_line):
                selected = _select_final_directive_citations(
                    [citation],
                    assistant_text=source_line,
                    statuses={"DIR-1": MandatoryStatus.MANDATORY},
                )
                self.assertEqual(selected, (citation,))

    def test_qualified_abbreviations_validate_wrong_exact_marker(self) -> None:
        section_three = Citation(
            ref_id="section-3",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s3",
            section_number="3",
        )
        section_eight = Citation(
            ref_id="section-8",
            source_name="Policy",
            directive_id="DIR-1",
            section_id="s8",
            section_number="8",
        )

        for source_line in (
            "Source: DIR-1, section No. 8 [section-3].",
            "Zdroj: DIR-1, kap. 8 [section-3].",
        ):
            with self.subTest(source_line=source_line):
                selected = _select_final_directive_citations(
                    [section_three, section_eight],
                    assistant_text=source_line,
                    statuses={"DIR-1": MandatoryStatus.MANDATORY},
                )
                self.assertEqual(selected[0].citation_scope, "document")
                self.assertEqual(selected[1], section_eight)

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

    def test_source_line_scopes_sections_to_each_directive_clause(self) -> None:
        citations = [
            Citation(
                ref_id=f"{directive_id}-{number}",
                source_name=f"Policy {directive_id[-1]}",
                directive_id=directive_id,
                section_id=f"{directive_id[-1].lower()}{number}",
                section_number=number,
            )
            for directive_id in ("DIR-A", "DIR-B")
            for number in ("3", "8")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text=(
                "Source: DIR-A, article 3; DIR-B, article 8."
            ),
            statuses={
                "DIR-A": MandatoryStatus.MANDATORY,
                "DIR-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(
            [
                (citation.directive_id, citation.section_number)
                for citation in selected
            ],
            [("DIR-A", "3"), ("DIR-B", "8")],
        )

    def test_source_line_scopes_comma_separated_directives(self) -> None:
        citations = [
            Citation(
                ref_id=f"{directive_id}-{number}",
                source_name=f"Policy {directive_id[-1]}",
                directive_id=directive_id,
                section_id=f"{directive_id[-1].lower()}{number}",
                section_number=number,
            )
            for directive_id in ("DIR-A", "DIR-B")
            for number in ("3", "8")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text="Source: DIR-A, section 3, DIR-B, section 8.",
            statuses={
                "DIR-A": MandatoryStatus.MANDATORY,
                "DIR-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(
            [
                (citation.directive_id, citation.section_number)
                for citation in selected
            ],
            [("DIR-A", "3"), ("DIR-B", "8")],
        )

    def test_comma_separated_source_rejects_marker_from_prior_entry(
        self,
    ) -> None:
        citations = [
            Citation(
                ref_id=f"{directive_id}-{number}",
                source_name=f"Policy {directive_id[-1]}",
                directive_id=directive_id,
                section_id=f"{directive_id[-1].lower()}{number}",
                section_number=number,
            )
            for directive_id, number in (("DIR-A", "3"), ("DIR-B", "8"))
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text=(
                "Source: DIR-A, section 3, DIR-B, section 8 [DIR-A-3]."
            ),
            statuses={
                "DIR-A": MandatoryStatus.MANDATORY,
                "DIR-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(selected[0].ref_id, "DIR-A-3")
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(selected[1], citations[0])
        self.assertEqual(selected[2], citations[1])

    def test_later_source_clause_validates_its_exact_marker(self) -> None:
        citations = [
            Citation(
                ref_id=f"{directive_id}-{number}",
                source_name=f"Policy {directive_id[-1]}",
                directive_id=directive_id,
                section_id=f"{directive_id[-1].lower()}{number}",
                section_number=number,
            )
            for directive_id in ("DIR-A", "DIR-B")
            for number in ("3", "8")
        ]

        selected = _select_final_directive_citations(
            citations,
            assistant_text=(
                "Source: DIR-A, article 3 [DIR-A-3]; "
                "DIR-B, article 8 [DIR-B-3]."
            ),
            statuses={
                "DIR-A": MandatoryStatus.MANDATORY,
                "DIR-B": MandatoryStatus.MANDATORY,
            },
        )

        self.assertEqual(selected[0].ref_id, "DIR-B-3")
        self.assertEqual(selected[0].citation_scope, "document")
        self.assertEqual(
            {
                (citation.directive_id, citation.section_number)
                for citation in selected
                if citation.citation_scope != "document"
            },
            {("DIR-A", "3"), ("DIR-B", "8")},
        )

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
