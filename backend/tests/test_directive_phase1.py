from __future__ import annotations

import asyncio
import json
import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ag_ui.core.events import CustomEvent
from fastapi import HTTPException

from agent_contracts import (
    AgentType,
    Citation,
    DIRECTIVE_RAG_PROMPT_VERSION,
    MandatoryStatus,
    PROMPT_VERSION,
    RuntimeDescriptor,
    RuntimeState,
    ToolResultEnvelope,
    ToolResultEvent,
    WorkflowProgressEvent,
    WorkflowStage,
    WorkflowStatus,
    render_directive_rag_instructions,
)
from agent_memory_backend import server
from agent_memory_backend.agui_adapter import to_agui_events
from agent_memory_backend.backend_services import (
    BackendServices,
    visible_agent_types,
)
from agent_memory_backend.config import get_settings
from agent_memory_backend.conversation_coordinator import ConversationCoordinator
from agent_memory_backend.conversation_history import runtime_state_from_document
from agent_memory_backend.conversation_registry import ConversationRegistry
from agent_memory_backend.foundry_hosted_maf_runtime import (
    FoundryHostedMafRuntime,
)
from agent_memory_backend.foundry_runtime_base import server_tool_events


_PROJECT_ENDPOINT = (
    "https://example.services.ai.azure.com/api/projects/directive-test"
)


def _hosted_runtime(
    *,
    agent_type: AgentType = AgentType.AGENT_FRAMEWORK,
    agent_name: str = "customer-support-maf-hosted",
    release_id: str = "support-release",
    prompt_version: str = PROMPT_VERSION,
) -> FoundryHostedMafRuntime:
    return FoundryHostedMafRuntime(
        agent_type=agent_type,
        project_endpoint=_PROJECT_ENDPOINT,
        physical_agent_name=agent_name,
        physical_agent_endpoint=(
            f"{_PROJECT_ENDPOINT}/agents/{agent_name}/endpoint/protocols/openai"
        ),
        release_id=release_id,
        prompt_version=prompt_version,
        request_timeout_seconds=30,
    )


class DirectiveContractTests(unittest.TestCase):
    def test_existing_values_and_citation_payload_remain_compatible(self) -> None:
        self.assertEqual(AgentType.FOUNDRY_PROMPT.value, "foundry-prompt")
        self.assertEqual(AgentType.AGENT_FRAMEWORK.value, "agent-framework")
        self.assertEqual(AgentType.DIRECTIVE_RAG.value, "directive-rag")

        citation = Citation(ref_id="ref-1", source_name="Source")
        expected = {
            "ref_id": "ref-1",
            "source_name": "Source",
            "search_idx": None,
            "url": None,
        }
        self.assertEqual(citation.to_dict(), expected)
        self.assertEqual(
            ToolResultEnvelope(citations=(citation,), status="ok").to_dict()[
                "citations"
            ],
            [expected],
        )

    def test_directive_citation_and_progress_are_typed(self) -> None:
        citation = Citation(
            ref_id="dir-001-v2-section-4",
            source_name="Travel Directive",
            directive_id="DIR-001",
            directive_version_id="DIR-001:v2",
            version_label="2.0",
            section_id="section-4",
            section_number="4",
            section_title="Eligibility",
            page_from=10,
            page_to=12,
            effective_from="2026-01-01",
            mandatory_status=MandatoryStatus.UNKNOWN,
            mandate_snapshot_id="snapshot-1",
            retrieval_strategy="full_document",
            coverage={"processed_sections": 12, "total_sections": 12},
        )
        payload = citation.to_dict()
        self.assertEqual(payload["mandatory_status"], "unknown")
        self.assertEqual(payload["directive_id"], "DIR-001")
        self.assertEqual(payload["coverage"]["total_sections"], 12)

        events = tuple(
            to_agui_events(
                WorkflowProgressEvent(
                    WorkflowStage.SEARCHING,
                    WorkflowStatus.IN_PROGRESS,
                )
            )
        )
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], CustomEvent)
        self.assertEqual(events[0].name, "agent_progress")
        self.assertEqual(
            events[0].value,
            {"stage": "searching", "status": "in_progress"},
        )

    def test_directive_prompt_is_versioned_separately(self) -> None:
        prompt = render_directive_rag_instructions()
        self.assertIn("retrieval planning", prompt)
        self.assertIn("non-authoritative guidance", prompt)
        self.assertNotEqual(DIRECTIVE_RAG_PROMPT_VERSION, PROMPT_VERSION)

    def test_hosted_tool_payload_preserves_directive_citation_fields(self) -> None:
        response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="mcp_call",
                    call_id="call-1",
                    name="get_directive_content",
                    output=json.dumps(
                        {
                            "status": "ok",
                            "data": {},
                            "citations": [
                                {
                                    "ref_id": "DIR-1:v2:s3",
                                    "source_name": "Directive 1",
                                    "directive_id": "DIR-1",
                                    "directive_version_id": "DIR-1:v2",
                                    "section_id": "s3",
                                    "page_from": 4,
                                    "mandatory_status": "mandatory",
                                    "coverage": {
                                        "processed_sections": 3,
                                        "total_sections": 3,
                                    },
                                }
                            ],
                        }
                    ),
                )
            ]
        )

        events = server_tool_events(response)
        result_event = next(
            event for event in events if isinstance(event, ToolResultEvent)
        )
        citation = result_event.result.citations[0]
        self.assertEqual(citation.directive_id, "DIR-1")
        self.assertEqual(citation.page_from, 4)
        self.assertEqual(citation.mandatory_status, MandatoryStatus.MANDATORY)
        self.assertEqual(citation.coverage["total_sections"], 3)


class DirectiveSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_directive_defaults_are_disabled_and_endpoints_are_agent_bound(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {"FOUNDRY_PROJECT_ENDPOINT": _PROJECT_ENDPOINT},
            clear=True,
        ):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertFalse(settings.directive_agent_enabled)
        self.assertFalse(settings.directive_agent_visible)
        self.assertTrue(
            settings.foundry_hosted_agent_endpoint.endswith(
                "/agents/customer-support-maf-hosted/endpoint/protocols/openai"
            )
        )
        self.assertTrue(
            settings.directive_foundry_agent_endpoint.endswith(
                "/agents/directive-rag-maf-hosted/endpoint/protocols/openai"
            )
        )
        self.assertEqual(settings.directive_max_related_depth, 2)

    def test_related_depth_cannot_exceed_architecture_limit(self) -> None:
        with patch.dict(
            os.environ,
            {"DIRECTIVE_MAX_RELATED_DEPTH": "3"},
            clear=True,
        ):
            get_settings.cache_clear()
            with self.assertRaisesRegex(
                ValueError,
                "DIRECTIVE_MAX_RELATED_DEPTH",
            ):
                get_settings()

    def test_directive_tool_limits_are_bounded(self) -> None:
        for name, value in (
            ("DIRECTIVE_MAX_CONTENT_TOKENS", "900001"),
            ("DIRECTIVE_MAX_SECTIONS_PER_CALL", "101"),
            ("DIRECTIVE_MAX_SEARCH_RESULTS", "101"),
            ("DIRECTIVE_TOOL_TIMEOUT_SECONDS", "601"),
            ("DIRECTIVE_TOOL_TIMEOUT_SECONDS", "nan"),
            ("DIRECTIVE_PROGRESS_HEARTBEAT_SECONDS", "61"),
            ("DIRECTIVE_PROGRESS_HEARTBEAT_SECONDS", "nan"),
        ):
            with self.subTest(name=name):
                with patch.dict(os.environ, {name: value}, clear=True):
                    get_settings.cache_clear()
                    with self.assertRaisesRegex(ValueError, name):
                        get_settings()

    def test_agent_names_are_url_encoded_in_derived_endpoints(self) -> None:
        with patch.dict(
            os.environ,
            {
                "FOUNDRY_PROJECT_ENDPOINT": _PROJECT_ENDPOINT,
                "FOUNDRY_HOSTED_AGENT_NAME": "support agent",
                "DIRECTIVE_FOUNDRY_AGENT_NAME": "directive/agent",
            },
            clear=True,
        ):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertIn(
            "/agents/support%20agent/",
            settings.foundry_hosted_agent_endpoint,
        )
        self.assertIn(
            "/agents/directive%2Fagent/",
            settings.directive_foundry_agent_endpoint,
        )


class HostedRuntimeEndpointTests(unittest.IsolatedAsyncioTestCase):
    def test_generic_or_mismatched_endpoint_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "agent-specific"):
            FoundryHostedMafRuntime(
                agent_type=AgentType.AGENT_FRAMEWORK,
                project_endpoint=_PROJECT_ENDPOINT,
                physical_agent_name="support-agent",
                physical_agent_endpoint=f"{_PROJECT_ENDPOINT}/openai/v1",
                release_id="release",
                prompt_version="prompt",
                request_timeout_seconds=30,
            )
        with self.assertRaisesRegex(ValueError, "agent-specific"):
            FoundryHostedMafRuntime(
                agent_type=AgentType.AGENT_FRAMEWORK,
                project_endpoint=_PROJECT_ENDPOINT,
                physical_agent_name="support-agent",
                physical_agent_endpoint=(
                    "https://other.example/agents/support-agent"
                    "/endpoint/protocols/openai"
                ),
                release_id="release",
                prompt_version="prompt",
                request_timeout_seconds=30,
            )

    async def test_initialize_binds_and_probes_the_physical_endpoint(self) -> None:
        response = SimpleNamespace(
            id="response-health",
            status="completed",
            model_extra={"agent_session_id": "health-session"},
        )
        openai = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(return_value=response)),
            close=AsyncMock(),
        )
        agents = SimpleNamespace(delete_session=AsyncMock())
        project = SimpleNamespace(
            agents=agents,
            get_openai_client=Mock(return_value=openai),
            close=AsyncMock(),
        )
        runtime = _hosted_runtime()

        with (
            patch(
                "azure.ai.projects.aio.AIProjectClient",
                return_value=project,
            ) as project_client,
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.get_credential",
                return_value=object(),
            ),
        ):
            await runtime.initialize()

        project_client.assert_called_once_with(
            endpoint=_PROJECT_ENDPOINT,
            credential=unittest.mock.ANY,
            allow_preview=True,
        )
        project.get_openai_client.assert_called_once_with(
            agent_name="customer-support-maf-hosted",
            base_url=(
                f"{_PROJECT_ENDPOINT}/agents/customer-support-maf-hosted"
                "/endpoint/protocols/openai"
            ),
            default_query={"api-version": "v1"},
        )
        probe = openai.responses.create.await_args.kwargs
        self.assertNotIn("max_output_tokens", probe)
        self.assertNotIn("x-ms-user-identity", probe["extra_headers"])
        agents.delete_session.assert_awaited_once_with(
            agent_name="customer-support-maf-hosted",
            session_id="health-session",
            headers=probe["extra_headers"],
        )
        await runtime.health_check()
        await runtime.close()

    async def test_probe_failure_closes_partially_initialized_clients(self) -> None:
        openai = SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("endpoint failed"))
            ),
            close=AsyncMock(),
        )
        project = SimpleNamespace(
            agents=SimpleNamespace(delete_session=AsyncMock()),
            get_openai_client=Mock(return_value=openai),
            close=AsyncMock(),
        )
        runtime = _hosted_runtime()

        with (
            patch(
                "azure.ai.projects.aio.AIProjectClient",
                return_value=project,
            ),
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.get_credential",
                return_value=object(),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "endpoint failed"):
                await runtime.initialize()

        openai.close.assert_awaited_once()
        project.close.assert_awaited_once()
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            await runtime.health_check()

    async def test_probe_cleanup_must_recover_before_runtime_is_healthy(
        self,
    ) -> None:
        response = SimpleNamespace(
            id="response-health",
            status="completed",
            model_extra={"agent_session_id": "health-session"},
        )
        delete_session = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        openai = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(return_value=response)),
            close=AsyncMock(),
        )
        project = SimpleNamespace(
            agents=SimpleNamespace(delete_session=delete_session),
            get_openai_client=Mock(return_value=openai),
            close=AsyncMock(),
        )
        runtime = _hosted_runtime()

        with (
            patch(
                "azure.ai.projects.aio.AIProjectClient",
                return_value=project,
            ),
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.get_credential",
                return_value=object(),
            ),
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            with (
                self.assertLogs("foundry_hosted_maf", level="ERROR"),
                self.assertRaisesRegex(RuntimeError, "session cleanup failed"),
            ):
                await runtime.initialize()

            self.assertEqual(delete_session.await_count, 6)
            self.assertEqual(
                runtime._pending_probe_session_id,
                "health-session",
            )
            with self.assertRaisesRegex(RuntimeError, "not initialized"):
                await runtime.health_check()

            delete_session.side_effect = None
            await runtime.initialize()

        self.assertEqual(openai.responses.create.await_count, 1)
        self.assertEqual(delete_session.await_count, 7)
        self.assertIsNone(runtime._pending_probe_session_id)
        await runtime.health_check()
        await runtime.close()

    async def test_close_time_cleanup_preserves_successful_probe(
        self,
    ) -> None:
        response = SimpleNamespace(
            id="response-health",
            status="completed",
            model_extra={"agent_session_id": "health-session"},
        )
        delete_session = AsyncMock(
            side_effect=[
                RuntimeError("cleanup failed"),
                RuntimeError("cleanup failed"),
                RuntimeError("cleanup failed"),
                None,
            ]
        )
        openai = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(return_value=response)),
            close=AsyncMock(),
        )
        project = SimpleNamespace(
            agents=SimpleNamespace(delete_session=delete_session),
            get_openai_client=Mock(return_value=openai),
            close=AsyncMock(),
        )
        runtime = _hosted_runtime()

        with (
            patch(
                "azure.ai.projects.aio.AIProjectClient",
                return_value=project,
            ),
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.get_credential",
                return_value=object(),
            ),
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "session cleanup failed"):
                await runtime.initialize()
            await runtime.initialize()

        self.assertEqual(openai.responses.create.await_count, 1)
        self.assertEqual(delete_session.await_count, 4)
        await runtime.health_check()
        await runtime.close()

    async def test_close_reclaims_pending_probe_session_without_inference(
        self,
    ) -> None:
        delete_session = AsyncMock()
        runtime = _hosted_runtime()
        runtime._openai = SimpleNamespace(close=AsyncMock())
        runtime._project = SimpleNamespace(
            agents=SimpleNamespace(delete_session=delete_session),
            close=AsyncMock(),
        )
        runtime._pending_probe_session_id = "pending-session"
        runtime._pending_probe_was_verified = True

        await runtime.close()

        delete_session.assert_awaited_once_with(
            agent_name="customer-support-maf-hosted",
            session_id="pending-session",
            headers={"Foundry-Features": "HostedAgents=V1Preview"},
        )
        self.assertIsNone(runtime._pending_probe_session_id)

    async def test_close_attempts_both_clients_after_a_close_failure(self) -> None:
        runtime = _hosted_runtime()
        runtime._openai = SimpleNamespace(
            close=AsyncMock(side_effect=RuntimeError("openai close failed"))
        )
        runtime._project = SimpleNamespace(close=AsyncMock())
        project = runtime._project

        with self.assertRaisesRegex(RuntimeError, "runtime cleanly"):
            await runtime.close()

        project.close.assert_awaited_once()
        self.assertIsNone(runtime._openai)
        self.assertIsNone(runtime._project)

    async def test_two_hosted_runtimes_keep_independent_descriptors(self) -> None:
        support = _hosted_runtime()
        directive = _hosted_runtime(
            agent_type=AgentType.DIRECTIVE_RAG,
            agent_name="directive-rag-maf-hosted",
            release_id="directive-release",
            prompt_version=DIRECTIVE_RAG_PROMPT_VERSION,
        )
        support._project = SimpleNamespace(
            agents=SimpleNamespace(
                create_session=AsyncMock(
                    return_value=SimpleNamespace(
                        agent_session_id="support-session"
                    )
                )
            )
        )
        directive._project = SimpleNamespace(
            agents=SimpleNamespace(
                create_session=AsyncMock(
                    return_value=SimpleNamespace(
                        agent_session_id="directive-session"
                    )
                )
            )
        )
        support._openai = SimpleNamespace(
            conversations=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(id="support-conversation")
                )
            )
        )
        directive._openai = SimpleNamespace(
            conversations=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(id="directive-conversation")
                )
            )
        )

        support_state = await support.create_state("app-support", "tenant:user")
        directive_state = await directive.create_state(
            "app-directive",
            "tenant:user",
        )

        self.assertEqual(
            support_state.descriptor.agent_type,
            AgentType.AGENT_FRAMEWORK,
        )
        self.assertEqual(
            directive_state.descriptor.agent_type,
            AgentType.DIRECTIVE_RAG,
        )
        self.assertEqual(
            directive_state.descriptor.physical_agent_name,
            "directive-rag-maf-hosted",
        )
        self.assertEqual(
            directive_state.descriptor.release_id,
            "directive-release",
        )
        self.assertNotEqual(
            support_state.descriptor.prompt_version,
            directive_state.descriptor.prompt_version,
        )


class DirectiveFeatureBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_directive_agent_is_hidden_until_visibility_is_enabled(
        self,
    ) -> None:
        hidden = SimpleNamespace(directive_agent_visible=False)
        visible = SimpleNamespace(directive_agent_visible=True)
        self.assertEqual(
            visible_agent_types(hidden),
            (AgentType.FOUNDRY_PROMPT, AgentType.AGENT_FRAMEWORK),
        )
        self.assertEqual(
            visible_agent_types(visible),
            (
                AgentType.FOUNDRY_PROMPT,
                AgentType.AGENT_FRAMEWORK,
                AgentType.DIRECTIVE_RAG,
            ),
        )

        with patch(
            "agent_memory_backend.server.get_settings",
            return_value=hidden,
        ):
            payload = await server.list_agents(object())
        self.assertEqual(
            [agent["agent_type"] for agent in payload["agents"]],
            ["foundry-prompt", "agent-framework"],
        )

    def test_mock_composition_adds_directive_runtime_only_when_enabled(
        self,
    ) -> None:
        services = BackendServices.build()
        with patch.dict(os.environ, {"LLM_MODE": "mock"}, clear=True):
            get_settings.cache_clear()
            disabled_components = services._runtime_components(get_settings())
        self.assertEqual(
            tuple(disabled_components),
            (AgentType.FOUNDRY_PROMPT, AgentType.AGENT_FRAMEWORK),
        )

        with patch.dict(
            os.environ,
            {"LLM_MODE": "mock", "DIRECTIVE_AGENT_ENABLED": "true"},
            clear=True,
        ):
            get_settings.cache_clear()
            enabled_components = services._runtime_components(get_settings())
        self.assertEqual(
            tuple(enabled_components),
            (
                AgentType.FOUNDRY_PROMPT,
                AgentType.AGENT_FRAMEWORK,
                AgentType.DIRECTIVE_RAG,
            ),
        )
        get_settings.cache_clear()

    def test_bad_directive_endpoint_does_not_break_support_composition(
        self,
    ) -> None:
        services = BackendServices.build()
        with patch.dict(
            os.environ,
            {
                "LLM_MODE": "real",
                "FOUNDRY_PROJECT_ENDPOINT": _PROJECT_ENDPOINT,
                "FOUNDRY_HOSTED_ENABLED": "true",
                "DIRECTIVE_AGENT_ENABLED": "true",
                "DIRECTIVE_FOUNDRY_AGENT_ENDPOINT": (
                    "https://other.example/agents/directive-rag-maf-hosted"
                    "/endpoint/protocols/openai"
                ),
            },
            clear=True,
        ):
            get_settings.cache_clear()
            with self.assertLogs("backend_services", level="ERROR"):
                components = services._runtime_components(get_settings())

        self.assertIn(AgentType.AGENT_FRAMEWORK, components)
        self.assertNotIn(AgentType.DIRECTIVE_RAG, components)
        get_settings.cache_clear()

    async def test_directive_failure_is_degraded_not_app_wide_unready(
        self,
    ) -> None:
        services = BackendServices.build()
        with patch.dict(
            os.environ,
            {"DIRECTIVE_AGENT_ENABLED": "true"},
            clear=True,
        ):
            get_settings.cache_clear()
            settings = get_settings()
        readiness = await services.readiness(settings)

        self.assertEqual(readiness["status"], "ready")
        self.assertEqual(
            readiness["degraded_dependencies"],
            [
                "directive_hosted_maf",
                "directive_tool_gateway",
                "hosted_tool_gateway",
            ],
        )
        self.assertFalse(
            readiness["dependencies"]["directive_hosted_maf"]["required"]
        )
        get_settings.cache_clear()

    async def test_cancelled_startup_still_closes_backend_services(self) -> None:
        @asynccontextmanager
        async def mcp_lifespan(_app):
            yield

        with (
            patch.object(
                server.application_tools_mcp_app.router,
                "lifespan_context",
                new=mcp_lifespan,
            ),
            patch.object(
                server.services,
                "start",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ),
            patch.object(
                server.services,
                "close",
                new=AsyncMock(),
            ) as close_services,
            patch(
                "agent_memory_backend.server.get_settings",
                return_value=SimpleNamespace(),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                async with server.lifespan(server.app):
                    self.fail("cancelled startup must not enter application life")

        close_services.assert_awaited_once()

    async def test_failed_runtime_initialization_retries_until_recovered(
        self,
    ) -> None:
        services = BackendServices.build()
        runtime = SimpleNamespace(
            initialize=AsyncMock(
                side_effect=[RuntimeError("temporary outage"), None]
            ),
            close=AsyncMock(),
        )
        services._runtime_candidates[AgentType.DIRECTIVE_RAG] = runtime

        with (
            self.assertLogs("backend_services", level="ERROR"),
            patch(
                "agent_memory_backend.backend_services.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            await services._initialize_runtime(AgentType.DIRECTIVE_RAG, runtime)
            await services._runtime_retry_tasks[AgentType.DIRECTIVE_RAG]

        self.assertIs(
            services.runtime_registry[AgentType.DIRECTIVE_RAG],
            runtime,
        )
        self.assertEqual(runtime.initialize.await_count, 2)
        await services.close()

    def test_existing_and_directive_runtime_state_restore_without_schema_change(
        self,
    ) -> None:
        for agent_type in (
            AgentType.FOUNDRY_PROMPT,
            AgentType.AGENT_FRAMEWORK,
            AgentType.DIRECTIVE_RAG,
        ):
            restored = runtime_state_from_document(
                {
                    "metadata": {
                        "schema_version": 3,
                        "agent_type": agent_type.value,
                        "physical_agent_name": f"physical-{agent_type.value}",
                        "release_id": "release",
                        "prompt_version": "prompt",
                        "runtime_state": {
                            "foundry_conversation_id": "conversation",
                            "hosted_session_id": "session",
                        },
                    }
                }
            )
            self.assertIsNotNone(restored)
            self.assertEqual(restored.descriptor.agent_type, agent_type)
            self.assertEqual(restored.schema_version, 3)

    async def test_legacy_conversations_remain_support_only(self) -> None:
        support_state = RuntimeState(
            descriptor=RuntimeDescriptor(
                agent_type=AgentType.AGENT_FRAMEWORK,
                physical_agent_name="support-agent",
                release_id="support-release",
                prompt_version="support-prompt",
            )
        )
        support_runtime = SimpleNamespace(
            create_state=AsyncMock(return_value=support_state),
            delete_state=AsyncMock(),
        )
        directive_runtime = SimpleNamespace()
        history = SimpleNamespace(bind_runtime_state=AsyncMock())
        coordinator = ConversationCoordinator(
            ConversationRegistry(),
            history,
            SimpleNamespace(),
            {
                AgentType.AGENT_FRAMEWORK: support_runtime,
                AgentType.DIRECTIVE_RAG: directive_runtime,
            },
        )
        legacy_document = {
            "_etag": "etag-1",
            "messages": [{"role": "user", "content": "hello"}],
        }

        state, runtime = await coordinator._restore_runtime(
            legacy_document,
            "conversation-1",
            "tenant:user",
            AgentType.AGENT_FRAMEWORK,
        )
        self.assertIs(state, support_state)
        self.assertIs(runtime, support_runtime)
        support_runtime.create_state.assert_awaited_once()

        with self.assertRaisesRegex(
            HTTPException,
            "CONVERSATION_AGENT_IMMUTABLE",
        ):
            await coordinator._restore_runtime(
                legacy_document,
                "conversation-2",
                "tenant:user",
                AgentType.DIRECTIVE_RAG,
            )


if __name__ == "__main__":
    unittest.main()
