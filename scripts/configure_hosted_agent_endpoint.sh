#!/usr/bin/env bash
# Pin both Hosted Agent manifests to the Foundry project created by Terraform.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ENDPOINT="${1:-}"

if [[ -z "$PROJECT_ENDPOINT" ]]; then
  PROJECT_ENDPOINT="$(
    terraform -chdir="$REPO_ROOT/infra" \
      output -raw foundry_agents_project_endpoint
  )"
fi

[[ "$PROJECT_ENDPOINT" =~ ^https://[^[:space:]]+/api/projects/[^[:space:]]+$ ]] || {
  echo "ERROR: invalid Foundry project endpoint: ${PROJECT_ENDPOINT}" >&2
  exit 2
}

manifests=(
  "$REPO_ROOT/agents/customer-support-maf/azure.yaml"
  "$REPO_ROOT/agents/directive-rag-maf/azure.yaml"
)

for manifest in "${manifests[@]}"; do
  rendered="$(mktemp)"
  if ! awk -v endpoint="$PROJECT_ENDPOINT" '
    /^[[:space:]]*endpoint:[[:space:]]*/ {
      count += 1
      match($0, /^[[:space:]]*/)
      print substr($0, RSTART, RLENGTH) "endpoint: " endpoint
      next
    }
    { print }
    END {
      if (count != 1) {
        exit 1
      }
    }
  ' "$manifest" >"$rendered"; then
    rm -f "$rendered"
    echo "ERROR: expected exactly one endpoint entry in ${manifest}" >&2
    exit 1
  fi
  mv "$rendered" "$manifest"
  echo "Configured ${manifest#"$REPO_ROOT"/} -> ${PROJECT_ENDPOINT}"
done
