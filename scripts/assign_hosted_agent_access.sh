#!/usr/bin/env bash
# Assign the backend AgentTools.Invoke application role to a Foundry-created
# Hosted Agent service principal. The operation is idempotent.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

PRINCIPAL_ID=""
API_APP_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --principal-id) PRINCIPAL_ID="$2"; shift 2;;
    --api-app-id) API_APP_ID="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[[ -n "$PRINCIPAL_ID" ]] || { echo "ERROR: --principal-id is required" >&2; exit 2; }
[[ -n "$API_APP_ID" ]] || { echo "ERROR: --api-app-id is required" >&2; exit 2; }

az ad sp show --id "$PRINCIPAL_ID" --query id -o none

RESOURCE_SP_ID="$(az ad sp show --id "$API_APP_ID" --query id -o tsv)"
APP_ROLE_ID="$(
  az ad sp show --id "$API_APP_ID" \
    --query "appRoles[?value=='AgentTools.Invoke' && contains(allowedMemberTypes, 'Application')].id | [0]" \
    -o tsv
)"
[[ -n "$APP_ROLE_ID" ]] || {
  echo "ERROR: AgentTools.Invoke application role is not defined on ${API_APP_ID}" >&2
  exit 1
}

ASSIGNMENT_ID="$(
  az rest \
    --method GET \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${PRINCIPAL_ID}/appRoleAssignments" \
    --query "value[?resourceId=='${RESOURCE_SP_ID}' && appRoleId=='${APP_ROLE_ID}'].id | [0]" \
    -o tsv
)"

if [[ -n "$ASSIGNMENT_ID" ]]; then
  echo "AgentTools.Invoke is already assigned to ${PRINCIPAL_ID}."
  exit 0
fi

az rest \
  --method POST \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${PRINCIPAL_ID}/appRoleAssignments" \
  --headers "Content-Type=application/json" \
  --body "{
    \"principalId\": \"${PRINCIPAL_ID}\",
    \"resourceId\": \"${RESOURCE_SP_ID}\",
    \"appRoleId\": \"${APP_ROLE_ID}\"
  }" \
  --query id \
  -o tsv

echo "Assigned AgentTools.Invoke to ${PRINCIPAL_ID}."
