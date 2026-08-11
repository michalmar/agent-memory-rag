#!/usr/bin/env bash
# Register and verify every resource provider required by the Terraform stack.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

INCLUDE_QUOTA=false
if [[ "${1:-}" == "--include-quota" ]]; then
  INCLUDE_QUOTA=true
  shift
fi
[[ $# -eq 0 ]] || {
  echo "Usage: $0 [--include-quota]" >&2
  exit 2
}

providers=(
  Microsoft.App
  Microsoft.Authorization
  Microsoft.CognitiveServices
  Microsoft.ContainerRegistry
  Microsoft.DocumentDB
  Microsoft.Insights
  Microsoft.ManagedIdentity
  Microsoft.Network
  Microsoft.OperationalInsights
  Microsoft.Search
  Microsoft.Storage
)
if [[ "$INCLUDE_QUOTA" == true ]]; then
  providers+=(Microsoft.Quota)
fi

for provider in "${providers[@]}"; do
  echo "Registering ${provider}..."
  az provider register \
    --namespace "$provider" \
    --wait \
    --output none
done

for provider in "${providers[@]}"; do
  state="$(
    az provider show \
      --namespace "$provider" \
      --query registrationState \
      --output tsv
  )"
  [[ "$state" == "Registered" ]] || {
    echo "ERROR: ${provider} is ${state}, expected Registered" >&2
    exit 1
  }
  printf '%-40s %s\n' "$provider" "$state"
done
