#!/usr/bin/env bash
# Configure a Foundry Hosted Agent identity for the application gateway and
# Agent 365 observability. The role assignments are idempotent.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

PRINCIPAL_ID=""
API_APP_ID=""
AZD_PROJECT_DIR=""
AGENT_TYPE="support"
APP_TOOLS_CONNECTION_ID="customer-support-tools-mcp"
SET_APP_TOOLS_CONNECTION=true
CONNECTION_OPTION_SET=false
AGENT365_APP_ID="${AGENT365_OBSERVABILITY_APP_ID:-9b975845-388f-4429-889e-eab1ef63949c}"
AGENT365_ROLE_ID="8f71190c-00c8-461d-a63b-f74abde9ba52"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --principal-id) PRINCIPAL_ID="$2"; shift 2;;
    --api-app-id) API_APP_ID="$2"; shift 2;;
    --azd-project-dir) AZD_PROJECT_DIR="$2"; shift 2;;
    --agent-type) AGENT_TYPE="$2"; shift 2;;
    --app-tools-connection-id)
      APP_TOOLS_CONNECTION_ID="$2"
      SET_APP_TOOLS_CONNECTION=true
      CONNECTION_OPTION_SET=true
      shift 2
      ;;
    --no-app-tools-connection)
      SET_APP_TOOLS_CONNECTION=false
      CONNECTION_OPTION_SET=true
      shift
      ;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[[ -n "$PRINCIPAL_ID" ]] || { echo "ERROR: --principal-id is required" >&2; exit 2; }
[[ -n "$API_APP_ID" ]] || { echo "ERROR: --api-app-id is required" >&2; exit 2; }
[[ "$AGENT_TYPE" == "support" || "$AGENT_TYPE" == "directive" ]] || {
  echo "ERROR: --agent-type must be support or directive" >&2
  exit 2
}
if [[ "$AGENT_TYPE" == "directive" && "$CONNECTION_OPTION_SET" == "false" ]]; then
  SET_APP_TOOLS_CONNECTION=false
fi

az ad sp show --id "$PRINCIPAL_ID" --query id -o none

assign_app_role() {
  local principal_id="$1"
  local resource_id="$2"
  local app_role_id="$3"
  local role_name="$4"
  local assignment_id

  assignment_id="$(
    az rest \
      --method GET \
      --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${principal_id}/appRoleAssignments" \
      --query "value[?resourceId=='${resource_id}' && appRoleId=='${app_role_id}'].id | [0]" \
      -o tsv
  )"

  if [[ -n "$assignment_id" ]]; then
    echo "${role_name} is already assigned to ${principal_id}."
    return
  fi

  az rest \
    --method POST \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/${principal_id}/appRoleAssignments" \
    --headers "Content-Type=application/json" \
    --body "{
      \"principalId\": \"${principal_id}\",
      \"resourceId\": \"${resource_id}\",
      \"appRoleId\": \"${app_role_id}\"
    }" \
    --query id \
    -o tsv

  echo "Assigned ${role_name} to ${principal_id}."
}

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

assign_app_role "$PRINCIPAL_ID" "$RESOURCE_SP_ID" "$APP_ROLE_ID" "AgentTools.Invoke"

AGENT365_RESOURCE_SP_ID="$(
  az ad sp show --id "$AGENT365_APP_ID" --query id -o tsv
)"
AGENT365_ROLE_VALUE="$(
  az ad sp show --id "$AGENT365_APP_ID" \
    --query "appRoles[?id=='${AGENT365_ROLE_ID}' && value=='Agent365.Observability.OtelWrite'].value | [0]" \
    -o tsv
)"
[[ "$AGENT365_ROLE_VALUE" == "Agent365.Observability.OtelWrite" ]] || {
  echo "ERROR: Agent365.Observability.OtelWrite is unavailable in this tenant" >&2
  exit 1
}

assign_app_role \
  "$PRINCIPAL_ID" \
  "$AGENT365_RESOURCE_SP_ID" \
  "$AGENT365_ROLE_ID" \
  "Agent365.Observability.OtelWrite"

if [[ -n "$AZD_PROJECT_DIR" ]]; then
  [[ -f "$AZD_PROJECT_DIR/azure.yaml" ]] || {
    echo "ERROR: azure.yaml not found in ${AZD_PROJECT_DIR}" >&2
    exit 1
  }
  AI_ACCOUNT_NAME="$(
    cd "$AZD_PROJECT_DIR"
    azd env get-value AZURE_AI_ACCOUNT_NAME
  )"
  AI_PROJECT_NAME="$(
    cd "$AZD_PROJECT_DIR"
    azd env get-value AZURE_AI_PROJECT_NAME
  )"
  [[ -n "$AI_ACCOUNT_NAME" && -n "$AI_PROJECT_NAME" ]] || {
    echo "ERROR: AZURE_AI_ACCOUNT_NAME and AZURE_AI_PROJECT_NAME are required" >&2
    exit 1
  }
  PROJECT_ENDPOINT="https://${AI_ACCOUNT_NAME}.services.ai.azure.com/api/projects/${AI_PROJECT_NAME}"
  SUBSCRIPTION_ID="$(
    cd "$AZD_PROJECT_DIR"
    azd env get-value AZURE_SUBSCRIPTION_ID
  )"
  RESOURCE_GROUP="$(
    cd "$AZD_PROJECT_DIR"
    azd env get-value AZURE_RESOURCE_GROUP
  )"
  TENANT_ID="$(
    cd "$AZD_PROJECT_DIR"
    azd env get-value AZURE_TENANT_ID
  )"
  [[ -n "$TENANT_ID" ]] || {
    echo "ERROR: AZURE_TENANT_ID is required" >&2
    exit 1
  }
  PROJECT_AGENT_PRINCIPAL_ID="$(
    az rest \
      --method GET \
      --uri "https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/accounts/${AI_ACCOUNT_NAME}/projects/${AI_PROJECT_NAME}?api-version=2025-10-01-preview" \
      --query properties.agentIdentity.agentIdentityId \
      -o tsv
  )"
  [[ -n "$PROJECT_AGENT_PRINCIPAL_ID" ]] || {
    echo "ERROR: Foundry project Agent Identity was not returned" >&2
    exit 1
  }

  assign_app_role \
    "$PROJECT_AGENT_PRINCIPAL_ID" \
    "$RESOURCE_SP_ID" \
    "$APP_ROLE_ID" \
    "AgentTools.Invoke (project Agent Identity)"

  (
    cd "$AZD_PROJECT_DIR"
    if [[ "$SET_APP_TOOLS_CONNECTION" == "true" ]]; then
      azd env set APP_TOOLS_CONNECTION_ID "$APP_TOOLS_CONNECTION_ID"
    fi
    azd env set ENTRA_TENANT_ID "$TENANT_ID"
    azd env set FOUNDRY_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
  )
  if [[ "$SET_APP_TOOLS_CONNECTION" == "true" ]]; then
    echo "Configured MCP, tenant, and Foundry project values in ${AZD_PROJECT_DIR}."
  else
    echo "Configured tenant and Foundry project values in ${AZD_PROJECT_DIR}."
  fi
  if [[ "$AGENT_TYPE" == "directive" ]]; then
    echo "Register ${PRINCIPAL_ID} and ${PROJECT_AGENT_PRINCIPAL_ID} in directive_hosted_agent_principal_ids before enabling the directive agent."
  else
    echo "Register ${PRINCIPAL_ID} and ${PROJECT_AGENT_PRINCIPAL_ID} in support_hosted_agent_principal_ids (or the legacy hosted_agent_principal_ids) before enabling the support agent."
  fi
fi
