from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse

from agent_memory_backend import server


def _settings(*, cosmos: bool = False, search: bool = False):
    return SimpleNamespace(
        cosmos_configured=cosmos,
        search_configured=search,
        foundry_prompt_enabled=False,
        foundry_hosted_enabled=False,
        directive_agent_enabled=False,
        directive_agent_visible=False,
        agent_gateway_audience="",
        hosted_agent_principal_ids=(),
        readiness_timeout_seconds=0.05,
    )


class ServerHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_liveness_does_not_call_dependencies(self) -> None:
        with patch.object(
            server.history_store, "health_check", new=AsyncMock()
        ) as check:
            result = await server.health_live()

        self.assertEqual(result, {"status": "ok"})
        check.assert_not_awaited()

    async def test_partial_initialization_returns_sanitized_503(self) -> None:
        with (
            patch("agent_memory_backend.server.get_settings", return_value=_settings(cosmos=True)),
        ):
            response = await server.health_ready()

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.body)
        self.assertEqual(payload["dependencies"]["cosmos_history"]["error"], "RuntimeError")
        self.assertEqual(payload["dependencies"]["cosmos_profile"]["error"], "RuntimeError")

    async def test_memory_failure_is_degraded_and_does_not_block_readiness(self) -> None:
        failing = AsyncMock(side_effect=ValueError("private database detail"))
        healthy = AsyncMock(return_value=None)
        with (
            patch(
                "agent_memory_backend.server.get_settings",
                return_value=_settings(cosmos=True),
            ),
            patch.object(server.history_store, "health_check", new=healthy),
            patch.object(server.profile_store, "health_check", new=healthy),
            patch.object(server.memory_store, "health_check", new=failing),
        ):
            degraded_response = await server.health_ready()

        self.assertEqual(degraded_response["status"], "ready")
        self.assertEqual(
            degraded_response["dependencies"]["cosmos_memory"]["error"],
            "ValueError",
        )
        self.assertFalse(
            degraded_response["dependencies"]["cosmos_memory"]["required"]
        )
        self.assertEqual(
            degraded_response["degraded_dependencies"], ["cosmos_memory"]
        )
        self.assertNotIn("private database", json.dumps(degraded_response))

        with (
            patch(
                "agent_memory_backend.server.get_settings",
                return_value=_settings(cosmos=True),
            ),
            patch.object(server.history_store, "health_check", new=healthy),
            patch.object(server.profile_store, "health_check", new=healthy),
            patch.object(server.memory_store, "health_check", new=healthy),
        ):
            ready_response = await server.health_ready()

        self.assertEqual(ready_response["status"], "ready")
        self.assertEqual(
            ready_response["dependencies"]["cosmos_memory"]["status"], "ok"
        )
        self.assertEqual(ready_response["degraded_dependencies"], [])
