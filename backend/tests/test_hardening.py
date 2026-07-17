from __future__ import annotations

import asyncio
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from agent_contracts import (
    COMMON_TOOL_DEFINITIONS,
    render_foundry_prompt_instructions,
    tool_definition,
)
from agent_memory_backend.agent_tools import ToolExecutionError, ToolExecutor
from agent_memory_backend.foundry_iq_health import FoundryIqHealthProbe
from setup.agents.release_prompt_agent import _prompt_definition
from agent_memory_backend.telemetry import _safe_attributes


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.last_kwargs = {}

    async def post(self, *args, **kwargs) -> _FakeResponse:
        self.last_kwargs = kwargs
        return self.response


class FoundryIqHealthProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_response_is_rejected_without_response_body(self) -> None:
        client = FoundryIqHealthProbe()
        client._client = _FakeHttpClient(
            _FakeResponse(206, {"sensitive": "dependency body"})
        )
        with patch(
            "agent_memory_backend.foundry_iq_health.FoundryIqHealthProbe._headers",
            return_value={"Authorization": "test"},
        ):
            with self.assertRaisesRegex(RuntimeError, "status=206") as raised:
                await client._retrieve({"messages": []})
        self.assertNotIn("sensitive", str(raised.exception))

    async def test_health_request_uses_supported_runtime(self) -> None:
        client = FoundryIqHealthProbe()
        http_client = _FakeHttpClient(_FakeResponse(200))
        client._client = http_client
        with patch(
            "agent_memory_backend.foundry_iq_health.FoundryIqHealthProbe._headers",
            return_value={"Authorization": "test"},
        ):
            await client.health_check()

        self.assertGreater(
            http_client.last_kwargs["json"]["maxRuntimeInSeconds"],
            10,
        )
        self.assertNotIn("messages", http_client.last_kwargs["json"])
        self.assertEqual(
            http_client.last_kwargs["json"]["intents"][0]["type"],
            "semantic",
        )
        self.assertEqual(
            http_client.last_kwargs["json"]["outputMode"],
            "extractiveData",
        )
        self.assertNotIn("maxOutputSize", http_client.last_kwargs["json"])
        self.assertTrue(
            all(
                source["maxOutputDocuments"] >= 50
                for source in http_client.last_kwargs["json"][
                    "knowledgeSourceParams"
                ]
            )
        )


class SharedToolContractTests(unittest.IsolatedAsyncioTestCase):
    def test_prompt_release_definition_contains_only_foundry_iq(self) -> None:
        definition = _prompt_definition(
            "model", "project-connection", "https://example.test/mcp"
        ).as_dict()

        self.assertEqual(
            definition["tools"],
            [
                {
                    "type": "mcp",
                    "server_label": "foundry-iq",
                    "server_url": "https://example.test/mcp",
                    "require_approval": "never",
                    "allowed_tools": ["knowledge_base_retrieve"],
                    "project_connection_id": "project-connection",
                }
            ],
        )

    def test_foundry_prompt_is_iq_only(self) -> None:
        prompt = render_foundry_prompt_instructions()
        self.assertIn("knowledge_base_retrieve", prompt)
        for application_tool in (
            "get_user_context",
            "get_order_status",
            "check_memory",
            "update_user_profile",
        ):
            self.assertNotIn(application_tool, prompt)

    def test_common_tools_are_strict_and_do_not_accept_identity(self) -> None:
        names = {definition.name for definition in COMMON_TOOL_DEFINITIONS}
        self.assertEqual(
            names,
            {
                "get_user_context",
                "get_order_status",
                "check_memory",
                "update_user_profile",
            },
        )
        for definition in COMMON_TOOL_DEFINITIONS:
            parameters = definition.arguments_model.model_json_schema()
            self.assertFalse(parameters["additionalProperties"])
            self.assertNotIn("user_id", parameters.get("properties", {}))
            self.assertNotIn("tenant_id", parameters.get("properties", {}))

    async def test_unknown_and_extra_arguments_return_typed_errors(self) -> None:
        executor = ToolExecutor(None, None)
        with self.assertRaises(ToolExecutionError) as unknown:
            await executor.execute("do_classic_rag", {}, user_id="tenant:user")
        self.assertEqual(unknown.exception.code, "UNKNOWN_TOOL")

        with self.assertRaises(ToolExecutionError) as invalid:
            await executor.execute(
                "get_order_status",
                {"order_id": "ORD-001", "user_id": "attacker"},
                user_id="tenant:user",
            )
        self.assertEqual(invalid.exception.code, "INVALID_TOOL_ARGUMENTS")

    def test_unknown_tool_definition_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            tool_definition("knowledge_base_retrieve")


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_failure_and_timeout_are_sanitized(self) -> None:
        from agent_memory_backend.health import run_readiness_check

        async def success() -> None:
            return None

        async def failure() -> None:
            raise ValueError("sensitive dependency detail")

        async def slow() -> None:
            await asyncio.sleep(0.1)

        with patch("agent_memory_backend.health.span", return_value=nullcontext()):
            _, success_result = await run_readiness_check("ok", success, 0.1)
            _, failure_result = await run_readiness_check("failed", failure, 0.1)
            _, timeout_result = await run_readiness_check("slow", slow, 0.01)

        self.assertEqual(success_result["status"], "ok")
        self.assertEqual(failure_result["error"], "ValueError")
        self.assertNotIn("sensitive", json.dumps(failure_result))
        self.assertEqual(timeout_result["error"], "timeout")


class TelemetryPrivacyTests(unittest.TestCase):
    def test_only_explicitly_safe_attributes_are_exported(self) -> None:
        attributes = _safe_attributes(
            {
                "agent.type": "foundry-prompt",
                "prompt.content": "private prompt",
                "user.profile": "private profile",
                "tool.arguments": "private payload",
            }
        )

        self.assertEqual(attributes, {"agent.type": "foundry-prompt"})

    def test_span_context_closes_on_success_error_and_cancellation(self) -> None:
        from agent_memory_backend.telemetry import span

        exits: list[type[BaseException] | None] = []

        class FakeSpan:
            def set_attribute(self, key: str, value: object) -> None:
                return None

        class FakeContext:
            def __enter__(self) -> FakeSpan:
                return FakeSpan()

            def __exit__(self, exc_type, exc, traceback) -> bool:
                exits.append(exc_type)
                return False

        tracer = SimpleNamespace(start_as_current_span=lambda name: FakeContext())
        with patch("opentelemetry.trace.get_tracer", return_value=tracer):
            with span("success"):
                pass
            with self.assertRaises(ValueError):
                with span("error"):
                    raise ValueError("failure")
            with self.assertRaises(asyncio.CancelledError):
                with span("cancelled"):
                    raise asyncio.CancelledError()

        self.assertEqual(exits, [None, ValueError, asyncio.CancelledError])


if __name__ == "__main__":
    unittest.main()
