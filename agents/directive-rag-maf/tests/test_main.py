from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = (
    Path(__file__).resolve().parents[1] / "src" / "directive-rag-maf"
)
sys.path.insert(0, str(REPO_ROOT))
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
    def test_agent_registers_only_four_current_directive_tools(self) -> None:
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
                "get_directive",
                "search_directives",
                "get_directive_content",
                "get_user_directive_mandates",
            },
        )
        self.assertNotIn("knowledge_base_retrieve", captured["tools"])
        self.assertNotIn("get_order_status", captured["tools"])
        self.assertIn(
            "own retrieval planning",
            " ".join(captured["instructions"].split()),
        )
        self.assertEqual(captured["default_options"], {"store": True})

    def test_iteration_ceiling_is_independent_and_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {"DIRECTIVE_MAX_ITERATIONS": "8"},
            clear=True,
        ):
            self.assertEqual(hosted_main._max_iterations(), 8)
        for value in ("", "not-a-number", "0", "31"):
            with patch.dict(
                os.environ,
                {"DIRECTIVE_MAX_ITERATIONS": value},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "range 1..30"):
                    hosted_main._max_iterations()

    def test_tool_wrapper_preserves_directive_timeout_configuration(self) -> None:
        invoke_gateway_tool = AsyncMock(
            return_value={"status": "ok", "data": {}}
        )

        with patch.object(
            gateway_tools,
            "invoke_gateway_tool",
            new=invoke_gateway_tool,
        ):
            result = asyncio.run(
                gateway_tools.get_user_directive_mandates(["ČD/42-A"])
            )

        self.assertEqual(result["status"], "ok")
        invoke_gateway_tool.assert_awaited_once_with(
            "get_user_directive_mandates",
            {"directive_ids": ["ČD/42-A"]},
            timeout_env_var="DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS",
            default_timeout=180.0,
        )

    def test_get_directive_wrapper_uses_consolidated_contract(self) -> None:
        invoke_gateway_tool = AsyncMock(
            return_value={"status": "ok", "data": {}}
        )

        with patch.object(
            gateway_tools,
            "invoke_gateway_tool",
            new=invoke_gateway_tool,
        ):
            asyncio.run(
                gateway_tools.get_directive(
                    "ČD/42-A",
                    view="manifest",
                )
            )

        invoke_gateway_tool.assert_awaited_once_with(
            "get_directive",
            {
                "directive_id": "ČD/42-A",
                "view": "manifest",
            },
            timeout_env_var="DIRECTIVE_TOOL_HTTP_TIMEOUT_SECONDS",
            default_timeout=180.0,
        )


if __name__ == "__main__":
    unittest.main()
