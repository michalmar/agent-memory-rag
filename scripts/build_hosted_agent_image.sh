#!/usr/bin/env bash
# Build one independently versioned Hosted MAF image in ACR.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
AGENT_KIND="support"
IMAGE_TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENT_KIND="$2"; shift 2;;
    --tag) IMAGE_TAG="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

ACR_NAME="$(tf acr_name)"
ACR_LOGIN="$(tf acr_login_server)"

case "$AGENT_KIND" in
  support)
    AGENT_NAME="$(tf foundry_hosted_agent_name)"
    IMAGE_TAG="${IMAGE_TAG:-$(tf agent_release_id)}"
    DOCKERFILE="$REPO_ROOT/agents/customer-support-maf/src/customer-support-maf/Dockerfile"
    ;;
  directive)
    AGENT_NAME="${DIRECTIVE_HOSTED_AGENT_NAME:-$(tf directive_foundry_agent_name)}"
    IMAGE_TAG="${IMAGE_TAG:-${DIRECTIVE_AGENT_RELEASE_ID:-$(tf directive_agent_release_id)}}"
    DOCKERFILE="$REPO_ROOT/agents/directive-rag-maf.Dockerfile"
    ;;
  *)
    echo "ERROR: --agent must be support or directive" >&2
    exit 2
    ;;
esac

echo "==> Building ${AGENT_KIND} Hosted MAF image: ${ACR_LOGIN}/${AGENT_NAME}:${IMAGE_TAG}"
az acr build \
  -r "$ACR_NAME" \
  -t "${AGENT_NAME}:${IMAGE_TAG}" \
  -f "$DOCKERFILE" \
  "$REPO_ROOT"
