"""Idempotently publish the native customer-support Prompt Agent."""

from __future__ import annotations

import hashlib
import json
import os

from agent_contracts import (
    FOUNDRY_PROMPT_VERSION,
    render_foundry_prompt_instructions,
)
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _tool_hash(kb_connection_id: str, kb_endpoint: str) -> str:
    value = {
        "foundry_iq": {
            "connection_id": kb_connection_id,
            "endpoint": kb_endpoint,
            "allowed_tools": ["knowledge_base_retrieve"],
        },
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _prompt_definition(
    model: str, kb_connection_id: str, kb_endpoint: str
) -> PromptAgentDefinition:
    foundry_iq = MCPTool(
        server_label="foundry-iq",
        server_url=kb_endpoint,
        require_approval="never",
        allowed_tools=["knowledge_base_retrieve"],
        project_connection_id=kb_connection_id,
    )
    return PromptAgentDefinition(
        model=model,
        instructions=render_foundry_prompt_instructions(),
        temperature=0.2,
        tools=[foundry_iq],
    )


def release() -> dict[str, str]:
    endpoint = _required("FOUNDRY_PROJECT_ENDPOINT")
    model = _required("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    connection_id = _required("FOUNDRY_IQ_CONNECTION_ID")
    kb_endpoint = _required("FOUNDRY_IQ_MCP_ENDPOINT")
    agent_name = os.environ.get(
        "FOUNDRY_PROMPT_AGENT_NAME", "customer-support-prompt"
    )
    release_id = os.environ.get("AGENT_RELEASE_ID", "dual-foundry-local")
    tool_hash = _tool_hash(connection_id, kb_endpoint)
    metadata = {
        "release_id": release_id,
        "prompt_hash": FOUNDRY_PROMPT_VERSION,
        "tool_hash": tool_hash,
    }

    credential = DefaultAzureCredential(
        managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")
    )
    project = AIProjectClient(endpoint=endpoint, credential=credential)
    try:
        try:
            existing = project.agents.get(agent_name=agent_name)
            latest = existing.versions.latest
            latest_version = project.agents.get_version(
                agent_name=agent_name, agent_version=latest.version
            )
            if all(
                (latest_version.metadata or {}).get(key) == value
                for key, value in metadata.items()
            ):
                return {
                    "agent_name": agent_name,
                    "agent_version": str(latest_version.version),
                    **metadata,
                }
        except ResourceNotFoundError:
            pass

        version = project.agents.create_version(
            agent_name=agent_name,
            definition=_prompt_definition(model, connection_id, kb_endpoint),
            metadata=metadata,
            description="Native customer-support Prompt Agent using Foundry IQ.",
        )
        return {
            "agent_name": agent_name,
            "agent_version": str(version.version),
            **metadata,
        }
    finally:
        project.close()
        credential.close()


if __name__ == "__main__":
    print(json.dumps(release(), sort_keys=True))
