from __future__ import annotations

import unittest
import asyncio
from datetime import datetime, timedelta, timezone
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
    InnerStateStatus,
    InnerTurnOutcome,
    RuntimeCompletedEvent,
    RuntimeDescriptor,
    RuntimeState,
    TextDeltaEvent,
    ToolStartedEvent,
    ToolResultEnvelope,
    ToolResultEvent,
    TurnContext,
    UsageEvent,
    lookup_order_status,
)
from agent_memory_backend.agent_mcp import (
    AgentMcpTokenVerifier,
    application_tools_mcp,
)
from agent_memory_backend.agent_tool_gateway import (
    AgentStateRequest,
    AgentStateTurnRequest,
    AgentToolRequest,
    complete_agent_state_turn,
    dispatch_agent_tool,
    fail_agent_state_turn,
    resolve_agent_state,
)
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


def _hosted_runtime(
    agent_type: AgentType = AgentType.AGENT_FRAMEWORK,
    *,
    agent_name: str = "support-agent",
) -> FoundryHostedMafRuntime:
    return FoundryHostedMafRuntime(
        agent_type=agent_type,
        project_endpoint="https://example.services.ai.azure.com/api/projects/test",
        physical_agent_name=agent_name,
        physical_agent_endpoint=(
            "https://example.services.ai.azure.com/api/projects/test"
            f"/agents/{agent_name}/endpoint/protocols/openai"
        ),
        release_id="release-1",
        prompt_version="prompt-1",
        request_timeout_seconds=30,
    )


def _document(state: RuntimeState, *, user_id: str = "tenant:user") -> dict:
    return {
        "id": "conversation-1",
        "user_id": user_id,
        "messages": [],
        "metadata": {
            "schema_version": state.schema_version,
            "agent_type": state.descriptor.agent_type.value,
            "physical_agent_name": state.descriptor.physical_agent_name,
            "release_id": state.descriptor.release_id,
            "prompt_version": state.descriptor.prompt_version,
            "observed_agent_version": state.descriptor.observed_agent_version,
            "runtime_state": {
                "foundry_conversation_id": state.foundry_conversation_id,
                "hosted_session_id": state.hosted_session_id,
                "inner_model_conversation_id": state.inner_model_conversation_id,
                "inner_state_status": (
                    state.inner_state_status.value
                    if state.inner_state_status
                    else None
                ),
                "inner_pending_call_id": state.inner_pending_call_id,
                "inner_pending_started_at": state.inner_pending_started_at,
                "inner_pending_revision": state.inner_pending_revision,
                "inner_pending_outcome": (
                    state.inner_pending_outcome.value
                    if state.inner_pending_outcome
                    else None
                ),
                "inner_recovery_started_at": state.inner_recovery_started_at,
                "inner_last_completed_call_id": (
                    state.inner_last_completed_call_id
                ),
                "inner_last_failed_call_id": state.inner_last_failed_call_id,
                "inner_state_revision": state.inner_state_revision,
                "last_response_id": state.last_response_id,
            },
        },
    }


class AgentGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_resolver_returns_only_bound_directive_state(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.BOOTSTRAP_REQUIRED

        class History:
            bind_runtime_state = AsyncMock()

            async def get_by_hosted_session(self, user_id: str, session_id: str):
                document = _document(state, user_id=user_id)
                document["_etag"] = "etag-1"
                return document

        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=SimpleNamespace(
                directive_hosted_agent_principal_ids=("directive-principal",)
            ),
        ):
            result = await resolve_agent_state(
                AgentStateRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    outer_foundry_conversation_id="foundry-conversation",
                ),
                AgentCaller("directive-principal", "tenant"),
                History(),
            )

        self.assertEqual(result["inner_model_conversation_id"], "conv_inner")
        self.assertTrue(result["bootstrap_required"])
        self.assertEqual(result["revision"], 1)
        self.assertNotIn("hosted_session_id", result)
        History.bind_runtime_state.assert_awaited_once()

    async def test_state_resolver_rejects_cross_conversation_binding(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )

        class History:
            async def get_by_hosted_session(self, user_id: str, session_id: str):
                return _document(state, user_id=user_id)

        with self.assertRaises(HTTPException) as raised:
            await resolve_agent_state(
                AgentStateRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    outer_foundry_conversation_id="another-conversation",
                ),
                AgentCaller("directive-principal", "tenant"),
                History(),
            )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_completion_waits_for_atomic_transcript_commit(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.BOOTSTRAP_REQUIRED
        state.inner_pending_call_id = "call-1"
        state.inner_pending_revision = 1
        state.inner_state_revision = 1
        document = _document(state, user_id="tenant:user")
        document["_etag"] = "etag-1"
        history = SimpleNamespace(
            get_by_hosted_session=AsyncMock(return_value=document),
            bind_runtime_state=AsyncMock(),
        )

        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=SimpleNamespace(
                directive_hosted_agent_principal_ids=("directive-principal",)
            ),
        ):
            result = await complete_agent_state_turn(
                AgentStateTurnRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    outer_foundry_conversation_id="foundry-conversation",
                    revision=1,
                ),
                AgentCaller("directive-principal", "tenant"),
                history,
            )

        self.assertEqual(result, {"status": "completed"})
        persisted_state = history.bind_runtime_state.await_args.args[2]
        self.assertIs(
            persisted_state.inner_state_status,
            InnerStateStatus.BOOTSTRAP_REQUIRED,
        )
        self.assertEqual(persisted_state.inner_pending_call_id, "call-1")
        self.assertIs(
            persisted_state.inner_pending_outcome,
            InnerTurnOutcome.COMPLETED,
        )
        self.assertEqual(
            history.bind_runtime_state.await_args.kwargs["expected_etag"],
            "etag-1",
        )

    async def test_state_resolver_retry_reuses_same_fenced_lease(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.READY
        state.inner_pending_call_id = "call-1"
        state.inner_pending_started_at = datetime.now(timezone.utc).isoformat()
        state.inner_pending_revision = 4
        state.inner_state_revision = 4
        history = SimpleNamespace(
            get_by_hosted_session=AsyncMock(
                return_value=_document(state, user_id="tenant:user")
            ),
            bind_runtime_state=AsyncMock(),
        )

        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=SimpleNamespace(
                directive_hosted_agent_principal_ids=("directive-principal",)
            ),
        ):
            result = await resolve_agent_state(
                AgentStateRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    outer_foundry_conversation_id="foundry-conversation",
                ),
                AgentCaller("directive-principal", "tenant"),
                history,
            )

        self.assertEqual(result["revision"], 4)
        history.bind_runtime_state.assert_not_awaited()

    async def test_completion_callback_retry_is_idempotent(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.READY
        state.inner_pending_call_id = "call-1"
        state.inner_pending_revision = 4
        state.inner_pending_outcome = InnerTurnOutcome.COMPLETED
        state.inner_state_revision = 5
        history = SimpleNamespace(
            get_by_hosted_session=AsyncMock(
                return_value=_document(state, user_id="tenant:user")
            ),
            bind_runtime_state=AsyncMock(),
        )

        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=SimpleNamespace(
                directive_hosted_agent_principal_ids=("directive-principal",)
            ),
        ):
            result = await complete_agent_state_turn(
                AgentStateTurnRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    outer_foundry_conversation_id="foundry-conversation",
                    revision=4,
                ),
                AgentCaller("directive-principal", "tenant"),
                history,
            )

        self.assertEqual(result, {"status": "completed"})
        history.bind_runtime_state.assert_not_awaited()

    async def test_terminal_callback_rejects_stale_fencing_revision(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.READY
        state.inner_pending_call_id = "call-1"
        state.inner_pending_revision = 4
        state.inner_state_revision = 4
        history = SimpleNamespace(
            get_by_hosted_session=AsyncMock(
                return_value=_document(state, user_id="tenant:user")
            ),
            bind_runtime_state=AsyncMock(),
        )

        with (
            patch(
                "agent_memory_backend.agent_tool_gateway.get_settings",
                return_value=SimpleNamespace(
                    directive_hosted_agent_principal_ids=("directive-principal",)
                ),
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            await complete_agent_state_turn(
                AgentStateTurnRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    outer_foundry_conversation_id="foundry-conversation",
                    revision=3,
                ),
                AgentCaller("directive-principal", "tenant"),
                history,
            )

        self.assertEqual(raised.exception.status_code, 409)
        history.bind_runtime_state.assert_not_awaited()

    async def test_failed_turn_requires_fresh_inner_state(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.BOOTSTRAP_REQUIRED
        state.inner_pending_call_id = "call-1"
        state.inner_pending_started_at = datetime.now(timezone.utc).isoformat()
        state.inner_pending_revision = 2
        state.inner_state_revision = 2
        document = _document(state, user_id="tenant:user")
        document["_etag"] = "etag-1"
        history = SimpleNamespace(
            get_by_hosted_session=AsyncMock(return_value=document),
            bind_runtime_state=AsyncMock(),
        )

        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=SimpleNamespace(
                directive_hosted_agent_principal_ids=("directive-principal",)
            ),
        ):
            result = await fail_agent_state_turn(
                AgentStateTurnRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-1",
                    outer_foundry_conversation_id="foundry-conversation",
                    revision=2,
                ),
                AgentCaller("directive-principal", "tenant"),
                history,
            )

        self.assertEqual(result, {"status": "failed"})
        persisted = history.bind_runtime_state.await_args.args[2]
        self.assertIs(
            persisted.inner_state_status,
            InnerStateStatus.RECOVERY_REQUIRED,
        )
        self.assertIsNone(persisted.inner_pending_call_id)
        self.assertEqual(persisted.inner_last_failed_call_id, "call-1")

    async def test_stale_lease_is_recovered_instead_of_taken_over(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session-1",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.READY
        state.inner_pending_call_id = "call-old"
        state.inner_pending_started_at = (
            datetime.now(timezone.utc) - timedelta(minutes=11)
        ).isoformat()
        state.inner_pending_revision = 2
        state.inner_state_revision = 2
        document = _document(state, user_id="tenant:user")
        document["_etag"] = "etag-1"
        history = SimpleNamespace(
            get_by_hosted_session=AsyncMock(return_value=document),
            bind_runtime_state=AsyncMock(),
        )

        with (
            patch(
                "agent_memory_backend.agent_tool_gateway.get_settings",
                return_value=SimpleNamespace(
                    directive_hosted_agent_principal_ids=("directive-principal",)
                ),
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            await resolve_agent_state(
                AgentStateRequest(
                    user_id="tenant:user",
                    session_id="hosted-session-1",
                    call_id="call-new",
                    outer_foundry_conversation_id="foundry-conversation",
                ),
                AgentCaller("directive-principal", "tenant"),
                history,
            )

        self.assertEqual(raised.exception.status_code, 409)
        persisted = history.bind_runtime_state.await_args.args[2]
        self.assertIs(
            persisted.inner_state_status,
            InnerStateStatus.RECOVERY_REQUIRED,
        )
        self.assertEqual(persisted.inner_last_failed_call_id, "call-old")
        self.assertIsNone(persisted.inner_pending_call_id)

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
        with patch(
            "agent_memory_backend.agent_tool_gateway.get_settings",
            return_value=SimpleNamespace(
                support_hosted_agent_principal_ids=("hosted-principal",),
                directive_hosted_agent_principal_ids=(),
            ),
        ):
            result = await dispatch_agent_tool(
                "get_order_status",
                request,
                AgentCaller(
                    principal_id="hosted-principal",
                    tenant_id="tenant",
                ),
                history,
                {AgentType.AGENT_FRAMEWORK: Executor()},
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
                {AgentType.AGENT_FRAMEWORK: ToolExecutor(None, None)},
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

    def test_chat_request_bounds_conversation_identifier(self) -> None:
        for conversation_id in ("x" * 129, "../conversation"):
            with self.subTest(conversation_id=conversation_id):
                with self.assertRaises(ValidationError):
                    server.ChatRequest(
                        message="hello",
                        conversation_id=conversation_id,
                        agent_type=AgentType.FOUNDRY_PROMPT,
                    )


class AgentMcpTests(unittest.IsolatedAsyncioTestCase):
    def test_shared_order_lookup_is_normalized_and_authoritative(self) -> None:
        self.assertEqual(
            lookup_order_status(" ord-003 "),
            {
                "order_id": "ORD-003",
                "status": "delivered",
                "trackingNumber": "Not yet assigned",
                "eta": "Delivered Jan 20, 2026",
                "currentStepIcon": "check_circle",
            },
        )

    async def test_mcp_exposes_only_stateless_order_lookup(self) -> None:
        tools = await application_tools_mcp.list_tools()

        self.assertEqual([tool.name for tool in tools], ["get_order_status"])
        self.assertTrue(application_tools_mcp.settings.stateless_http)
        self.assertTrue(application_tools_mcp.settings.json_response)

    async def test_mcp_token_verifier_reuses_gateway_policy(self) -> None:
        settings = SimpleNamespace(
            agent_gateway_required_role="AgentTools.Invoke"
        )
        with (
            patch(
                "agent_memory_backend.agent_mcp.validate_agent_token",
                return_value=AgentCaller(
                    principal_id="hosted-agent", tenant_id="tenant"
                ),
            ) as validate,
            patch(
                "agent_memory_backend.agent_mcp.get_settings",
                return_value=settings,
            ),
        ):
            access = await AgentMcpTokenVerifier().verify_token("token")

        validate.assert_called_once_with("Bearer token")
        self.assertIsNotNone(access)
        self.assertEqual(access.client_id, "hosted-agent")
        self.assertEqual(access.scopes, ["AgentTools.Invoke"])

    async def test_mcp_token_verifier_rejects_invalid_tokens(self) -> None:
        with patch(
            "agent_memory_backend.agent_mcp.validate_agent_token",
            side_effect=HTTPException(status_code=403),
        ):
            access = await AgentMcpTokenVerifier().verify_token("invalid")

        self.assertIsNone(access)


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
    async def test_directive_state_deletes_inner_before_outer_and_session(self) -> None:
        order = []
        inner_conversations = SimpleNamespace(
            delete=AsyncMock(
                side_effect=lambda **kwargs: order.append(
                    ("conversation", kwargs["conversation_id"])
                )
            )
        )
        outer_conversations = SimpleNamespace(
            delete=AsyncMock(
                side_effect=lambda **kwargs: order.append(
                    ("conversation", kwargs["conversation_id"])
                )
            )
        )
        agents = SimpleNamespace(
            delete_session=AsyncMock(
                side_effect=lambda **kwargs: order.append(
                    ("session", kwargs["session_id"])
                )
            )
        )
        runtime = _hosted_runtime(
            AgentType.DIRECTIVE_RAG,
            agent_name="directive-agent",
        )
        runtime._openai = SimpleNamespace(conversations=outer_conversations)
        runtime._model_openai = SimpleNamespace(
            conversations=inner_conversations
        )
        runtime._project = SimpleNamespace(agents=agents)
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            foundry_id="outer-conversation",
            hosted_id="hosted-session",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.schema_version = 7

        await runtime.delete_state(state, "tenant:user")

        self.assertEqual(
            order,
            [
                ("conversation", "conv_inner"),
                ("conversation", "outer-conversation"),
                ("session", "hosted-session"),
            ],
        )

    async def test_legacy_directive_state_deletes_inner_through_physical_endpoint(
        self,
    ) -> None:
        physical_conversations = SimpleNamespace(delete=AsyncMock())
        model_conversations = SimpleNamespace(delete=AsyncMock())
        runtime = _hosted_runtime(
            AgentType.DIRECTIVE_RAG,
            agent_name="directive-agent",
        )
        runtime._openai = SimpleNamespace(
            conversations=physical_conversations
        )
        runtime._model_openai = SimpleNamespace(
            conversations=model_conversations
        )
        runtime._project = SimpleNamespace(
            agents=SimpleNamespace(delete_session=AsyncMock())
        )
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            foundry_id="outer-conversation",
        )
        state.inner_model_conversation_id = "legacy-inner"
        state.schema_version = 6

        await runtime.delete_state(state, "tenant:user")

        self.assertEqual(physical_conversations.delete.await_count, 2)
        self.assertEqual(
            physical_conversations.delete.await_args_list[0].kwargs[
                "conversation_id"
            ],
            "legacy-inner",
        )
        model_conversations.delete.assert_not_awaited()

    async def test_directive_state_deletion_tolerates_absent_resources(self) -> None:
        class NotFoundError(Exception):
            status_code = 404

        inner_conversations = SimpleNamespace(
            delete=AsyncMock(side_effect=NotFoundError())
        )
        outer_conversations = SimpleNamespace(
            delete=AsyncMock()
        )
        agents = SimpleNamespace(
            delete_session=AsyncMock(side_effect=NotFoundError())
        )
        runtime = _hosted_runtime(
            AgentType.DIRECTIVE_RAG,
            agent_name="directive-agent",
        )
        runtime._openai = SimpleNamespace(conversations=outer_conversations)
        runtime._model_openai = SimpleNamespace(
            conversations=inner_conversations
        )
        runtime._project = SimpleNamespace(agents=agents)
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            foundry_id="outer-conversation",
            hosted_id="hosted-session",
        )
        state.inner_model_conversation_id = "conv_inner"

        await runtime.delete_state(state, "tenant:user")

        inner_conversations.delete.assert_awaited_once()
        outer_conversations.delete.assert_awaited_once()
        agents.delete_session.assert_awaited_once()

    async def test_directive_creation_cleans_partial_remote_state(self) -> None:
        outer_conversations = SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(id="outer-conversation")
            ),
            delete=AsyncMock(),
        )
        inner_conversations = SimpleNamespace(
            create=AsyncMock(side_effect=RuntimeError("inner creation failed"))
        )
        agents = SimpleNamespace(
            create_session=AsyncMock(
                return_value=SimpleNamespace(agent_session_id="hosted-session")
            ),
            delete_session=AsyncMock(),
        )
        runtime = _hosted_runtime(
            AgentType.DIRECTIVE_RAG,
            agent_name="directive-agent",
        )
        runtime._openai = SimpleNamespace(conversations=outer_conversations)
        runtime._model_openai = SimpleNamespace(
            conversations=inner_conversations
        )
        runtime._project = SimpleNamespace(agents=agents)

        with self.assertRaisesRegex(RuntimeError, "inner creation failed"):
            await runtime.create_state("application-1", "tenant:user")

        outer_conversations.delete.assert_awaited_once()
        self.assertEqual(
            outer_conversations.delete.await_args.kwargs["conversation_id"],
            "outer-conversation",
        )
        agents.delete_session.assert_awaited_once()

    async def test_directive_recovery_replaces_legacy_inner_state(self) -> None:
        model_conversations = SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="conv_replacement")),
            delete=AsyncMock(),
        )
        physical_conversations = SimpleNamespace(delete=AsyncMock())
        runtime = _hosted_runtime(
            AgentType.DIRECTIVE_RAG,
            agent_name="directive-agent",
        )
        runtime._openai = SimpleNamespace(
            conversations=physical_conversations
        )
        runtime._model_openai = SimpleNamespace(
            conversations=model_conversations
        )
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        state.inner_model_conversation_id = "conv_uncertain"
        state.inner_state_status = InnerStateStatus.RECOVERY_REQUIRED
        state.inner_pending_call_id = "call-1"
        state.inner_pending_revision = 4
        state.inner_state_revision = 4
        state.schema_version = 6

        replacement_id = await runtime.recover_inner_state(
            state,
            "tenant:user",
            seed_messages=[
                {"role": "user", "content": "Committed question"},
                {"role": "assistant", "content": "Committed answer"},
            ],
        )

        self.assertEqual(replacement_id, "conv_replacement")
        self.assertEqual(state.inner_model_conversation_id, replacement_id)
        self.assertIs(state.inner_state_status, InnerStateStatus.READY)
        model_conversations.create.assert_awaited_once_with(
            items=[
                {"role": "user", "content": "Committed question"},
                {"role": "assistant", "content": "Committed answer"},
            ],
            extra_headers={
                "Foundry-Features": "HostedAgents=V1Preview",
                "x-ms-user-identity": "tenant:user",
            },
        )
        self.assertIsNone(state.inner_pending_call_id)
        self.assertEqual(state.inner_last_failed_call_id, "call-1")
        self.assertEqual(state.inner_state_revision, 5)
        physical_conversations.delete.assert_awaited_once()
        self.assertEqual(
            physical_conversations.delete.await_args.kwargs["conversation_id"],
            "conv_uncertain",
        )
        model_conversations.delete.assert_not_awaited()
        self.assertEqual(state.schema_version, 7)

    async def test_directive_recovery_replaces_project_inner_state(self) -> None:
        model_conversations = SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="conv_replacement")),
            delete=AsyncMock(),
        )
        physical_conversations = SimpleNamespace(delete=AsyncMock())
        runtime = _hosted_runtime(
            AgentType.DIRECTIVE_RAG,
            agent_name="directive-agent",
        )
        runtime._openai = SimpleNamespace(
            conversations=physical_conversations
        )
        runtime._model_openai = SimpleNamespace(
            conversations=model_conversations
        )
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        state.inner_model_conversation_id = "conv_project"
        state.inner_state_status = InnerStateStatus.RECOVERY_REQUIRED
        state.schema_version = 7

        await runtime.recover_inner_state(
            state,
            "tenant:user",
            seed_messages=[],
        )

        model_conversations.delete.assert_awaited_once()
        self.assertEqual(
            model_conversations.delete.await_args.kwargs["conversation_id"],
            "conv_project",
        )
        physical_conversations.delete.assert_not_awaited()

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
        prompt_runtime = FoundryPromptRuntime()
        hosted_runtime = _hosted_runtime()
        for runtime in (prompt_runtime, hosted_runtime):
            get_agent = AsyncMock(side_effect=AssertionError("unexpected definition read"))
            runtime._project = SimpleNamespace(
                agents=SimpleNamespace(get=get_agent)
            )
            runtime._openai = object()
            if isinstance(runtime, FoundryHostedMafRuntime):
                runtime._endpoint_verified = True
                await runtime.health_check()
            else:
                with patch(
                    "agent_memory_backend.foundry_prompt_runtime.get_settings",
                    return_value=SimpleNamespace(foundry_prompt_enabled=True),
                ):
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
        runtime = _hosted_runtime()
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

        context = TurnContext("application-1", "tenant:user", state)
        with patch(
            "agent_memory_backend.foundry_hosted_maf_runtime.stream_response",
            side_effect=fake_stream,
        ):
            events = [event async for event in runtime.stream_turn("hello", context)]

        self.assertEqual(state.hosted_session_id, "precreated-session")
        self.assertEqual(state.last_response_id, "response-1")
        self.assertIsInstance(events[-1], RuntimeCompletedEvent)

    async def test_hosted_runtime_reports_framework_function_tools(self) -> None:
        runtime = _hosted_runtime()
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

        context = TurnContext("application-1", "tenant:user", state)
        with patch(
            "agent_memory_backend.foundry_hosted_maf_runtime.stream_response",
            side_effect=fake_stream,
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

        authoritative = tuple(
            to_agui_events(
                CitationsEvent((citation,), authoritative=True)
            )
        )[0]
        self.assertTrue(authoritative.value["authoritative"])
        self.assertEqual(
            authoritative.value["citations"][0]["ref_id"],
            "returns-policy",
        )


class RoutingAndPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        server.conversation_registry.close()

    async def asyncTearDown(self) -> None:
        server.conversation_registry.close()

    async def test_legacy_directive_state_allocates_inner_conversation(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        document = _document(state, user_id="tenant:user")
        document["_etag"] = "etag-1"
        document["messages"] = [{"role": "user", "content": "previous"}]

        async def allocate(runtime_state, user_id, *, bootstrap_required):
            self.assertEqual(user_id, "tenant:user")
            self.assertTrue(bootstrap_required)
            runtime_state.inner_model_conversation_id = "conv_inner"
            runtime_state.inner_state_status = InnerStateStatus.BOOTSTRAP_REQUIRED
            return "conv_inner"

        runtime = SimpleNamespace(
            allocate_inner_state=AsyncMock(side_effect=allocate),
            delete_inner_state=AsyncMock(),
        )
        history = SimpleNamespace(bind_runtime_state=AsyncMock())
        coordinator = ConversationCoordinator(
            ConversationRegistry(),
            history,
            SimpleNamespace(),
            {AgentType.DIRECTIVE_RAG: runtime},
        )

        restored, selected_runtime = await coordinator._restore_runtime(
            document,
            "conversation-1",
            "tenant:user",
            AgentType.DIRECTIVE_RAG,
        )

        self.assertIs(selected_runtime, runtime)
        self.assertEqual(restored.inner_model_conversation_id, "conv_inner")
        history.bind_runtime_state.assert_awaited_once()
        runtime.delete_inner_state.assert_not_awaited()

    async def test_recovery_rotates_inner_state_before_retry(self) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        state.inner_model_conversation_id = "conv_uncertain"
        state.inner_state_status = InnerStateStatus.RECOVERY_REQUIRED
        state.inner_last_failed_call_id = "call-1"
        document = _document(state, user_id="tenant:user")
        document["_etag"] = "etag-2"
        document["messages"] = [{"role": "user", "content": "previous"}]

        async def recover(runtime_state, user_id, *, seed_messages):
            order.append("recover")
            self.assertEqual(user_id, "tenant:user")
            self.assertEqual(
                seed_messages,
                [{"role": "user", "content": "previous"}],
            )
            runtime_state.inner_model_conversation_id = "conv_replacement"
            runtime_state.inner_state_status = InnerStateStatus.READY
            runtime_state.inner_recovery_started_at = None
            runtime_state.inner_state_revision += 1
            return "conv_replacement"

        runtime = SimpleNamespace(
            recover_inner_state=AsyncMock(side_effect=recover),
            delete_inner_state=AsyncMock(),
        )
        etags = iter(("etag-claim", "etag-final"))
        order: list[str] = []

        async def bind_runtime_state(
            conversation_id,
            user_id,
            runtime_state,
            *,
            expected_etag,
        ):
            order.append(f"bind:{runtime_state.inner_state_status.value}")
            self.assertEqual(conversation_id, "conversation-1")
            self.assertEqual(user_id, "tenant:user")
            persisted = _document(runtime_state, user_id=user_id)
            persisted["messages"] = list(document["messages"])
            persisted["_etag"] = next(etags)
            return persisted

        history = SimpleNamespace(
            bind_runtime_state=AsyncMock(side_effect=bind_runtime_state),
        )
        coordinator = ConversationCoordinator(
            ConversationRegistry(),
            history,
            SimpleNamespace(),
            {AgentType.DIRECTIVE_RAG: runtime},
        )

        restored, _ = await coordinator._restore_runtime(
            document,
            "conversation-1",
            "tenant:user",
            AgentType.DIRECTIVE_RAG,
        )

        self.assertEqual(
            restored.inner_model_conversation_id,
            "conv_replacement",
        )
        self.assertIs(restored.inner_state_status, InnerStateStatus.READY)
        self.assertEqual(history.bind_runtime_state.await_count, 2)
        self.assertEqual(
            order,
            ["bind:recovering", "recover", "bind:ready"],
        )
        claimed_state = history.bind_runtime_state.await_args_list[0].args[2]
        self.assertIs(
            claimed_state.inner_state_status,
            InnerStateStatus.RECOVERING,
        )
        self.assertIsNotNone(claimed_state.inner_recovery_started_at)
        runtime.delete_inner_state.assert_not_awaited()

    async def test_transcript_commit_winning_recovery_cas_keeps_inner_state(
        self,
    ) -> None:
        class PreconditionFailed(Exception):
            status_code = 412

        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        state.inner_model_conversation_id = "conv_inner"
        state.inner_state_status = InnerStateStatus.READY
        state.inner_pending_call_id = "call-1"
        state.inner_pending_revision = 1
        state.inner_pending_outcome = InnerTurnOutcome.COMPLETED
        document = _document(state, user_id="tenant:user")
        document["_etag"] = "etag-completed"

        winner_state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        winner_state.inner_model_conversation_id = "conv_inner"
        winner_state.inner_state_status = InnerStateStatus.READY
        winner_state.inner_last_completed_call_id = "call-1"
        winner = _document(winner_state, user_id="tenant:user")
        winner["_etag"] = "etag-transcript"
        runtime = SimpleNamespace(
            recover_inner_state=AsyncMock(),
            delete_inner_state=AsyncMock(),
        )
        history = SimpleNamespace(
            bind_runtime_state=AsyncMock(side_effect=PreconditionFailed()),
            get_conversation=AsyncMock(return_value=winner),
        )
        coordinator = ConversationCoordinator(
            ConversationRegistry(),
            history,
            SimpleNamespace(),
            {AgentType.DIRECTIVE_RAG: runtime},
        )

        restored, _ = await coordinator._restore_runtime(
            document,
            "conversation-1",
            "tenant:user",
            AgentType.DIRECTIVE_RAG,
        )

        self.assertEqual(restored.inner_last_completed_call_id, "call-1")
        runtime.recover_inner_state.assert_not_awaited()
        runtime.delete_inner_state.assert_not_awaited()

    async def test_lost_final_cas_response_preserves_committed_replacement(
        self,
    ) -> None:
        state = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        state.inner_model_conversation_id = "conv_uncertain"
        state.inner_state_status = InnerStateStatus.RECOVERY_REQUIRED
        document = _document(state, user_id="tenant:user")
        document["_etag"] = "etag-recovery"
        document["messages"] = [{"role": "user", "content": "committed"}]

        async def recover(runtime_state, user_id, *, seed_messages):
            self.assertEqual(user_id, "tenant:user")
            self.assertEqual(
                seed_messages,
                [{"role": "user", "content": "committed"}],
            )
            runtime_state.inner_model_conversation_id = "conv_replacement"
            runtime_state.inner_state_status = InnerStateStatus.READY
            runtime_state.inner_recovery_started_at = None
            runtime_state.inner_state_revision += 1
            return "conv_replacement"

        runtime = SimpleNamespace(
            recover_inner_state=AsyncMock(side_effect=recover),
            delete_inner_state=AsyncMock(),
        )

        class History:
            def __init__(self):
                self.calls = 0
                self.current = document

            async def bind_runtime_state(
                history_self,
                conversation_id,
                user_id,
                runtime_state,
                *,
                expected_etag,
            ):
                history_self.calls += 1
                persisted = _document(runtime_state, user_id=user_id)
                persisted["messages"] = list(document["messages"])
                if history_self.calls == 1:
                    persisted["_etag"] = "etag-claim"
                    history_self.current = persisted
                    return persisted
                persisted["_etag"] = "etag-final"
                history_self.current = persisted
                raise RuntimeError("Cosmos response lost")

            async def get_conversation(history_self, conversation_id, user_id):
                return history_self.current

        history = History()
        coordinator = ConversationCoordinator(
            ConversationRegistry(),
            history,
            SimpleNamespace(),
            {AgentType.DIRECTIVE_RAG: runtime},
        )

        restored, _ = await coordinator._restore_runtime(
            document,
            "conversation-1",
            "tenant:user",
            AgentType.DIRECTIVE_RAG,
        )

        self.assertEqual(
            restored.inner_model_conversation_id,
            "conv_replacement",
        )
        self.assertIs(restored.inner_state_status, InnerStateStatus.READY)
        runtime.delete_inner_state.assert_not_awaited()

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

    async def test_prepare_failure_releases_existing_conversation_lock(self) -> None:
        registry = ConversationRegistry()
        coordinator = SimpleNamespace(
            prepare=AsyncMock(side_effect=RuntimeError("restore failed"))
        )
        service = server.ChatTurnService(
            coordinator,
            registry,
            SimpleNamespace(),
        )

        for index in range(100):
            with self.assertRaisesRegex(RuntimeError, "restore failed"):
                await service.create_response(
                    message="question",
                    conversation_id=f"conversation-{index}",
                    agent_type=AgentType.DIRECTIVE_RAG,
                    user_id="tenant:user",
                )

        self.assertEqual(registry._locks, {})
        lease = await registry.acquire("conversation-retry")
        await lease.release()


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

    async def test_directive_append_atomically_finalizes_latest_lifecycle(
        self,
    ) -> None:
        store = ConversationHistoryStore()
        container = _EtagContainer()
        persisted = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        persisted.inner_model_conversation_id = "conv_inner"
        persisted.inner_state_status = InnerStateStatus.BOOTSTRAP_REQUIRED
        persisted.inner_pending_call_id = "call-1"
        persisted.inner_pending_started_at = datetime.now(timezone.utc).isoformat()
        persisted.inner_pending_revision = 1
        persisted.inner_pending_outcome = InnerTurnOutcome.COMPLETED
        persisted.inner_state_revision = 2
        container.document = _document(persisted)
        container.document["_etag"] = "etag-2"
        store._container = container

        observed = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        observed.inner_model_conversation_id = "conv_inner"
        observed.inner_state_status = InnerStateStatus.BOOTSTRAP_REQUIRED
        observed.last_response_id = "response-1"
        await store.append_messages(
            "conversation-1",
            "tenant:user",
            [
                {"role": "user", "content": "Question"},
                {"role": "assistant", "content": "Answer"},
            ],
            observed,
        )

        private = container.replace_kwargs["body"]["metadata"]["runtime_state"]
        self.assertEqual(private["inner_state_status"], "ready")
        self.assertIsNone(private["inner_pending_call_id"])
        self.assertEqual(private["inner_last_completed_call_id"], "call-1")
        self.assertEqual(private["inner_state_revision"], 3)
        self.assertEqual(private["last_response_id"], "response-1")
        self.assertIs(observed.inner_state_status, InnerStateStatus.READY)
        self.assertEqual(observed.inner_last_completed_call_id, "call-1")

    async def test_directive_append_cannot_finalize_recovery_claim(self) -> None:
        store = ConversationHistoryStore()
        container = _EtagContainer()
        persisted = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        persisted.inner_model_conversation_id = "conv_inner"
        persisted.inner_state_status = InnerStateStatus.RECOVERING
        persisted.inner_pending_call_id = "call-1"
        persisted.inner_pending_revision = 1
        persisted.inner_pending_outcome = InnerTurnOutcome.COMPLETED
        persisted.inner_recovery_started_at = datetime.now(timezone.utc).isoformat()
        container.document = _document(persisted)
        container.document["_etag"] = "etag-recovery"
        store._container = container

        observed = _runtime_state(
            AgentType.DIRECTIVE_RAG,
            hosted_id="hosted-session",
        )
        observed.inner_model_conversation_id = "conv_inner"
        with self.assertRaisesRegex(
            RuntimeError,
            "completion is not durable",
        ):
            await store.append_messages(
                "conversation-1",
                "tenant:user",
                [{"role": "assistant", "content": "Answer"}],
                observed,
            )

        self.assertIsNone(container.replace_kwargs)


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
