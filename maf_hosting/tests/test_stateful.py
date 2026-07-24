from __future__ import annotations

import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_framework_foundry_hosting import ResponsesHostServer

from maf_hosting.gateway import ResolvedAgentState
from maf_hosting.stateful import (
    StatefulResponsesHostServer,
    _active_session,
)


class StatefulHostTests(unittest.IsolatedAsyncioTestCase):
    def _server(self) -> StatefulResponsesHostServer:
        server = object.__new__(StatefulResponsesHostServer)
        server._session_locks = {}
        server._session_locks_guard = asyncio.Lock()
        return server

    async def test_later_turn_uses_session_and_skips_outer_history(self) -> None:
        observed = {}

        async def base_handler(self, request, context):
            del self, request
            observed["history"] = await context.get_history()
            observed["service_session_id"] = str(
                _active_session.get().service_session_id
            )
            yield SimpleNamespace(type="response.completed")

        context = SimpleNamespace(
            conversation_id="outer-conversation",
            get_history=AsyncMock(return_value=["old turn"]),
        )
        with (
            patch.object(
                ResponsesHostServer,
                "_handle_inner_agent",
                new=base_handler,
            ),
            patch(
                "maf_hosting.stateful.resolve_agent_state",
                new=AsyncMock(
                    return_value=ResolvedAgentState(
                        "conv_inner",
                        False,
                        "release-1",
                        1,
                    )
                ),
            ),
            patch(
                "maf_hosting.stateful.complete_agent_state_turn",
                new=AsyncMock(),
            ) as complete,
            patch.dict(
                "os.environ",
                {"DIRECTIVE_AGENT_RELEASE_ID": "release-1"},
                clear=False,
            ),
        ):
            events = [
                event
                async for event in self._server()._handle_inner_agent(
                    object(),
                    context,
                )
            ]

        self.assertEqual(len(events), 1)
        self.assertEqual(observed["history"], [])
        self.assertIn("conv_inner", observed["service_session_id"])
        context.get_history.assert_not_awaited()
        complete.assert_awaited_once_with("outer-conversation")

    async def test_bootstrap_loads_history_and_marks_completion(self) -> None:
        async def base_handler(self, request, context):
            del self, request
            await context.get_history()
            yield SimpleNamespace(type="response.completed")

        context = SimpleNamespace(
            conversation_id="outer-conversation",
            get_history=AsyncMock(return_value=["old turn"]),
        )
        with (
            patch.object(
                ResponsesHostServer,
                "_handle_inner_agent",
                new=base_handler,
            ),
            patch(
                "maf_hosting.stateful.resolve_agent_state",
                new=AsyncMock(
                    return_value=ResolvedAgentState(
                        "conv_inner",
                        True,
                        "release-1",
                        1,
                    )
                ),
            ),
            patch(
                "maf_hosting.stateful.complete_agent_state_turn",
                new=AsyncMock(),
            ) as complete,
            patch.dict(
                "os.environ",
                {"DIRECTIVE_AGENT_RELEASE_ID": "release-1"},
                clear=False,
            ),
        ):
            _ = [
                event
                async for event in self._server()._handle_inner_agent(
                    object(),
                    context,
                )
            ]

        context.get_history.assert_awaited_once_with()
        complete.assert_awaited_once_with("outer-conversation")

    async def test_failed_response_releases_durable_turn_lease(self) -> None:
        async def base_handler(self, request, context):
            del self, request, context
            yield SimpleNamespace(type="response.failed")

        context = SimpleNamespace(conversation_id="outer-conversation")
        with (
            patch.object(
                ResponsesHostServer,
                "_handle_inner_agent",
                new=base_handler,
            ),
            patch(
                "maf_hosting.stateful.resolve_agent_state",
                new=AsyncMock(
                    return_value=ResolvedAgentState(
                        "conv_inner",
                        False,
                        "release-1",
                        1,
                    )
                ),
            ),
            patch(
                "maf_hosting.stateful.fail_agent_state_turn",
                new=AsyncMock(),
            ) as fail,
            patch.dict(
                "os.environ",
                {"DIRECTIVE_AGENT_RELEASE_ID": "release-1"},
                clear=False,
            ),
        ):
            _ = [
                event
                async for event in self._server()._handle_inner_agent(
                    object(),
                    context,
                )
            ]

        fail.assert_awaited_once_with("outer-conversation")

    def test_pinned_host_override_signature_is_compatible(self) -> None:
        self.assertEqual(
            tuple(
                inspect.signature(
                    ResponsesHostServer._handle_inner_agent
                ).parameters
            ),
            ("self", "request", "context"),
        )
        source = inspect.getsource(ResponsesHostServer._handle_inner_agent)
        self.assertIn("history = await context.get_history()", source)
        self.assertIn("self._agent.run(stream=True, **run_kwargs)", source)


if __name__ == "__main__":
    unittest.main()
