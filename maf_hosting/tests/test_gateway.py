from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from maf_hosting import gateway


class GatewayInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def _invoke(
        self,
        *,
        environment: dict[str, str],
        **timeout_options,
    ):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok", "data": {}},
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post.return_value = response

        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                gateway,
                "get_request_context",
                return_value=SimpleNamespace(
                    user_id="tenant:user",
                    session_id="session-1",
                    call_id="call-1",
                ),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
            patch.object(
                gateway.httpx,
                "AsyncClient",
                return_value=client,
            ) as client_factory,
        ):
            result = await gateway.invoke_gateway_tool(
                "get_user_context",
                {"query": "remember"},
                **timeout_options,
            )

        return result, client, client_factory

    async def test_injects_request_context_and_bearer_token(self) -> None:
        result, client, _ = await self._invoke(
            environment={
                "APP_TOOL_GATEWAY_URL": "https://frontend.example/api/",
                "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
            },
        )

        self.assertEqual(result["status"], "ok")
        request = client.post.await_args
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
                _, _, client_factory = await self._invoke(
                    environment=environment,
                    **options,
                )
                self.assertEqual(
                    client_factory.call_args.kwargs["timeout"],
                    expected,
                )

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
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "inner_model_conversation_id": "conv_inner",
                "bootstrap_required": False,
                "release_id": "release-1",
                "revision": 1,
            },
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post.return_value = response
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
                return_value=SimpleNamespace(
                    user_id="tenant:user",
                    session_id="session-1",
                    call_id="call-1",
                ),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
            patch.object(
                gateway.httpx,
                "AsyncClient",
                return_value=client,
            ),
        ):
            state = await gateway.resolve_agent_state("outer-foundry")

        self.assertEqual(state.inner_model_conversation_id, "conv_inner")
        request = client.post.await_args
        self.assertEqual(
            request.kwargs["headers"],
            {"Authorization": "Bearer " + "token"},
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
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "completed"},
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post.return_value = response
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
                return_value=SimpleNamespace(
                    user_id="tenant:user",
                    session_id="session-1",
                    call_id="call-1",
                ),
            ),
            patch.object(
                gateway._credential,
                "get_token",
                new=AsyncMock(return_value=SimpleNamespace(token="token")),
            ),
            patch.object(
                gateway.httpx,
                "AsyncClient",
                return_value=client,
            ),
        ):
            await gateway.complete_agent_state_turn(
                "outer-foundry",
                revision=7,
            )

        self.assertEqual(
            client.post.await_args.kwargs["json"],
            {
                "user_id": "tenant:user",
                "session_id": "session-1",
                "call_id": "call-1",
                "outer_foundry_conversation_id": "outer-foundry",
                "revision": 7,
            },
        )


if __name__ == "__main__":
    unittest.main()
