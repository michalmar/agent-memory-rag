"""Foundry Hosted Microsoft Agent Framework customer-support agent."""

from __future__ import annotations

import os

from agent_contracts import render_instructions
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import DefaultAzureCredential
from gateway_tools import (
    check_memory,
    get_user_context,
    update_user_profile,
)
from maf_hosting import run_hosted_agent


def _build_application_tools():
    endpoint = f"{os.environ['APP_TOOL_GATEWAY_URL'].rstrip('/')}/mcp/"
    return FoundryChatClient.get_mcp_tool(
        name="application_tools",
        url=endpoint,
        description="Retrieve authoritative application data such as order status.",
        allowed_tools=["get_order_status"],
        approval_mode="never_require",
        project_connection_id=os.environ["APP_TOOLS_CONNECTION_ID"],
    )


def build_agent() -> Agent:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    kb_endpoint = os.environ["IQ_MCP_ENDPOINT"]
    kb_connection_id = os.environ["IQ_CONNECTION_ID"]

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=DefaultAzureCredential(),
        function_invocation_configuration={"max_iterations": 5},
    )
    knowledge_base = FoundryChatClient.get_mcp_tool(
        name="knowledge_base_retrieve",
        url=kb_endpoint,
        description="Retrieve grounded customer-support knowledge from Foundry IQ.",
        allowed_tools=["knowledge_base_retrieve"],
        approval_mode="never_require",
        project_connection_id=kb_connection_id,
    )
    application_tools = _build_application_tools()
    hosted_agent_id = (
        os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID", "").strip() or None
    )
    return Agent(
        client=client,
        id=hosted_agent_id,
        name="customer-support-maf-hosted",
        instructions=render_instructions(),
        tools=[
            knowledge_base,
            application_tools,
            get_user_context,
            check_memory,
            update_user_profile,
        ],
        default_options={"store": False},
    )


def main() -> None:
    run_hosted_agent(build_agent)


if __name__ == "__main__":
    main()
