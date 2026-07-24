from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = Path(__file__).resolve().parents[1] / "src" / "customer-support-maf"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SOURCE_DIR))

import main as hosted_main


class HostedMcpConfigurationTests(unittest.TestCase):
    def test_application_tool_includes_endpoint_and_connection(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_TOOL_GATEWAY_URL": "https://frontend.example/api/",
                "APP_TOOLS_CONNECTION_ID": "application-tools",
            },
        ):
            tool = hosted_main._build_application_tools()

        self.assertEqual(
            tool["server_url"],
            "https://frontend.example/api/mcp/",
        )
        self.assertEqual(tool["project_connection_id"], "application-tools")
        self.assertEqual(tool["allowed_tools"], ["get_order_status"])
        self.assertEqual(tool["require_approval"], "never")

    def test_agent_registers_both_mcp_and_three_local_tools(self) -> None:
        captured = {}

        def agent_factory(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(**kwargs)

        def mcp_tool_factory(**kwargs):
            return SimpleNamespace(name=kwargs["name"], configuration=kwargs)

        with (
            patch.dict(
                os.environ,
                {
                    "APP_TOOL_GATEWAY_URL": "https://frontend.example/api",
                    "APP_TOOLS_CONNECTION_ID": "application-tools",
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-5.6-sol",
                    "FOUNDRY_PROJECT_ENDPOINT": "https://project.example",
                    "IQ_CONNECTION_ID": "knowledge",
                    "IQ_MCP_ENDPOINT": "https://iq.example/mcp",
                },
                clear=True,
            ),
            patch.object(hosted_main, "Agent", side_effect=agent_factory),
            patch.object(hosted_main, "DefaultAzureCredential"),
            patch.object(
                hosted_main.FoundryChatClient,
                "get_mcp_tool",
                side_effect=mcp_tool_factory,
            ),
            patch.object(
                hosted_main,
                "render_instructions",
                return_value="instructions",
            ),
        ):
            hosted_main.build_agent()

        self.assertEqual(
            [tool.name for tool in captured["tools"]],
            [
                "knowledge_base_retrieve",
                "application_tools",
                "get_user_context",
                "check_memory",
                "update_user_profile",
            ],
        )
        self.assertEqual(captured["default_options"], {"store": False})


if __name__ == "__main__":
    unittest.main()
