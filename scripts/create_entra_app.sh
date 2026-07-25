#!/usr/bin/env bash
# Create the Entra ID app registration for the app (MANUAL step — intentionally
# NOT in Terraform; see backend/README or docs. Requires Entra directory rights:
# Application Administrator / Application.ReadWrite.All).
#
# Produces a single SPA app registration that:
#   * exposes an `access_as_user` delegated scope  (backend audience = api://<appId>)
#   * exposes an `AgentTools.Invoke` application role for Hosted Agent identities
#   * exposes a `DirectiveSource.Manage` user role for approved operators
#   * issues v2 access tokens                       (iss = .../v2.0)
#   * has a SPA redirect URI for MSAL               (the frontend public URL)
#   * pre-authorizes the Azure CLI client           (so we can fetch a test token)
#
# Usage:
#   AZURE_CONFIG_DIR="$HOME/.azure-365" ./scripts/create_entra_app.sh \
#       --frontend-url https://<frontend-fqdn> [--name agent-memory-rag] \
#       [--app-id <existing-app-id>] [--localhost]
#
# On success it prints the env values to wire into the backend + frontend
# (ENTRA_TENANT_ID / ENTRA_AUDIENCE and ENTRA_CLIENT_ID / ENTRA_API_SCOPE).
set -euo pipefail

NAME="agent-memory-rag"
APP_ID=""
FRONTEND_URL=""
ADD_LOCALHOST="false"
AZ_CLI_APP_ID="04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Microsoft Azure CLI (well-known)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2;;
    --app-id) APP_ID="$2"; shift 2;;
    --frontend-url) FRONTEND_URL="${2%/}"; shift 2;;
    --localhost) ADD_LOCALHOST="true"; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[[ -n "$FRONTEND_URL" ]] || { echo "ERROR: --frontend-url is required" >&2; exit 2; }
if [[ -n "$APP_ID" && ! "$APP_ID" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo "ERROR: --app-id must be an application GUID" >&2
  exit 2
fi

TENANT_ID="$(az account show --query tenantId -o tsv)"
SCOPE_ID="$(uuidgen | tr 'A-Z' 'a-z')"
APP_ROLE_ID="$(uuidgen | tr 'A-Z' 'a-z')"
DIRECTIVE_SOURCE_ROLE_ID="$(uuidgen | tr 'A-Z' 'a-z')"

# SPA redirect URIs (MSAL redirects back to the app origin).
REDIRECTS="[\"${FRONTEND_URL}\", \"${FRONTEND_URL}/\"]"
if [[ "$ADD_LOCALHOST" == "true" ]]; then
  REDIRECTS="[\"${FRONTEND_URL}\", \"${FRONTEND_URL}/\", \"http://localhost:5175\", \"http://localhost:5175/\"]"
fi

echo ">> Creating app registration '${NAME}' in tenant ${TENANT_ID}..."
if [[ -n "$APP_ID" ]]; then
  EXISTING_NAME="$(
    az ad app show --id "$APP_ID" --query displayName --output tsv
  )"
  if [[ "$EXISTING_NAME" != "$NAME" ]]; then
    echo "ERROR: app ${APP_ID} is named '${EXISTING_NAME}', not '${NAME}'" >&2
    echo "Pass the matching --name value to update it explicitly." >&2
    exit 2
  fi
  echo ">> Reusing explicitly selected app ${APP_ID}"
else
  MATCHING_APP_IDS="$(
    az ad app list \
      --display-name "$NAME" \
      --query "[].appId" \
      --output tsv
  )"
  if [[ -n "$MATCHING_APP_IDS" ]]; then
    echo "ERROR: an app named '${NAME}' already exists." >&2
    echo "Rerun with --app-id set to the intended application ID:" >&2
    printf '%s\n' "$MATCHING_APP_IDS" >&2
    exit 2
  fi
  APP_ID="$(az ad app create --display-name "$NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
fi
OBJ_ID="$(az ad app show --id "$APP_ID" --query id -o tsv)"
# Preserve an already-created scope id so the identifier stays stable across re-runs.
EXISTING_SCOPE="$(az ad app show --id "$APP_ID" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)"
[[ -n "$EXISTING_SCOPE" ]] && SCOPE_ID="$EXISTING_SCOPE"
EXISTING_APP_ROLE="$(az ad app show --id "$APP_ID" --query "appRoles[?value=='AgentTools.Invoke'].id | [0]" -o tsv)"
[[ -n "$EXISTING_APP_ROLE" ]] && APP_ROLE_ID="$EXISTING_APP_ROLE"
EXISTING_DIRECTIVE_SOURCE_ROLE="$(az ad app show --id "$APP_ID" --query "appRoles[?value=='DirectiveSource.Manage'].id | [0]" -o tsv)"
[[ -n "$EXISTING_DIRECTIVE_SOURCE_ROLE" ]] && DIRECTIVE_SOURCE_ROLE_ID="$EXISTING_DIRECTIVE_SOURCE_ROLE"
EXISTING_APP_ROLES="$(az ad app show --id "$APP_ID" --query appRoles -o json)"
APP_ROLES="$(
  jq \
    --arg agent_id "$APP_ROLE_ID" \
    --arg directive_source_id "$DIRECTIVE_SOURCE_ROLE_ID" '
      (
        if any(.[]; .value == "AgentTools.Invoke") then
          map(
            if .value == "AgentTools.Invoke" then
              . + {
                allowedMemberTypes: ["Application"],
                description: "Allow a hosted agent to invoke the private application tool gateway.",
                displayName: "Invoke agent tools",
                isEnabled: true
              }
            else .
            end
          )
        else
          . + [{
            allowedMemberTypes: ["Application"],
            description: "Allow a hosted agent to invoke the private application tool gateway.",
            displayName: "Invoke agent tools",
            id: $agent_id,
            isEnabled: true,
            value: "AgentTools.Invoke"
          }]
        end
      )
      |
      if any(.[]; .value == "DirectiveSource.Manage") then
        map(
          if .value == "DirectiveSource.Manage" then
            . + {
              allowedMemberTypes: ["User"],
              description: "Allow an approved operator to list, upload, and delete directive source PDFs.",
              displayName: "Manage directive sources",
              isEnabled: true
            }
          else .
          end
        )
      else
        . + [{
          allowedMemberTypes: ["User"],
          description: "Allow an approved operator to list, upload, and delete directive source PDFs.",
          displayName: "Manage directive sources",
          id: $directive_source_id,
          isEnabled: true,
          value: "DirectiveSource.Manage"
        }]
      end
  ' <<<"$EXISTING_APP_ROLES"
)"
echo ">> appId=${APP_ID} objectId=${OBJ_ID} scopeId=${SCOPE_ID} agentRoleId=${APP_ROLE_ID} directiveSourceRoleId=${DIRECTIVE_SOURCE_ROLE_ID}"

# --- PATCH 1: identifier URI, exposed scope, v2 tokens, SPA redirects.
PATCH1=$(cat <<JSON
{
  "identifierUris": ["api://${APP_ID}"],
  "appRoles": ${APP_ROLES},
  "spa": { "redirectUris": ${REDIRECTS} },
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [
      {
        "id": "${SCOPE_ID}",
        "adminConsentDescription": "Allow the app to call the support API as the signed-in user.",
        "adminConsentDisplayName": "Access support API",
        "userConsentDescription": "Allow the app to call the support API on your behalf.",
        "userConsentDisplayName": "Access support API",
        "value": "access_as_user",
        "type": "User",
        "isEnabled": true
      }
    ]
  }
}
JSON
)
echo ">> [1/2] identifier URI, scope, v2 tokens, SPA redirects..."
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/${OBJ_ID}" \
  --headers "Content-Type=application/json" \
  --body "$PATCH1"

# --- PATCH 2: pre-authorize the Azure CLI for the now-existing scope (headless test tokens).
PATCH2=$(cat <<JSON
{
  "api": {
    "preAuthorizedApplications": [
      { "appId": "${AZ_CLI_APP_ID}", "delegatedPermissionIds": ["${SCOPE_ID}"] }
    ]
  }
}
JSON
)
echo ">> [2/2] pre-authorize Azure CLI client..."
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/${OBJ_ID}" \
  --headers "Content-Type=application/json" \
  --body "$PATCH2"

echo ">> Validating application roles..."
ROLES_READY="false"
for attempt in {1..12}; do
  ROLE_MANIFEST="$(
    az rest \
      --method GET \
      --uri "https://graph.microsoft.com/v1.0/applications/${OBJ_ID}" \
      --output json
  )"
  if jq -e \
    --arg agent_id "$APP_ROLE_ID" \
    --arg directive_source_id "$DIRECTIVE_SOURCE_ROLE_ID" '
      any(
        .appRoles[];
        .id == $agent_id
        and .value == "AgentTools.Invoke"
        and (.allowedMemberTypes | index("Application"))
        and .isEnabled == true
      )
      and any(
        .appRoles[];
        .id == $directive_source_id
        and .value == "DirectiveSource.Manage"
        and (.allowedMemberTypes | index("User"))
        and .isEnabled == true
      )
    ' >/dev/null <<<"$ROLE_MANIFEST"; then
    ROLES_READY="true"
    break
  fi
  sleep 5
done
if [[ "$ROLES_READY" != "true" ]]; then
  echo "ERROR: application role validation failed" >&2
  exit 1
fi

# Service principal (enterprise app) so tokens can be issued for this app.
if ! az ad sp show --id "$APP_ID" >/dev/null 2>&1; then
  echo ">> Creating service principal..."
  az ad sp create --id "$APP_ID" >/dev/null
fi

cat <<OUT

============================================================
Entra app registration ready.

  Tenant ID : ${TENANT_ID}
  Client ID : ${APP_ID}
  Audience  : ${APP_ID}   (v2 access tokens carry the client-id GUID as 'aud')
  API scope : api://${APP_ID}/access_as_user
  Operator role: DirectiveSource.Manage

Backend  (AUTH_MODE=entra) env:
  AUTH_MODE=entra
  ENTRA_TENANT_ID=${TENANT_ID}
  ENTRA_AUDIENCE=${APP_ID}
  ENTRA_REQUIRED_SCOPES=access_as_user

Frontend (/config.js) env:
  AUTH_MODE=entra
  ENTRA_TENANT_ID=${TENANT_ID}
  ENTRA_CLIENT_ID=${APP_ID}
  ENTRA_API_SCOPE=api://${APP_ID}/access_as_user

Fetch a test access token (Azure CLI is pre-authorized):
  az account get-access-token --scope api://${APP_ID}/access_as_user --query accessToken -o tsv
============================================================
OUT
