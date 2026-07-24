from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SOURCE_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "directive-rag-maf"
)
sys.path.insert(0, str(SOURCE_DIR))

spec = importlib.util.spec_from_file_location(
    "directive_hosted_main",
    SOURCE_DIR / "main.py",
)
assert spec and spec.loader
hosted_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosted_main)

import gateway_tools


class DirectiveHostedAgentTests(unittest.TestCase):
    def test_agent_registers_only_eight_directive_tools(self) -> None:
        captured = {}

        def agent_factory(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(**kwargs)

        with (
            patch.dict(
                os.environ,
                {
                    "FOUNDRY_PROJECT_ENDPOINT": "https://project.example",
                    "DIRECTIVE_MODEL_DEPLOYMENT": "gpt-5.6-sol",
                    "DIRECTIVE_MAX_ITERATIONS": "12",
                },
                clear=True,
            ),
            patch.object(hosted_main, "Agent", side_effect=agent_factory),
            patch.object(hosted_main, "FoundryChatClient"),
            patch.object(hosted_main, "DefaultAzureCredential"),
        ):
            hosted_main.build_agent()

        self.assertEqual(
            {tool.name for tool in captured["tools"]},
            {
                "resolve_directive",
                "search_directives",
                "get_directive_manifest",
                "get_directive_content",
                "search_within_directive",
                "get_related_directives",
                "get_precomputed_summary",
                "get_user_directive_mandates",
            },
        )
        self.assertNotIn("knowledge_base_retrieve", captured["tools"])
        self.assertNotIn("get_order_status", captured["tools"])
        self.assertIn("own retrieval planning", captured["instructions"])

    def test_iteration_ceiling_is_independent_and_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {"DIRECTIVE_MAX_ITERATIONS": "8"},
            clear=True,
        ):
            self.assertEqual(hosted_main._max_iterations(), 8)
        for value in ("0", "31"):
            with patch.dict(
                os.environ,
                {"DIRECTIVE_MAX_ITERATIONS": value},
                clear=True,
            ):
                with self.assertRaises(RuntimeError):
                    hosted_main._max_iterations()

    def test_observability_identity_uses_deployment_tenant(self) -> None:
        with patch.dict(
            os.environ,
            {"ENTRA_TENANT_ID": "tenant"},
            clear=True,
        ):
            self.assertEqual(
                hosted_main._configure_observability_identity(),
                ("tenant", None),
            )
            self.assertEqual(
                os.environ["FOUNDRY_AGENT_TENANT_ID"],
                "tenant",
            )

    def test_tool_wrapper_injects_request_context_not_model_identity(self) -> None:
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok", "data": {}},
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.post.return_value = response
        token = SimpleNamespace(token="token")

        async def invoke():
            with (
                patch.dict(
                    os.environ,
                    {
                        "APP_TOOL_GATEWAY_URL": "https://frontend.example/api",
                        "APP_TOOL_GATEWAY_SCOPE": "api://app/.default",
                    },
                    clear=True,
                ),
                patch.object(
                    gateway_tools,
                    "get_request_context",
                    return_value=SimpleNamespace(
                        user_id="tenant:user",
                        session_id="session-1",
                        call_id="call-1",
                    ),
                ),
                patch.object(
                    gateway_tools._credential,
                    "get_token",
                    new=AsyncMock(return_value=token),
                ),
                patch.object(
                    gateway_tools.httpx,
                    "AsyncClient",
                    return_value=client,
                ),
            ):
                return await gateway_tools.get_user_directive_mandates(
                    ["10000001"]
                )

        result = asyncio.run(invoke())
        self.assertEqual(result["status"], "ok")
        request = client.post.await_args
        self.assertEqual(
            request.args[0],
            "https://frontend.example/api/internal/agent-tools/"
            "get_user_directive_mandates",
        )
        self.assertEqual(
            request.kwargs["json"],
            {
                "user_id": "tenant:user",
                "session_id": "session-1",
                "call_id": "call-1",
                "arguments": {"directive_ids": ["10000001"]},
            },
        )


if __name__ == "__main__":
    unittest.main()
