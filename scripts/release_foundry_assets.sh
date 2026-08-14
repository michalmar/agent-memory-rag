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

set_tf_environment() {
    local name="$1"
    local output="$2"
    local value
    value="$(tf "$output")"
    export "$name=$value"
}

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --quiet \
    -r "$REPO_ROOT/setup/knowledgebase/requirements.txt" \
    -r "$REPO_ROOT/setup/agents/requirements.txt" \
    "$REPO_ROOT/directive_contracts" \
    "$REPO_ROOT/agent_contracts"
"$VENV_DIR/bin/python" -c 'import directive_contracts, agent_contracts'

set_tf_environment AZURE_OPENAI_ENDPOINT openai_endpoint
set_tf_environment AZURE_OPENAI_RESOURCE_URI openai_resource_uri
set_tf_environment AZURE_OPENAI_CHAT_DEPLOYMENT chat_deployment
export AZURE_OPENAI_CHAT_MODEL="$AZURE_OPENAI_CHAT_DEPLOYMENT"
set_tf_environment AZURE_OPENAI_EMBED_DEPLOYMENT embedding_deployment
set_tf_environment SEARCH_ENDPOINT search_endpoint
set_tf_environment SEARCH_KB search_knowledge_base
set_tf_environment SEARCH_ORDERS_INDEX search_orders_index
set_tf_environment SEARCH_POLICY_INDEX search_policy_index
set_tf_environment SEARCH_ORDERS_KNOWLEDGE_SOURCE search_orders_knowledge_source
set_tf_environment SEARCH_POLICY_KNOWLEDGE_SOURCE search_policy_knowledge_source
set_tf_environment SEARCH_KNOWLEDGE_API_VERSION search_knowledge_api_version

set_tf_environment FOUNDRY_PROJECT_ENDPOINT foundry_agents_project_endpoint
export AZURE_AI_MODEL_DEPLOYMENT_NAME="$AZURE_OPENAI_CHAT_DEPLOYMENT"
set_tf_environment FOUNDRY_IQ_CONNECTION_ID foundry_iq_connection_name
set_tf_environment FOUNDRY_IQ_MCP_ENDPOINT foundry_iq_mcp_endpoint
set_tf_environment FOUNDRY_PROMPT_AGENT_NAME foundry_prompt_agent_name
set_tf_environment AGENT_RELEASE_ID agent_release_id
if [[ "$MODE" == "all" || "$MODE" == "knowledge" ]]; then
    echo "==> Configuring Search indexes and Foundry IQ"
    "$VENV_DIR/bin/python" "$REPO_ROOT/setup/knowledgebase/setup_search.py"
fi

if [[ "$MODE" == "all" || "$MODE" == "prompt" ]]; then
    echo "==> Publishing native Prompt Agent"
    "$VENV_DIR/bin/python" "$REPO_ROOT/setup/agents/release_prompt_agent.py"
fi
