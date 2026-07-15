"""Foundry Hosted Microsoft Agent Framework customer-support agent."""

from __future__ import annotations

import os

from agent_contracts import render_instructions
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity.aio import DefaultAzureCredential
from gateway_tools import (
    check_memory,
    get_order_status,
    get_user_context,
    update_user_profile,
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
    return Agent(
        client=client,
        name="customer-support-maf-hosted",
        instructions=render_instructions(),
        tools=[
            knowledge_base,
            get_user_context,
            get_order_status,
            check_memory,
            update_user_profile,
        ],
        default_options={"store": False},
    )


def main() -> None:
    ResponsesHostServer(build_agent()).run()


if __name__ == "__main__":
    main()
