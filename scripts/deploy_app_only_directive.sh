#!/usr/bin/env bash
# Build and deploy application images to an environment selected by variables.
# Optionally build and deploy a new Directive Hosted Agent version.
set -euo pipefail

export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TAG=""
ENV_FILE=""
DEPLOY_BACKEND=true
DEPLOY_FRONTEND=true
WITH_DIRECTIVE=false
APP_MODE_SET=false
BUILD_CONTEXT=""
BUILD_LOG=""
UPDATE_LOG=""

usage() {
  cat <<'EOF'
Usage:
  deploy_app_only_directive.sh [tag] [--env-file <path>]
  deploy_app_only_directive.sh [tag] --backend-only [--env-file <path>]
  deploy_app_only_directive.sh [tag] --frontend-only [--env-file <path>]
  deploy_app_only_directive.sh [tag] --with-directive [--env-file <path>]
  deploy_app_only_directive.sh [tag] --directive-only [--env-file <path>]

Required environment:
  AZURE_CONFIG_DIR
  AZURE_TENANT_ID
  AZURE_SUBSCRIPTION_ID
  ACR_NAME
  ACR_LOGIN_SERVER

Required for backend/frontend:
  AZURE_RESOURCE_GROUP
  BACKEND_CONTAINER_APP     (when deploying backend)
  FRONTEND_CONTAINER_APP    (when deploying frontend)

Required for Directive Agent:
  DIRECTIVE_AGENT_NAME
  FOUNDRY_PROJECT_ENDPOINT

Optional:
  FOUNDRY_API_VERSION       (default: v1)
  FOUNDRY_RESOURCE          (default: https://ai.azure.com)
EOF
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: required environment variable $name is not set" >&2
    exit 2
  fi
}

run_acr_build() {
  local component="$1"
  local image="$2"
  local status
  shift 2

  BUILD_LOG="$(mktemp "${TMPDIR:-/tmp}/agent-memory-acr-build.XXXXXX")"
  echo "==> $component build started: $image"
  if az acr build "$@" >"$BUILD_LOG" 2>&1; then
    rm -f "$BUILD_LOG"
    BUILD_LOG=""
    echo "==> $component build finished: $image"
    return
  else
    status=$?
  fi

  echo "ERROR: $component build failed; Azure build output follows" >&2
  cat "$BUILD_LOG" >&2
  rm -f "$BUILD_LOG"
  BUILD_LOG=""
  return "$status"
}

run_containerapp_update() {
  local component="$1"
  local app_name="$2"
  local image="$3"
  local status

  UPDATE_LOG="$(mktemp "${TMPDIR:-/tmp}/agent-memory-aca-update.XXXXXX")"
  echo "==> $component Container App update started: $image"
  if az containerapp update \
    --name "$app_name" \
    --resource-group "$RESOURCE_GROUP" \
    --image "$image" \
    --only-show-errors \
    --output none >"$UPDATE_LOG" 2>&1; then
    rm -f "$UPDATE_LOG"
    UPDATE_LOG=""
    echo "==> $component Container App update: OK"
    return
  else
    status=$?
  fi

  echo "ERROR: $component Container App update failed; Azure output follows" >&2
  cat "$UPDATE_LOG" >&2
  rm -f "$UPDATE_LOG"
  UPDATE_LOG=""
  return "$status"
}

cleanup() {
  if [[ -n "$UPDATE_LOG" && -f "$UPDATE_LOG" ]]; then
    rm -f "$UPDATE_LOG"
  fi
  if [[ -n "$BUILD_LOG" && -f "$BUILD_LOG" ]]; then
    rm -f "$BUILD_LOG"
  fi
  if [[ -n "$BUILD_CONTEXT" && -d "$BUILD_CONTEXT" ]]; then
    rm -rf "$BUILD_CONTEXT"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "ERROR: --env-file requires a path" >&2
        exit 2
      fi
      [[ -z "$ENV_FILE" ]] || {
        echo "ERROR: --env-file may be provided only once" >&2
        exit 2
      }
      ENV_FILE="$2"
      shift 2
      ;;
    --backend-only)
      [[ "$APP_MODE_SET" == false ]] || {
        echo "ERROR: choose only one of --backend-only, --frontend-only, or --directive-only" >&2
        exit 2
      }
      DEPLOY_BACKEND=true
      DEPLOY_FRONTEND=false
      APP_MODE_SET=true
      shift
      ;;
    --frontend-only)
      [[ "$APP_MODE_SET" == false ]] || {
        echo "ERROR: choose only one of --backend-only, --frontend-only, or --directive-only" >&2
        exit 2
      }
      DEPLOY_BACKEND=false
      DEPLOY_FRONTEND=true
      APP_MODE_SET=true
      shift
      ;;
    --with-directive)
      WITH_DIRECTIVE=true
      shift
      ;;
    --directive-only)
      [[ "$APP_MODE_SET" == false ]] || {
        echo "ERROR: choose only one of --backend-only, --frontend-only, or --directive-only" >&2
        exit 2
      }
      DEPLOY_BACKEND=false
      DEPLOY_FRONTEND=false
      WITH_DIRECTIVE=true
      APP_MODE_SET=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      [[ -z "$TAG" ]] || {
        echo "ERROR: only one image tag may be provided" >&2
        exit 2
      }
      TAG="$1"
      shift
      ;;
  esac
done

if [[ -n "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" ]] || {
    echo "ERROR: environment file not found: $ENV_FILE" >&2
    exit 2
  }
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

require_env AZURE_CONFIG_DIR
require_env AZURE_TENANT_ID
require_env AZURE_SUBSCRIPTION_ID
require_env ACR_NAME
require_env ACR_LOGIN_SERVER

if [[ "$DEPLOY_BACKEND" == true || "$DEPLOY_FRONTEND" == true ]]; then
  require_env AZURE_RESOURCE_GROUP
fi
if [[ "$DEPLOY_BACKEND" == true ]]; then
  require_env BACKEND_CONTAINER_APP
fi
if [[ "$DEPLOY_FRONTEND" == true ]]; then
  require_env FRONTEND_CONTAINER_APP
fi
if [[ "$WITH_DIRECTIVE" == true ]]; then
  require_env DIRECTIVE_AGENT_NAME
  require_env FOUNDRY_PROJECT_ENDPOINT
fi

TARGET_TENANT_ID="$AZURE_TENANT_ID"
TARGET_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-}"
ACR_LOGIN="$ACR_LOGIN_SERVER"
BACKEND_APP="${BACKEND_CONTAINER_APP:-}"
FRONTEND_APP="${FRONTEND_CONTAINER_APP:-}"
FOUNDRY_BASE_URL="${FOUNDRY_PROJECT_ENDPOINT:-}"
FOUNDRY_API_VERSION="${FOUNDRY_API_VERSION:-v1}"
FOUNDRY_RESOURCE="${FOUNDRY_RESOURCE:-https://ai.azure.com}"

TAG="${TAG:-manual-$(date -u +%Y%m%d%H%M%S)}"
BACKEND_IMAGE="$ACR_LOGIN/backend:$TAG"
FRONTEND_IMAGE="$ACR_LOGIN/frontend:$TAG"
DIRECTIVE_IMAGE=""
if [[ "$WITH_DIRECTIVE" == true ]]; then
  DIRECTIVE_IMAGE="$ACR_LOGIN/$DIRECTIVE_AGENT_NAME:$TAG"
fi

command -v az >/dev/null || {
  echo "ERROR: az is required" >&2
  exit 1
}
if [[ "$WITH_DIRECTIVE" == true ]]; then
  command -v jq >/dev/null || {
    echo "ERROR: jq is required with --with-directive or --directive-only" >&2
    exit 1
  }
fi

az account set --subscription "$TARGET_SUBSCRIPTION_ID"
ACTIVE_TENANT_ID="$(az account show --query tenantId -o tsv)"
ACTIVE_SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
if [[ "$ACTIVE_TENANT_ID" != "$TARGET_TENANT_ID" ||
  "$ACTIVE_SUBSCRIPTION_ID" != "$TARGET_SUBSCRIPTION_ID" ]]; then
  echo "ERROR: Azure CLI is not using the expected target tenant and subscription" >&2
  exit 1
fi

echo "==> Tenant       : $ACTIVE_TENANT_ID"
echo "==> Subscription : $ACTIVE_SUBSCRIPTION_ID"
echo "==> Registry     : $ACR_LOGIN"
echo "==> Tag          : $TAG"

if [[ "$DEPLOY_BACKEND" == true || "$WITH_DIRECTIVE" == true ]]; then
  BUILD_CONTEXT="$(mktemp -d "${TMPDIR:-/tmp}/agent-memory-app-only.XXXXXX")"
  CONTEXT_PATHS=(agent_contracts directive_contracts maf_hosting)
  if [[ "$DEPLOY_BACKEND" == true ]]; then
    CONTEXT_PATHS+=(backend)
  fi
  if [[ "$WITH_DIRECTIVE" == true ]]; then
    CONTEXT_PATHS+=(agents/directive-rag-maf/src/directive-rag-maf)
  fi

  tar \
    --exclude='*/.env' \
    --exclude='*/.env.*' \
    --exclude='*/.venv' \
    --exclude='*/.venv/*' \
    --exclude='*/.pytest_cache' \
    --exclude='*/.pytest_cache/*' \
    --exclude='*/__pycache__' \
    --exclude='*/__pycache__/*' \
    --exclude='*/node_modules' \
    --exclude='*/node_modules/*' \
    --exclude='*/dist' \
    --exclude='*/dist/*' \
    --exclude='*/.azure' \
    --exclude='*/.azure/*' \
    -C "$REPO_ROOT" \
    -cf - \
    "${CONTEXT_PATHS[@]}" |
    tar -C "$BUILD_CONTEXT" -xf -
fi

if [[ "$DEPLOY_BACKEND" == true ]]; then
  run_acr_build "Backend image" "$BACKEND_IMAGE" \
    --registry "$ACR_NAME" \
    --image "backend:$TAG" \
    --file "$BUILD_CONTEXT/backend/Dockerfile" \
    "$BUILD_CONTEXT"
fi

if [[ "$DEPLOY_FRONTEND" == true ]]; then
  run_acr_build "Frontend image" "$FRONTEND_IMAGE" \
    --registry "$ACR_NAME" \
    --image "frontend:$TAG" \
    --file "$REPO_ROOT/frontend/Dockerfile" \
    "$REPO_ROOT/frontend"
fi

if [[ "$WITH_DIRECTIVE" == true ]]; then
  run_acr_build "Directive Hosted Agent image" "$DIRECTIVE_IMAGE" \
    --registry "$ACR_NAME" \
    --image "$DIRECTIVE_AGENT_NAME:$TAG" \
    --file "$BUILD_CONTEXT/agents/directive-rag-maf/src/directive-rag-maf/Dockerfile" \
    "$BUILD_CONTEXT"

  CURRENT_AGENT="$(
    az rest \
      --method GET \
      --url "$FOUNDRY_BASE_URL/agents/$DIRECTIVE_AGENT_NAME/versions?api-version=$FOUNDRY_API_VERSION" \
      --resource "$FOUNDRY_RESOURCE" \
      --only-show-errors \
      --output json |
      jq -c '
        [
          .data[]
          | select(
              .draft == false
              and .status == "active"
              and (.version | test("^[0-9]+$"))
            )
        ]
        | max_by(.version | tonumber)
      '
  )"
  [[ "$CURRENT_AGENT" != "null" ]] || {
    echo "ERROR: no existing Directive Hosted Agent version was found" >&2
    exit 1
  }
  DIRECTIVE_VERSION_BEFORE="$(jq -r '.version' <<<"$CURRENT_AGENT")"
  echo "==> Directive Hosted Agent version before: $DIRECTIVE_VERSION_BEFORE (active)"

  AGENT_REQUEST="$(
    jq -cn \
      --arg image "$DIRECTIVE_IMAGE" \
      --argjson current "$CURRENT_AGENT" \
      '{
        description: $current.description,
        definition: {
          kind: $current.definition.kind,
          container_configuration: {image: $image},
          cpu: $current.definition.cpu,
          memory: $current.definition.memory,
          protocol_versions: $current.definition.protocol_versions,
          environment_variables: $current.definition.environment_variables
        }
      }'
  )"
  DIRECTIVE_VERSION="$(
    az rest \
      --method POST \
      --url "$FOUNDRY_BASE_URL/agents/$DIRECTIVE_AGENT_NAME/versions?api-version=$FOUNDRY_API_VERSION" \
      --resource "$FOUNDRY_RESOURCE" \
      --headers "Content-Type=application/json" \
      --body "$AGENT_REQUEST" \
      --only-show-errors \
      --query version \
      --output tsv
  )"
  [[ -n "$DIRECTIVE_VERSION" ]] || {
    echo "ERROR: Foundry did not return the new agent version" >&2
    exit 1
  }

  for attempt in $(seq 1 60); do
    DIRECTIVE_STATUS="$(
      az rest \
        --method GET \
        --url "$FOUNDRY_BASE_URL/agents/$DIRECTIVE_AGENT_NAME/versions/$DIRECTIVE_VERSION?api-version=$FOUNDRY_API_VERSION" \
        --resource "$FOUNDRY_RESOURCE" \
        --only-show-errors \
        --query status \
        --output tsv
    )"
    if [[ "$DIRECTIVE_STATUS" == "active" ]]; then
      break
    fi
    if [[ "$DIRECTIVE_STATUS" == "failed" ]]; then
      az rest \
        --method GET \
        --url "$FOUNDRY_BASE_URL/agents/$DIRECTIVE_AGENT_NAME/versions/$DIRECTIVE_VERSION?api-version=$FOUNDRY_API_VERSION" \
        --resource "$FOUNDRY_RESOURCE" \
        --only-show-errors \
        --query error \
        --output json >&2
      exit 1
    fi
    if [[ "$attempt" == 60 ]]; then
      echo "ERROR: timed out waiting for Directive Hosted Agent version $DIRECTIVE_VERSION" >&2
      exit 1
    fi
    sleep 5
  done

  az rest \
    --method PATCH \
    --url "$FOUNDRY_BASE_URL/agents/$DIRECTIVE_AGENT_NAME?api-version=$FOUNDRY_API_VERSION" \
    --resource "$FOUNDRY_RESOURCE" \
    --headers "Content-Type=application/merge-patch+json" \
    --body '{
      "agent_endpoint": {
        "version_selector": {
          "version_selection_rules": [
            {
              "agent_version": "@latest",
              "traffic_percentage": 100,
              "type": "FixedRatio"
            }
          ]
        },
        "protocol_configuration": {
          "responses": {}
        }
      }
    }' \
    --only-show-errors \
    --output none

  DIRECTIVE_AGENT_AFTER="$(
    az rest \
      --method GET \
      --url "$FOUNDRY_BASE_URL/agents/$DIRECTIVE_AGENT_NAME/versions/$DIRECTIVE_VERSION?api-version=$FOUNDRY_API_VERSION" \
      --resource "$FOUNDRY_RESOURCE" \
      --only-show-errors \
      --output json
  )"
  DIRECTIVE_VERSION_AFTER="$(jq -r '.version' <<<"$DIRECTIVE_AGENT_AFTER")"
  DIRECTIVE_STATUS_AFTER="$(jq -r '.status' <<<"$DIRECTIVE_AGENT_AFTER")"
  if [[ "$DIRECTIVE_VERSION_AFTER" != "$DIRECTIVE_VERSION" ||
    "$DIRECTIVE_STATUS_AFTER" != "active" ||
    "$DIRECTIVE_VERSION_AFTER" -le "$DIRECTIVE_VERSION_BEFORE" ]]; then
    echo "ERROR: Directive Hosted Agent version verification failed" >&2
    exit 1
  fi
  echo "==> Directive Hosted Agent version after: $DIRECTIVE_VERSION_AFTER ($DIRECTIVE_STATUS_AFTER)"
fi

if [[ "$DEPLOY_BACKEND" == true ]]; then
  run_containerapp_update "Backend" "$BACKEND_APP" "$BACKEND_IMAGE"
fi

if [[ "$DEPLOY_FRONTEND" == true ]]; then
  run_containerapp_update "Frontend" "$FRONTEND_APP" "$FRONTEND_IMAGE"
fi

echo "==> Deployment complete"
