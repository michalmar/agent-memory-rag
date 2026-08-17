from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from maf_hosting import gateway


class GatewayInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await gateway.close_gateway_transport()

    async def asyncTearDown(self) -> None:
        await gateway.close_gateway_transport()

    def _request_context(self) -> SimpleNamespace:
        return SimpleNamespace(
            user_id="tenant:user",
            session_id="session-1",
            call_id="call-1",
        )

    def _response(self, payload: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: payload,
        )

    def _client_factory(
        self,
        *responses: SimpleNamespace,
    ) -> tuple[Mock, list[SimpleNamespace]]:
        created_clients: list[SimpleNamespace] = []

        def build_client(*args, **kwargs):
            del args, kwargs
            client = SimpleNamespace(
                post=AsyncMock(side_effect=list(responses)),
                aclose=AsyncMock(),
            )
            created_clients.append(client)
            return client

        return Mock(side_effect=build_client), created_clients

    async def _invoke(
        self,
        *,
        environment: dict[str, str],
        client_factory: Mock,
        **timeout_options,
    ):
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                gateway,
                "get_request_context",
                return_value=self._request_context(),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
            patch.object(
                gateway.httpx,
                "AsyncClient",
                client_factory,
            ),
        ):
            result = await gateway.invoke_gateway_tool(
                "get_user_context",
                {"query": "remember"},
                **timeout_options,
            )

        return result

    async def test_injects_request_context_and_bearer_token(self) -> None:
        client_factory, clients = self._client_factory(
            self._response({"status": "ok", "data": {}}),
        )
        result = await self._invoke(
            environment={
                "APP_TOOL_GATEWAY_URL": "https://frontend.example/api/",
                "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
            },
            client_factory=client_factory,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(client_factory.call_count, 1)
        request = clients[0].post.await_args
        self.assertEqual(
            request.args[0],
            "https://frontend.example/api/internal/agent-tools/get_user_context",
        )
        self.assertEqual(
            request.kwargs["headers"],
            {"Authorization": "Bearer token"},
        )
        self.assertEqual(
            request.kwargs["json"],
            {
                "user_id": "tenant:user",
                "session_id": "session-1",
                "call_id": "call-1",
                "arguments": {"query": "remember"},
            },
        )

    async def test_reuses_one_pooled_client_for_tool_and_state_calls(self) -> None:
        client_factory, clients = self._client_factory(
            self._response({"status": "ok", "data": {}}),
            self._response(
                {
                    "inner_model_conversation_id": "conv_inner",
                    "bootstrap_required": False,
                    "release_id": "release-1",
                    "revision": 1,
                }
            ),
        )
        with (
            patch.dict(
                os.environ,
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                },
                clear=True,
            ),
            patch.object(
                gateway,
                "get_request_context",
                return_value=self._request_context(),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
            patch.object(
                gateway.httpx,
                "AsyncClient",
                client_factory,
            ),
        ):
            await gateway.invoke_gateway_tool("get_user_context", {"query": "remember"})
            state = await gateway.resolve_agent_state("outer-foundry")

        self.assertEqual(state.release_id, "release-1")
        self.assertEqual(client_factory.call_count, 1)
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].post.await_count, 2)

    async def test_resolves_explicit_environment_and_default_timeouts(self) -> None:
        cases = [
            (
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                    "DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS": "175",
                },
                {
                    "timeout": 12.5,
                    "timeout_env_var": "DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS",
                    "default_timeout": 180.0,
                },
                12.5,
            ),
            (
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                    "DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS": "175",
                },
                {
                    "timeout_env_var": "DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS",
                    "default_timeout": 180.0,
                },
                175.0,
            ),
            (
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                },
                {
                    "timeout_env_var": "DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS",
                    "default_timeout": 180.0,
                },
                180.0,
            ),
            (
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                },
                {},
                30.0,
            ),
        ]

        for environment, options, expected in cases:
            with self.subTest(expected=expected):
                client_factory, clients = self._client_factory(
                    self._response({"status": "ok", "data": {}}),
                )
                await self._invoke(
                    environment=environment,
                    client_factory=client_factory,
                    **options,
                )
                timeout = clients[0].post.await_args.kwargs["timeout"]
                self.assertEqual(timeout.connect, 5.0)
                self.assertEqual(timeout.read, expected)
                await gateway.close_gateway_transport()

    async def test_configures_explicit_connection_pool_limits(self) -> None:
        client_factory, _ = self._client_factory(
            self._response({"status": "ok", "data": {}}),
        )
        await self._invoke(
            environment={
                "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                "APP_TOOL_GATEWAY_CONNECT_TIMEOUT_SECONDS": "9",
                "APP_TOOL_GATEWAY_MAX_CONNECTIONS": "40",
                "APP_TOOL_GATEWAY_MAX_KEEPALIVE_CONNECTIONS": "11",
                "APP_TOOL_GATEWAY_KEEPALIVE_EXPIRY_SECONDS": "25",
            },
            client_factory=client_factory,
        )

        limits = client_factory.call_args.kwargs["limits"]
        self.assertEqual(limits.max_connections, 40)
        self.assertEqual(limits.max_keepalive_connections, 11)
        self.assertEqual(limits.keepalive_expiry, 25.0)

    async def test_requires_complete_request_context(self) -> None:
        with patch.object(
            gateway,
            "get_request_context",
            return_value=SimpleNamespace(
                user_id="tenant:user",
                session_id=None,
                call_id="call-1",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "context is incomplete"):
                await gateway.invoke_gateway_tool("get_user_context", {})

    async def test_state_resolution_is_authenticated_and_session_bound(self) -> None:
        client_factory, clients = self._client_factory(
            self._response(
                {
                    "inner_model_conversation_id": "conv_inner",
                    "bootstrap_required": False,
                    "release_id": "release-1",
                    "revision": 1,
                }
            ),
        )
        with (
            patch.dict(
                os.environ,
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                },
                clear=True,
            ),
            patch.object(
                gateway,
                "get_request_context",
                return_value=self._request_context(),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
            patch.object(
                gateway.httpx,
                "AsyncClient",
                client_factory,
            ),
        ):
            state = await gateway.resolve_agent_state("outer-foundry")

        self.assertEqual(state.inner_model_conversation_id, "conv_inner")
        request = clients[0].post.await_args
        self.assertEqual(
            request.kwargs["headers"],
            {"Authorization": "Bearer token"},
        )
        self.assertEqual(
            request.kwargs["json"],
            {
                "user_id": "tenant:user",
                "session_id": "session-1",
                "call_id": "call-1",
                "outer_foundry_conversation_id": "outer-foundry",
            },
        )

    async def test_terminal_state_transition_includes_fencing_revision(self) -> None:
        client_factory, clients = self._client_factory(
            self._response({"status": "completed"}),
        )
        with (
            patch.dict(
                os.environ,
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                },
                clear=True,
            ),
            patch.object(
                gateway,
                "get_request_context",
                return_value=self._request_context(),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
            patch.object(
                gateway.httpx,
                "AsyncClient",
                client_factory,
            ),
        ):
            await gateway.complete_agent_state_turn(
                "outer-foundry",
                revision=7,
            )

        self.assertEqual(
            clients[0].post.await_args.kwargs["json"],
            {
                "user_id": "tenant:user",
                "session_id": "session-1",
                "call_id": "call-1",
                "outer_foundry_conversation_id": "outer-foundry",
                "revision": 7,
            },
        )

    async def test_cleanup_closes_the_pooled_client(self) -> None:
        client_factory, clients = self._client_factory(
            self._response({"status": "ok", "data": {}}),
        )
        await self._invoke(
            environment={
                "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
            },
            client_factory=client_factory,
        )

        await gateway.close_gateway_transport()

        clients[0].aclose.assert_awaited_once_with()

    async def test_rejects_runtime_config_changes_without_explicit_cleanup(self) -> None:
        client_factory, _ = self._client_factory(
            self._response({"status": "ok", "data": {}}),
        )
        await self._invoke(
            environment={
                "APP_TOOL_GATEWAY_URL": "https://frontend.example",
                "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
            },
            client_factory=client_factory,
        )

        with (
            patch.dict(
                os.environ,
                {
                    "APP_TOOL_GATEWAY_URL": "https://other.example",
                    "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                },
                clear=True,
            ),
            patch.object(
                gateway,
                "get_request_context",
                return_value=self._request_context(),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "configuration changed without cleanup",
            ):
                await gateway.invoke_gateway_tool("get_user_context", {})


if __name__ == "__main__":
    unittest.main()
