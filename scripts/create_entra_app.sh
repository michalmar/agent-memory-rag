#!/usr/bin/env bash
# Create the Entra ID app registration for the app (MANUAL step — intentionally
# NOT in Terraform; see backend/README or docs. Requires Entra directory rights:
# Application Administrator / Application.ReadWrite.All).
#
# Produces a single SPA app registration that:
#   * exposes an `access_as_user` delegated scope  (backend audience = api://<appId>)
#   * issues v2 access tokens                       (iss = .../v2.0)
#   * has a SPA redirect URI for MSAL               (the frontend public URL)
#   * pre-authorizes the Azure CLI client           (so we can fetch a test token)
#
# Usage:
#   AZURE_CONFIG_DIR="$HOME/.azure-365" ./scripts/create_entra_app.sh \
#       --frontend-url https://<frontend-fqdn> [--name agent-memory-rag] [--localhost]
#
# On success it prints the env values to wire into the backend + frontend
# (ENTRA_TENANT_ID / ENTRA_AUDIENCE and ENTRA_CLIENT_ID / ENTRA_API_SCOPE).
set -euo pipefail

NAME="agent-memory-rag"
FRONTEND_URL=""
ADD_LOCALHOST="false"
AZ_CLI_APP_ID="04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Microsoft Azure CLI (well-known)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2;;
    --frontend-url) FRONTEND_URL="${2%/}"; shift 2;;
    --localhost) ADD_LOCALHOST="true"; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

[[ -n "$FRONTEND_URL" ]] || { echo "ERROR: --frontend-url is required" >&2; exit 2; }

TENANT_ID="$(az account show --query tenantId -o tsv)"
SCOPE_ID="$(uuidgen | tr 'A-Z' 'a-z')"

# SPA redirect URIs (MSAL redirects back to the app origin).
REDIRECTS="[\"${FRONTEND_URL}\", \"${FRONTEND_URL}/\"]"
if [[ "$ADD_LOCALHOST" == "true" ]]; then
  REDIRECTS="[\"${FRONTEND_URL}\", \"${FRONTEND_URL}/\", \"http://localhost:5175\", \"http://localhost:5175/\"]"
fi

echo ">> Creating app registration '${NAME}' in tenant ${TENANT_ID}..."
# Idempotent: reuse an existing app with this display name if present.
EXISTING="$(az ad app list --display-name "$NAME" --query "[0].appId" -o tsv)"
if [[ -n "$EXISTING" ]]; then
  APP_ID="$EXISTING"
  echo ">> Reusing existing app ${APP_ID}"
else
  APP_ID="$(az ad app create --display-name "$NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
fi
OBJ_ID="$(az ad app show --id "$APP_ID" --query id -o tsv)"
# Preserve an already-created scope id so the identifier stays stable across re-runs.
EXISTING_SCOPE="$(az ad app show --id "$APP_ID" --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)"
[[ -n "$EXISTING_SCOPE" ]] && SCOPE_ID="$EXISTING_SCOPE"
echo ">> appId=${APP_ID} objectId=${OBJ_ID} scopeId=${SCOPE_ID}"

# --- PATCH 1: identifier URI, exposed scope, v2 tokens, SPA redirects.
PATCH1=$(cat <<JSON
{
  "identifierUris": ["api://${APP_ID}"],
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
