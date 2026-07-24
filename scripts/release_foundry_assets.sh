#!/usr/bin/env bash
# Configure Foundry IQ and publish the native Prompt Agent without setup images.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"
export PIP_DISABLE_PIP_VERSION_CHECK=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
VENV_DIR="${SETUP_VENV_DIR:-$REPO_ROOT/setup/.venv}"
MODE="${1:-all}"

case "$MODE" in
    all | knowledge | prompt) ;;
    *)
        echo "Usage: $0 [all|knowledge|prompt]" >&2
        exit 2
        ;;
esac

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --quiet \
    -r "$REPO_ROOT/setup/knowledgebase/requirements.txt" \
    -r "$REPO_ROOT/setup/agents/requirements.txt"

export AZURE_OPENAI_ENDPOINT="$(tf openai_endpoint)"
export AZURE_OPENAI_RESOURCE_URI="$(tf openai_resource_uri)"
export AZURE_OPENAI_CHAT_DEPLOYMENT="$(tf chat_deployment)"
export AZURE_OPENAI_CHAT_MODEL="$AZURE_OPENAI_CHAT_DEPLOYMENT"
export AZURE_OPENAI_EMBED_DEPLOYMENT="$(tf embedding_deployment)"
export SEARCH_ENDPOINT="$(tf search_endpoint)"
export SEARCH_KB="$(tf search_knowledge_base)"
export SEARCH_ORDERS_INDEX="$(tf search_orders_index)"
export SEARCH_POLICY_INDEX="$(tf search_policy_index)"
export SEARCH_ORDERS_KNOWLEDGE_SOURCE="$(tf search_orders_knowledge_source)"
export SEARCH_POLICY_KNOWLEDGE_SOURCE="$(tf search_policy_knowledge_source)"
export SEARCH_KNOWLEDGE_API_VERSION="$(tf search_knowledge_api_version)"

export FOUNDRY_PROJECT_ENDPOINT="$(tf foundry_agents_project_endpoint)"
export AZURE_AI_MODEL_DEPLOYMENT_NAME="$AZURE_OPENAI_CHAT_DEPLOYMENT"
export FOUNDRY_IQ_CONNECTION_ID="$(tf foundry_iq_connection_name)"
export FOUNDRY_IQ_MCP_ENDPOINT="$(tf foundry_iq_mcp_endpoint)"
export FOUNDRY_PROMPT_AGENT_NAME="$(tf foundry_prompt_agent_name)"
export AGENT_RELEASE_ID="$(tf agent_release_id)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ "$MODE" == "all" || "$MODE" == "knowledge" ]]; then
    echo "==> Configuring Search indexes and Foundry IQ"
    "$VENV_DIR/bin/python" "$REPO_ROOT/setup/knowledgebase/setup_search.py"
fi

if [[ "$MODE" == "all" || "$MODE" == "prompt" ]]; then
    echo "==> Publishing native Prompt Agent"
    "$VENV_DIR/bin/python" "$REPO_ROOT/setup/agents/release_prompt_agent.py"
fi
