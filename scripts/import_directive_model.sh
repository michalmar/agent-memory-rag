#!/usr/bin/env bash
# Adopt an existing directive model deployment without changing source files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESOURCE_ID="${1:-}"

usage() {
  echo "Usage: $0 <directive-model-resource-id>" >&2
}

if [[ ! "$RESOURCE_ID" =~ ^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/deployments/[^/]+$ ]]; then
  usage
  exit 2
fi

terraform -chdir="$REPO_ROOT/infra" import \
  azurerm_cognitive_deployment.directive \
  "$RESOURCE_ID"
