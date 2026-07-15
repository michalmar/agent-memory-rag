from __future__ import annotations

import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from azure.core import MatchConditions
from ag_ui.core.events import CustomEvent

from agent_memory_backend import server
from agent_contracts import (
    AgentType,
    Citation,
    CitationsEvent,
    RuntimeCompletedEvent,
    RuntimeDescriptor,
    RuntimeState,
    TextDeltaEvent,
    ToolStartedEvent,
    ToolResultEnvelope,
    ToolResultEvent,
    TurnContext,
    UsageEvent,
)
from agent_memory_backend.agent_tool_gateway import AgentToolRequest, dispatch_agent_tool
from agent_memory_backend.agent_tools import ToolExecutor
from agent_memory_backend.agui_adapter import to_agui_events
from agent_memory_backend.auth import AgentCaller, AgentTokenValidator, User
from agent_memory_backend.conversation_coordinator import ConversationCoordinator, PreparedConversation
from agent_memory_backend.conversation_history import ConversationHistoryStore
from agent_memory_backend.conversation_registry import ConversationRegistry, LiveConversation
from agent_memory_backend.foundry_hosted_maf_runtime import FoundryHostedMafRuntime
from agent_memory_backend.foundry_prompt_runtime import FoundryPromptRuntime
from agent_memory_backend.mock_agent_runtime import MockAgentRuntime
from agent_memory_backend.turn_accumulator import TurnAccumulator
from agent_memory_backend.user_profile_memory import UserProfileMemoryStore, public_profile


def _runtime_state(
    agent_type: AgentType,
    *,
    foundry_id: str = "foundry-conversation",
    hosted_id: str | None = None,
) -> RuntimeState:
    return RuntimeState(
        descriptor=RuntimeDescriptor(
            agent_type=agent_type,
            physical_agent_name=f"physical-{agent_type.value}",
            release_id="release-1",
            prompt_version="prompt-1",
            observed_agent_version="1",
        ),
        foundry_conversation_id=foundry_id,
        hosted_session_id=hosted_id,
    )


def _document(state: RuntimeState, *, user_id: str = "tenant:user") -> dict:
    return {
        "id": "conversation-1",
        "user_id": user_id,
        "messages": [],
        "metadata": {
            "schema_version": 3,
            "agent_type": state.descriptor.agent_type.value,
            "physical_agent_name": state.descriptor.physical_agent_name,
            "release_id": state.descriptor.release_id,
            "prompt_version": state.descriptor.prompt_version,
            "observed_agent_version": state.descriptor.observed_agent_version,
            "runtime_state": {
                "foundry_conversation_id": state.foundry_conversation_id,
                "hosted_session_id": state.hosted_session_id,
                "last_response_id": state.last_response_id,
            },
        },
    }


class AgentGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_uses_bound_user_and_hosted_session(self) -> None:
        state = _runtime_state(
            AgentType.AGENT_FRAMEWORK, hosted_id="hosted-session-1"
        )

        class History:
            async def get_by_hosted_session(
                self, user_id: str, session_id: str
            ) -> dict:
                self.lookup = (user_id, session_id)
                return _document(state, user_id=user_id)

        history = History()

        class Executor:
            async def execute_envelope(
                executor_self,
                name: str,
                arguments: dict,
                *,
                user_id: str,
            ) -> ToolResultEnvelope:
                self.assertEqual(user_id, "tenant:user")
                self.assertEqual(name, "get_order_status")
                self.assertEqual(arguments, {"order_id": "ORD-001"})
                return ToolResultEnvelope(status="ok", data={"status": "shipped"})

        request = AgentToolRequest(
            user_id="tenant:user",
            session_id="hosted-session-1",
            call_id="call-1",
            arguments={"order_id": "ORD-001"},
        )
        result = await dispatch_agent_tool(
            "get_order_status",
            request,
            AgentCaller(principal_id="hosted-principal", tenant_id="tenant"),
            history,
            Executor(),
        )

        self.assertEqual(history.lookup, ("tenant:user", "hosted-session-1"))
        self.assertEqual(result.status, "ok")

    async def test_gateway_rejects_non_hosted_runtime_binding(self) -> None:
        state = _runtime_state(
            AgentType.FOUNDRY_PROMPT, hosted_id="hosted-session-1"
        )

        class History:
            async def get_by_hosted_session(self, user_id: str, session_id: str):
                return _document(state, user_id=user_id)

        with self.assertRaises(HTTPException) as raised:
            await dispatch_agent_tool(
                "get_order_status",
                AgentToolRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    arguments={"order_id": "ORD-001"},
                ),
                AgentCaller(principal_id="hosted-principal", tenant_id="tenant"),
                History(),
                ToolExecutor(None, None),
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_gateway_request_rejects_unknown_identity_fields(self) -> None:
        with self.assertRaises(ValidationError):
            AgentToolRequest.model_validate(
                {
                    "user_id": "tenant:user",
                    "session_id": "hosted-session-1",
                    "call_id": "call-1",
                    "arguments": {},
                    "tenant_id": "attacker",
                }
            )


class AgentTokenPolicyTests(unittest.TestCase):
    def _validator(self) -> AgentTokenValidator:
        validator = object.__new__(AgentTokenValidator)
        validator.tenant_id = "tenant"
        validator.audience = "api://backend"
        validator.required_role = "AgentTools.Invoke"
        validator.allowed_principals = {"hosted-principal"}
        validator.allowed_issuers = {
            "https://login.microsoftonline.com/tenant/v2.0"
        }
        validator._jwk_client = SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key="key")
        )
        return validator

    def _claims(self, **overrides) -> dict:
        claims = {
            "iss": "https://login.microsoftonline.com/tenant/v2.0",
            "tid": "tenant",
            "oid": "hosted-principal",
            "roles": ["AgentTools.Invoke"],
        }
        claims.update(overrides)
        return claims

    def test_application_role_and_allowlisted_principal_are_required(self) -> None:
        validator = self._validator()
        with patch("agent_memory_backend.auth.jwt.decode", return_value=self._claims()):
            caller = validator.validate("Bearer token")
        self.assertEqual(caller.principal_id, "hosted-principal")

        with patch("agent_memory_backend.auth.jwt.decode", return_value=self._claims(scp="user.read")):
            with self.assertRaises(HTTPException) as delegated:
                validator.validate("Bearer token")
        self.assertEqual(delegated.exception.status_code, 403)

        with patch(
            "agent_memory_backend.auth.jwt.decode",
            return_value=self._claims(oid="old-principal"),
        ):
            with self.assertRaises(HTTPException) as untrusted:
                validator.validate("Bearer token")
        self.assertEqual(untrusted.exception.status_code, 403)


class RemoteRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_mock_does_not_expose_application_tools(self) -> None:
        runtime = MockAgentRuntime(
            AgentType.FOUNDRY_PROMPT, ToolExecutor(None, None)
        )
        state = await runtime.create_state("application-1", "tenant:user")
        context = TurnContext("application-1", "tenant:user", state)

        events = [
            event
            async for event in runtime.stream_turn(
                "Check order ORD-001", context
            )
        ]

        self.assertFalse(
            any(isinstance(event, ToolStartedEvent) for event in events)
        )
        self.assertIsInstance(events[-1], RuntimeCompletedEvent)

    async def test_health_checks_do_not_require_agent_definition_read(self) -> None:
        for runtime, module, enabled_name in (
            (
                FoundryPromptRuntime(),
                "agent_memory_backend.foundry_prompt_runtime.get_settings",
                "foundry_prompt_enabled",
            ),
            (
                FoundryHostedMafRuntime(),
                "agent_memory_backend.foundry_hosted_maf_runtime.get_settings",
                "foundry_hosted_enabled",
            ),
        ):
            get_agent = AsyncMock(side_effect=AssertionError("unexpected definition read"))
            runtime._project = SimpleNamespace(
                agents=SimpleNamespace(get=get_agent)
            )
            runtime._openai = object()
            with patch(module, return_value=SimpleNamespace(**{enabled_name: True})):
                await runtime.health_check()
            get_agent.assert_not_awaited()

    async def test_prompt_state_uses_authenticated_user_header(self) -> None:
        conversations = SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="conversation-1"))
        )
        runtime = FoundryPromptRuntime()
        runtime._openai = SimpleNamespace(conversations=conversations)
        settings = SimpleNamespace(
            foundry_prompt_agent_name="prompt-agent",
            agent_release_id="release-1",
        )

        with patch("agent_memory_backend.foundry_prompt_runtime.get_settings", return_value=settings):
            state = await runtime.create_state(
                "application-1", "tenant:user", seed_messages=[]
            )

        self.assertEqual(state.foundry_conversation_id, "conversation-1")
        self.assertEqual(
            conversations.create.await_args.kwargs["extra_headers"],
            {"x-ms-user-identity": "tenant:user"},
        )

    async def test_prompt_runtime_emits_only_server_side_iq_tools(self) -> None:
        runtime = FoundryPromptRuntime()
        runtime._openai = object()
        state = _runtime_state(AgentType.FOUNDRY_PROMPT)
        response = SimpleNamespace(
            id="response-1",
            model_extra={},
            output=[
                SimpleNamespace(
                    type="mcp_call",
                    call_id="mcp-1",
                    name="knowledge_base_retrieve",
                    output={
                        "content": "grounded",
                        "citations": [
                            {
                                "ref_id": "returns-policy",
                                "source_name": "Returns policy",
                                "search_idx": 0,
                                "url": "https://example.test/returns",
                            }
                        ],
                    },
                ),
                SimpleNamespace(
                    type="function_call",
                    call_id="function-1",
                    name="get_order_status",
                    arguments='{"order_id":"ORD-001"}',
                ),
            ],
            usage=None,
        )

        async def fake_stream(*args, **kwargs):
            yield ("completed_response", response)

        settings = SimpleNamespace(
            foundry_prompt_agent_name="prompt-agent",
            agent_request_timeout_seconds=30,
        )
        context = TurnContext("application-1", "tenant:user", state)
        with (
            patch(
                "agent_memory_backend.foundry_prompt_runtime.stream_response",
                side_effect=fake_stream,
            ),
            patch(
                "agent_memory_backend.foundry_prompt_runtime.get_settings",
                return_value=settings,
            ),
        ):
            events = [event async for event in runtime.stream_turn("hello", context)]

        tool_events = [event for event in events if isinstance(event, ToolStartedEvent)]
        self.assertEqual([event.tool_name for event in tool_events], ["knowledge_base_retrieve"])
        result_events = [event for event in events if isinstance(event, ToolResultEvent)]
        self.assertEqual(
            result_events[0].result.citations[0].source_name, "Returns policy"
        )
        self.assertIsInstance(events[-1], RuntimeCompletedEvent)

    async def test_hosted_response_without_session_id_preserves_binding(self) -> None:
        runtime = FoundryHostedMafRuntime()
        runtime._openai = object()
        state = _runtime_state(
            AgentType.AGENT_FRAMEWORK, hosted_id="precreated-session"
        )
        response = SimpleNamespace(
            id="response-1", model_extra={}, output=[], usage=None
        )

        async def fake_stream(*args, **kwargs):
            self.assertEqual(
                kwargs["extra_headers"]["x-ms-user-identity"], "tenant:user"
            )
            self.assertEqual(
                kwargs["extra_body"]["agent_session_id"], "precreated-session"
            )
            yield ("completed_response", response)

        settings = SimpleNamespace(agent_request_timeout_seconds=30)
        context = TurnContext("application-1", "tenant:user", state)
        with (
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.stream_response",
                side_effect=fake_stream,
            ),
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.get_settings",
                return_value=settings,
            ),
        ):
            events = [event async for event in runtime.stream_turn("hello", context)]

        self.assertEqual(state.hosted_session_id, "precreated-session")
        self.assertEqual(state.last_response_id, "response-1")
        self.assertIsInstance(events[-1], RuntimeCompletedEvent)

    async def test_hosted_runtime_reports_framework_function_tools(self) -> None:
        runtime = FoundryHostedMafRuntime()
        runtime._openai = object()
        state = _runtime_state(
            AgentType.AGENT_FRAMEWORK, hosted_id="hosted-session"
        )
        response = SimpleNamespace(
            id="response-1",
            model_extra={},
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="function-1",
                    name="get_order_status",
                    arguments='{"order_id":"ORD-001"}',
                )
            ],
            usage=None,
        )

        async def fake_stream(*args, **kwargs):
            yield ("completed_response", response)

        settings = SimpleNamespace(agent_request_timeout_seconds=30)
        context = TurnContext("application-1", "tenant:user", state)
        with (
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.stream_response",
                side_effect=fake_stream,
            ),
            patch(
                "agent_memory_backend.foundry_hosted_maf_runtime.get_settings",
                return_value=settings,
            ),
        ):
            events = [event async for event in runtime.stream_turn("hello", context)]

        tool_events = [event for event in events if isinstance(event, ToolStartedEvent)]
        self.assertEqual([event.tool_name for event in tool_events], ["get_order_status"])


class AGUIAdapterTests(unittest.TestCase):
    def test_citations_use_custom_event_without_synthetic_tool(self) -> None:
        citation = Citation(
            ref_id="returns-policy",
            source_name="Returns policy",
            search_idx=0,
            url="https://example.test/returns",
        )

        events = tuple(to_agui_events(CitationsEvent((citation,))))

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], CustomEvent)
        self.assertEqual(events[0].name, "agent_citations")
        self.assertEqual(events[0].value[0]["ref_id"], "returns-policy")


class RoutingAndPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        server.conversation_registry.close()

    async def asyncTearDown(self) -> None:
        server.conversation_registry.close()

    async def test_existing_conversation_agent_is_immutable(self) -> None:
        state = _runtime_state(AgentType.FOUNDRY_PROMPT)

        class History:
            enabled = True

            async def get_conversation(self, session_id: str, user_id: str):
                return _document(state, user_id=user_id)

        coordinator = ConversationCoordinator(
            ConversationRegistry(),
            History(),
            SimpleNamespace(),
            {
                AgentType.FOUNDRY_PROMPT: SimpleNamespace(),
                AgentType.AGENT_FRAMEWORK: SimpleNamespace(),
            },
        )
        with self.assertRaises(HTTPException) as raised:
            await coordinator.prepare(
                conversation_id="conversation-1",
                agent_type=AgentType.AGENT_FRAMEWORK,
                user_id="tenant:user",
                initial_title="hello",
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail, "CONVERSATION_AGENT_IMMUTABLE"
        )

    async def test_failed_cosmos_create_cleans_remote_state(self) -> None:
        state = _runtime_state(AgentType.FOUNDRY_PROMPT)
        runtime = SimpleNamespace(
            create_state=AsyncMock(return_value=state),
            delete_state=AsyncMock(),
        )

        class History:
            enabled = True

            async def create_conversation(self, *args, **kwargs):
                raise RuntimeError("Cosmos unavailable")

        registry = ConversationRegistry()
        coordinator = ConversationCoordinator(
            registry,
            History(),
            SimpleNamespace(),
            {AgentType.FOUNDRY_PROMPT: runtime},
        )
        with self.assertRaisesRegex(RuntimeError, "Cosmos unavailable"):
            await coordinator.prepare(
                conversation_id=None,
                agent_type=AgentType.FOUNDRY_PROMPT,
                user_id="tenant:user",
                initial_title="hello",
            )

        runtime.delete_state.assert_awaited_once_with(state, "tenant:user")
        self.assertEqual(registry._conversations, {})

    async def test_chat_persists_backend_usage_tools_and_citations(self) -> None:
        state = _runtime_state(AgentType.AGENT_FRAMEWORK, hosted_id="hosted-session")
        conversation = LiveConversation(
            "conversation-1",
            user_id="tenant:user",
            title="Grounded question",
            agent_type=AgentType.AGENT_FRAMEWORK,
            runtime_state=state,
        )
        citation = Citation(
            ref_id="returns-policy",
            source_name="Returns policy",
            search_idx=0,
            url="https://example.test/returns",
        )

        class Runtime:
            async def stream_turn(self, message: str, context: TurnContext):
                yield ToolStartedEvent("tool-1", "knowledge_base_retrieve")
                yield ToolResultEvent(
                    "tool-1",
                    "tool-result-1",
                    ToolResultEnvelope(
                        status="ok",
                        data={},
                        citations=(citation,),
                    ),
                )
                yield TextDeltaEvent("message-1", "Grounded answer")
                yield CitationsEvent((citation,))
                yield UsageEvent(input_tokens=20, output_tokens=7, cached_tokens=4)
                yield RuntimeCompletedEvent("response-1")

        history = SimpleNamespace(append_messages=AsyncMock())
        original_history = server.history_store
        server.history_store = history
        request = server.ChatRequest(
            message="question",
            conversation_id="conversation-1",
            agent_type=AgentType.AGENT_FRAMEWORK,
        )
        try:
            with patch.object(
                server.conversation_coordinator,
                "prepare",
                new=AsyncMock(
                    return_value=PreparedConversation(conversation, Runtime())
                ),
            ):
                response = await server.chat(
                    request,
                    User("tenant:user", "User", "user@example.com", "U"),
                )
                async for _ in response.body_iterator:
                    pass
        finally:
            server.history_store = original_history

        records = history.append_messages.await_args.args[2]
        assistant = records[1]
        self.assertEqual(
            assistant["usage"],
            {"input_tokens": 20, "output_tokens": 7, "cached_tokens": 4},
        )
        self.assertEqual(
            assistant["tools"], ["knowledge_base_retrieve"]
        )
        self.assertEqual(
            assistant["citations"],
            [
                {
                    "ref_id": "returns-policy",
                    "source_name": "Returns policy",
                    "search_idx": 0,
                    "url": "https://example.test/returns",
                }
            ],
        )
        self.assertEqual(response.headers["x-conversation-id"], "conversation-1")
        self.assertFalse(
            server.conversation_registry._locks["conversation-1"].locked()
        )
        next_lease = await server.conversation_registry.acquire("conversation-1")
        try:
            self.assertIsNotNone(response.background)
            await response.background()
            self.assertTrue(
                server.conversation_registry._locks["conversation-1"].locked()
            )
        finally:
            await next_lease.release()

    async def test_chat_rejects_overlapping_turn_before_streaming(self) -> None:
        state = _runtime_state(AgentType.AGENT_FRAMEWORK)
        conversation = LiveConversation(
            "conversation-1",
            user_id="tenant:user",
            agent_type=AgentType.AGENT_FRAMEWORK,
            runtime_state=state,
        )
        lease = await server.conversation_registry.acquire("conversation-1")
        try:
            with patch.object(
                server.conversation_coordinator,
                "prepare",
                new=AsyncMock(
                    return_value=PreparedConversation(
                        conversation, SimpleNamespace()
                    )
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await server.chat(
                        server.ChatRequest(
                            message="question",
                            conversation_id="conversation-1",
                            agent_type=AgentType.AGENT_FRAMEWORK,
                        ),
                        User("tenant:user", "User", "user@example.com", "U"),
                    )
        finally:
            await lease.release()

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "CONVERSATION_BUSY")


class _EtagContainer:
    def __init__(self) -> None:
        self.document = {
            "id": "conversation-1",
            "user_id": "tenant:user",
            "title": "Before",
            "created_at": "now",
            "messages": [],
            "metadata": {},
            "_etag": "etag-1",
        }
        self.replace_kwargs = None

    async def read_item(self, item: str, partition_key: str) -> dict:
        return dict(self.document)

    async def replace_item(self, **kwargs) -> dict:
        self.replace_kwargs = kwargs
        return kwargs["body"]


class ConversationEtagTests(unittest.IsolatedAsyncioTestCase):
    async def test_updates_use_if_not_modified_etag(self) -> None:
        store = ConversationHistoryStore()
        container = _EtagContainer()
        store._container = container

        await store._replace_conversation(
            container.document,
            [{"role": "user", "content": "hello"}],
            expected_etag="etag-client",
        )

        self.assertEqual(container.replace_kwargs["etag"], "etag-client")
        self.assertEqual(
            container.replace_kwargs["match_condition"],
            MatchConditions.IfNotModified,
        )

    async def test_append_messages_stores_public_message_metadata(self) -> None:
        store = ConversationHistoryStore()
        container = _EtagContainer()
        store._container = container
        citation = {
            "ref_id": "returns-policy",
            "source_name": "Returns policy",
            "search_idx": 0,
            "url": "https://example.test/returns",
        }

        turn = TurnAccumulator(
            "Can I return this?",
            user_created_at="2026-07-12T10:00:00+00:00",
        )
        turn.consume(TextDeltaEvent("message-1", "Yes."))
        turn.consume(
            ToolStartedEvent("tool-1", "knowledge_base_retrieve")
        )
        turn.consume(
            UsageEvent(input_tokens=12, output_tokens=4, cached_tokens=2)
        )
        turn.consume(
            CitationsEvent(
                (
                    Citation(
                        ref_id=citation["ref_id"],
                        source_name=citation["source_name"],
                        search_idx=citation["search_idx"],
                        url=citation["url"],
                    ),
                )
            )
        )
        records = turn.message_records(
            assistant_created_at="2026-07-12T10:00:02+00:00",
        )
        await store.append_messages(
            "conversation-1",
            "tenant:user",
            records,
            _runtime_state(AgentType.FOUNDRY_PROMPT),
        )

        messages = container.replace_kwargs["body"]["messages"]
        self.assertEqual(
            messages[-2]["created_at"], "2026-07-12T10:00:00+00:00"
        )
        self.assertEqual(messages[-1]["usage"]["output_tokens"], 4)
        self.assertEqual(messages[-1]["tools"], ["knowledge_base_retrieve"])
        self.assertEqual(messages[-1]["citations"], [citation])


class _ProfileContainer:
    def __init__(self) -> None:
        self.document = {
            "id": "tenant:user",
            "user_id": "tenant:user",
            "version": 1,
            "basic_info": {},
            "interests": [],
            "habits": [],
            "preferences": {},
            "status": {},
            "facts": [],
            "source_conversations": [],
            "created_at": "now",
            "updated_at": "now",
            "_etag": "etag-1",
            "_rid": "private",
        }
        self.replace_calls = 0

    async def read_item(self, item: str, partition_key: str) -> dict:
        return dict(self.document)

    async def replace_item(self, **kwargs) -> dict:
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError

        self.replace_calls += 1
        if self.replace_calls == 1:
            self.document["facts"] = ["concurrent update"]
            self.document["_etag"] = "etag-2"
            raise CosmosAccessConditionFailedError(
                status_code=412, message="etag conflict"
            )
        self.document = {**kwargs["body"], "_etag": "etag-3"}
        return dict(self.document)


class ProfileConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_patch_retries_and_preserves_concurrent_fields(self) -> None:
        store = UserProfileMemoryStore()
        container = _ProfileContainer()
        store._container = container

        updated = await store.patch_profile(
            "tenant:user", {"preferences": {"theme": "dark"}}
        )

        self.assertEqual(container.replace_calls, 2)
        self.assertEqual(updated["facts"], ["concurrent update"])
        self.assertEqual(updated["preferences"], {"theme": "dark"})

    def test_public_profile_hides_cosmos_metadata(self) -> None:
        public = public_profile(_ProfileContainer().document)
        self.assertNotIn("user_id", public)
        self.assertNotIn("source_conversations", public)
        self.assertNotIn("_etag", public)
        self.assertNotIn("_rid", public)


class ConversationEvictionTests(unittest.IsolatedAsyncioTestCase):
    async def test_eviction_removes_unused_conversation_lock(self) -> None:
        registry = ConversationRegistry()
        registry._conversations = {
            "old": LiveConversation("old", "", last_activity=1),
            "new": LiveConversation("new", "", last_activity=2),
        }
        registry._locks["old"] = asyncio.Lock()

        with patch("agent_memory_backend.conversation_registry.MAX_CONVERSATIONS", 1):
            registry._evict_if_needed()

        self.assertNotIn("old", registry._conversations)
        self.assertNotIn("old", registry._locks)


if __name__ == "__main__":
    unittest.main()
