#!/usr/bin/env bash
# Validate the standalone Global Administrator input file at each deployment gate.
set -euo pipefail

PHASE="${1:-}"
INPUTS_FILE="${2:-}"
ERRORS=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  echo "Usage: $0 bootstrap|terraform|release|agent-roles <inputs-file>" >&2
}

case "$PHASE" in
  bootstrap|terraform|release|agent-roles) ;;
  *)
    usage
    exit 2
    ;;
esac

[[ -n "$INPUTS_FILE" && -f "$INPUTS_FILE" ]] || {
  usage
  exit 2
}

set -a
# shellcheck disable=SC1090
source "$INPUTS_FILE"
set +a

fail() {
  echo "ERROR: $*" >&2
  ERRORS=$((ERRORS + 1))
}

value_of() {
  local name="$1"
  printf '%s' "${!name-}"
}

require_value() {
  local name="$1"
  [[ -n "$(value_of "$name")" ]] || fail "${name} is required for ${PHASE}"
}

require_guid() {
  local name="$1"
  local value
  value="$(value_of "$name")"
  [[ "$value" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] ||
    fail "${name} must be a GUID"
}

require_positive_integer() {
  local name="$1"
  local value
  value="$(value_of "$name")"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] ||
    fail "${name} must be a positive integer"
}

require_boolean() {
  local name="$1"
  local value
  value="$(value_of "$name")"
  [[ "$value" == true || "$value" == false ]] ||
    fail "${name} must be true or false"
}

bootstrap_values=(
  TARGET_TENANT_ID
  TARGET_SUBSCRIPTION_ID
  LOCATION
  SEARCH_LOCATION
  RESOURCE_GROUP
  NAME_PREFIX
  ENVIRONMENT_NAME
  APP_NAME
  TAG_ENVIRONMENT
  TAG_OWNER
  VNET_ADDRESS_SPACE
  CHAT_MODEL_NAME
  CHAT_MODEL_VERSION
  CHAT_MODEL_SKU
  CHAT_MODEL_CAPACITY
  EMBEDDING_MODEL_NAME
  EMBEDDING_MODEL_VERSION
  EMBEDDING_MODEL_SKU
  EMBEDDING_MODEL_CAPACITY
  DIRECTIVE_MODEL_NAME
  DIRECTIVE_MODEL_VERSION
  DIRECTIVE_MODEL_SKU
  DIRECTIVE_MODEL_CAPACITY
  SEARCH_SKU
  DIRECTIVE_STORAGE_REPLICATION_TYPE
  DIRECTIVE_MODEL_MODE
  AGENT365_ENABLED
  RETAIN_TERRAFORM_DEPLOYER_DATA_ROLES
  DEPLOYMENT_RUN_ID
  SECURE_EVIDENCE_DIR
)
for name in "${bootstrap_values[@]}"; do
  require_value "$name"
done

require_guid TARGET_TENANT_ID
require_guid TARGET_SUBSCRIPTION_ID
require_positive_integer CHAT_MODEL_CAPACITY
require_positive_integer EMBEDDING_MODEL_CAPACITY
require_positive_integer DIRECTIVE_MODEL_CAPACITY
require_boolean AGENT365_ENABLED
require_boolean RETAIN_TERRAFORM_DEPLOYER_DATA_ROLES

[[ "$(value_of NAME_PREFIX)" =~ ^[a-z0-9]+$ ]] ||
  fail "NAME_PREFIX must contain only lowercase letters and digits"
[[ "$(value_of DIRECTIVE_MODEL_MODE)" == fresh \
  || "$(value_of DIRECTIVE_MODEL_MODE)" == adopt ]] ||
  fail "DIRECTIVE_MODEL_MODE must be fresh or adopt"
if [[ "$(value_of DIRECTIVE_MODEL_MODE)" == adopt ]]; then
  require_value DIRECTIVE_MODEL_IMPORT_ID
  [[ "$(value_of DIRECTIVE_MODEL_IMPORT_ID)" =~ ^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/deployments/[^/]+$ ]] ||
    fail "DIRECTIVE_MODEL_IMPORT_ID must be a directive deployment ARM resource ID"
elif [[ -n "$(value_of DIRECTIVE_MODEL_IMPORT_ID)" ]]; then
  fail "DIRECTIVE_MODEL_IMPORT_ID must be empty in fresh mode"
fi
case "$(value_of DIRECTIVE_STORAGE_REPLICATION_TYPE)" in
  LRS|ZRS|GRS|GZRS|RAGRS|RAGZRS) ;;
  *) fail "DIRECTIVE_STORAGE_REPLICATION_TYPE is unsupported" ;;
esac
[[ "$(value_of RETAIN_TERRAFORM_DEPLOYER_DATA_ROLES)" == true ]] ||
  fail "RETAIN_TERRAFORM_DEPLOYER_DATA_ROLES must be explicitly approved as true"
[[ "$(value_of DEPLOYMENT_RUN_ID)" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$ ]] ||
  fail "DEPLOYMENT_RUN_ID must be 3-80 safe filename characters"
[[ "$(value_of SECURE_EVIDENCE_DIR)" == /* ]] ||
  fail "SECURE_EVIDENCE_DIR must be an absolute path"
evidence_directory="$(value_of SECURE_EVIDENCE_DIR)"
if [[ -e "$evidence_directory" && ! -d "$evidence_directory" ]]; then
  fail "SECURE_EVIDENCE_DIR exists but is not a directory"
elif [[ -d "$evidence_directory" ]]; then
  [[ -w "$evidence_directory" ]] ||
    fail "SECURE_EVIDENCE_DIR must be writable"
  evidence_marker="$evidence_directory/.agent-memory-rag-deployment-id"
  if [[ ! -f "$evidence_marker" ]]; then
    fail "existing SECURE_EVIDENCE_DIR has no deployment marker"
  elif [[ "$(cat "$evidence_marker")" != "$(value_of DEPLOYMENT_RUN_ID)" ]]; then
    fail "existing SECURE_EVIDENCE_DIR belongs to another deployment run"
  fi
  evidence_path="$(cd "$evidence_directory" && pwd -P)"
else
  evidence_parent="$(dirname "$evidence_directory")"
  [[ -d "$evidence_parent" && -w "$evidence_parent" ]] ||
    fail "SECURE_EVIDENCE_DIR parent must exist and be writable"
  if [[ -d "$evidence_parent" ]]; then
    evidence_path="$(
      cd "$evidence_parent"
      printf '%s/%s' "$(pwd -P)" "$(basename "$evidence_directory")"
    )"
  else
    evidence_path=""
  fi
fi
if [[ -n "${evidence_path:-}" ]]; then
  case "${evidence_path}/" in
    "${REPO_ROOT}/"*)
      fail "SECURE_EVIDENCE_DIR must be outside the extracted deployment package"
      ;;
  esac
fi

if [[ "$PHASE" == terraform || "$PHASE" == release || "$PHASE" == agent-roles ]]; then
  require_guid DEPLOYER_OBJECT_ID
  require_guid ENTRA_CLIENT_ID
  require_value ENTRA_API_SCOPE
  require_value PROMPT_AGENT_RELEASE_ID
  require_value DIRECTIVE_AGENT_RELEASE_ID
  expected_scope="api://$(value_of ENTRA_CLIENT_ID)/access_as_user"
  [[ "$(value_of ENTRA_API_SCOPE)" == "$expected_scope" ]] ||
    fail "ENTRA_API_SCOPE must equal ${expected_scope}"
fi

if [[ "$PHASE" == release || "$PHASE" == agent-roles ]]; then
  release_values=(
    SUPPORT_AZD_ENVIRONMENT
    DIRECTIVE_AZD_ENVIRONMENT
    APP_IMAGE_TAG
    SUPPORT_IMAGE_TAG
    DIRECTIVE_IMAGE_TAG
    INGESTION_RELEASE_ID
  )
  for name in "${release_values[@]}"; do
    require_value "$name"
  done
  if [[ -z "$(value_of DIRECTIVE_SOURCE_USER_IDS)" \
    && -z "$(value_of DIRECTIVE_SOURCE_GROUP_IDS)" ]]; then
    fail "at least one directive-source user or group object ID is required"
  fi
fi

if [[ "$PHASE" == agent-roles ]]; then
  require_guid SUPPORT_AGENT_PRINCIPAL_ID
  require_guid DIRECTIVE_AGENT_PRINCIPAL_ID
  require_guid PROJECT_AGENT_PRINCIPAL_ID
  if [[ "$(value_of AGENT365_ENABLED)" == true ]]; then
    require_value AGENT365_LICENSED_USER_UPN
  fi
fi

if [[ "$ERRORS" -ne 0 ]]; then
  echo "Input validation failed with ${ERRORS} error(s)." >&2
  exit 1
fi

echo "Inputs valid for phase: ${PHASE}"
