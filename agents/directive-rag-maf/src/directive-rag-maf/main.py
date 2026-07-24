"""Foundry Hosted Microsoft Agent Framework directive assistant."""

from __future__ import annotations

import os

from agent_contracts import render_directive_rag_instructions
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import DefaultAzureCredential
from gateway_tools import DIRECTIVE_TOOLS
from maf_hosting import run_hosted_agent


def _max_iterations() -> int:
    error_message = "DIRECTIVE_MAX_ITERATIONS must be in the range 1..30"
    try:
        value = int(os.environ.get("DIRECTIVE_MAX_ITERATIONS", "12"))
    except ValueError as exc:
        raise RuntimeError(error_message) from exc
    if value < 1 or value > 30:
        raise RuntimeError(error_message)
    return value


def build_agent() -> Agent:
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["DIRECTIVE_MODEL_DEPLOYMENT"],
        credential=DefaultAzureCredential(),
        function_invocation_configuration={
            "max_iterations": _max_iterations()
        },
    )
    hosted_agent_id = (
        os.environ.get("FOUNDRY_AGENT_INSTANCE_CLIENT_ID", "").strip() or None
    )
    return Agent(
        client=client,
        id=hosted_agent_id,
        name="directive-rag-maf-hosted",
        instructions=render_directive_rag_instructions(),
        tools=list(DIRECTIVE_TOOLS),
        default_options={"store": True},
    )


def main() -> None:
    run_hosted_agent(build_agent, stateful_continuation=True)


if __name__ == "__main__":
    main()
