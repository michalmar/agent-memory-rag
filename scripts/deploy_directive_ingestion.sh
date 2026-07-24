#!/usr/bin/env bash
# Build the directive ingestion image through ACR Tasks and update only its job.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

RG="$(tf resource_group)"
ACR_NAME="$(tf acr_name)"
ACR_LOGIN="$(tf acr_login_server)"
JOB_NAME="$(tf directive_ingestion_job_name)"
IDENTITY_PRINCIPAL_ID="$(tf directive_ingestion_identity_principal_id)"
COSMOS_ENDPOINT="$(tf cosmos_endpoint)"
COSMOS_ACCOUNT="${COSMOS_ENDPOINT#https://}"
COSMOS_ACCOUNT="${COSMOS_ACCOUNT%%.*}"
COSMOS_DATABASE="$(tf directive_cosmos_database)"
STORAGE_ACCOUNT="$(tf directive_artifacts_storage_account)"
BLOB_CONTAINER="$(tf directive_artifacts_container)"
DOCUMENT_INTELLIGENCE_NAME="$(tf directive_document_intelligence_name)"
SEARCH_NAME="$(tf search_service_name)"
FOUNDRY_SCOPE="$(tf foundry_agents_account_id)"
TAG="${1:-$(date +%Y%m%d%H%M%S)}"
REPOSITORY="directive-ingestion"
IMAGE="$ACR_LOGIN/$REPOSITORY:$TAG"
JOB_CONTAINER="directive-ingestion"

ACR_SCOPE="$(
  az acr show --name "$ACR_NAME" --resource-group "$RG" --query id --output tsv
)"
STORAGE_SCOPE="$(
  az storage account show \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RG" \
    --query id \
    --output tsv
)/blobServices/default/containers/$BLOB_CONTAINER"
DOCUMENT_INTELLIGENCE_SCOPE="$(
  az cognitiveservices account show \
    --name "$DOCUMENT_INTELLIGENCE_NAME" \
    --resource-group "$RG" \
    --query id \
    --output tsv
)"
SEARCH_SCOPE="$(
  az search service show \
    --name "$SEARCH_NAME" \
    --resource-group "$RG" \
    --query id \
    --output tsv
)"
COSMOS_ACCOUNT_SCOPE="$(
  az cosmosdb show \
    --name "$COSMOS_ACCOUNT" \
    --resource-group "$RG" \
    --query id \
    --output tsv
)"
COSMOS_SCOPE="$COSMOS_ACCOUNT_SCOPE/dbs/$COSMOS_DATABASE"
COSMOS_ROLE_DEFINITION="$COSMOS_ACCOUNT_SCOPE/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"

ARM_ROLE_SNAPSHOT="$(mktemp)"
COSMOS_ROLE_SNAPSHOT="$(mktemp)"
trap 'rm -f "$ARM_ROLE_SNAPSHOT" "$COSMOS_ROLE_SNAPSHOT"' EXIT

EXPECTED_ARM_ROLES=(
  "AcrPull|$ACR_SCOPE"
  "Storage Blob Data Contributor|$STORAGE_SCOPE"
  "Cognitive Services User|$DOCUMENT_INTELLIGENCE_SCOPE"
  "Search Service Contributor|$SEARCH_SCOPE"
  "Search Index Data Contributor|$SEARCH_SCOPE"
  "Cognitive Services OpenAI User|$FOUNDRY_SCOPE"
)

same_scope() {
  local left right
  left="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  right="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
  [[ "$left" == "$right" ]]
}

has_exact_arm_role() {
  local expected_role="$1"
  local expected_scope="$2"
  local actual_role actual_scope
  while IFS=$'\t' read -r actual_role actual_scope; do
    if [[ "$actual_role" == "$expected_role" ]] \
      && same_scope "$actual_scope" "$expected_scope"; then
      return 0
    fi
  done <"$ARM_ROLE_SNAPSHOT"
  return 1
}

has_exact_cosmos_role() {
  local actual_definition actual_scope
  while IFS=$'\t' read -r actual_definition actual_scope; do
    if same_scope "$actual_definition" "$COSMOS_ROLE_DEFINITION" \
      && same_scope "$actual_scope" "$COSMOS_SCOPE"; then
      return 0
    fi
  done <"$COSMOS_ROLE_SNAPSHOT"
  return 1
}

roles_are_ready() {
  local expected expected_role expected_scope
  az role assignment list \
    --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
    --all \
    --query "[].{role:roleDefinitionName,scope:scope}" \
    --output tsv >"$ARM_ROLE_SNAPSHOT"
  az cosmosdb sql role assignment list \
    --account-name "$COSMOS_ACCOUNT" \
    --resource-group "$RG" \
    --query \
      "[?principalId=='$IDENTITY_PRINCIPAL_ID'].[roleDefinitionId,scope]" \
    --output tsv >"$COSMOS_ROLE_SNAPSHOT"

  for expected in "${EXPECTED_ARM_ROLES[@]}"; do
    expected_role="${expected%%|*}"
    expected_scope="${expected#*|}"
    has_exact_arm_role "$expected_role" "$expected_scope" || return 1
  done
  has_exact_cosmos_role
}

show_execution_logs() {
  local execution_name="$1"
  az containerapp job logs show \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --execution "$execution_name" \
    --container "$JOB_CONTAINER" \
    --tail 300 \
    --format text || true
}

wait_for_execution() {
  local execution_name="$1"
  local label="$2"
  local max_attempts="$3"
  local delay_seconds="$4"
  local attempt status
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    status="$(
      az containerapp job execution show \
        --name "$JOB_NAME" \
        --resource-group "$RG" \
        --job-execution-name "$execution_name" \
        --query properties.status \
        --output tsv
    )"
    case "$status" in
      Succeeded)
        show_execution_logs "$execution_name"
        return 0
        ;;
      Failed | Stopped | Degraded)
        show_execution_logs "$execution_name"
        echo "$label execution ended with status $status" >&2
        return 1
        ;;
    esac
    sleep "$delay_seconds"
  done
  show_execution_logs "$execution_name"
  echo "$label execution did not finish in the allowed time" >&2
  return 1
}

assert_execution_mode() {
  local execution_name="$1"
  local expected_argument="$2"
  local attempt actual_command actual_arguments
  for attempt in {1..30}; do
    actual_command="$(
      az containerapp job execution show \
        --name "$JOB_NAME" \
        --resource-group "$RG" \
        --job-execution-name "$execution_name" \
        --query "join(' ', properties.template.containers[0].command)" \
        --output tsv 2>/dev/null || true
    )"
    actual_arguments="$(
      az containerapp job execution show \
        --name "$JOB_NAME" \
        --resource-group "$RG" \
        --job-execution-name "$execution_name" \
        --query "join(' ', properties.template.containers[0].args)" \
        --output tsv 2>/dev/null || true
    )"
    if [[ "$actual_command" == "directive-ingest" ]] \
      && [[ "$actual_arguments" == "$expected_argument" ]]; then
      return 0
    fi
    sleep 2
  done
  echo "Execution $execution_name did not use directive-ingest $expected_argument" >&2
  return 1
}

echo "==> Registry : $ACR_LOGIN"
echo "==> Job      : $JOB_NAME"
echo "==> Identity : $IDENTITY_PRINCIPAL_ID"
echo "==> Image    : $IMAGE"

echo "==> Building directive ingestion image through ACR Tasks"
az acr build \
  --registry "$ACR_NAME" \
  --image "$REPOSITORY:$TAG" \
  --file "$REPO_ROOT/setup/directive_ingest/Dockerfile" \
  "$REPO_ROOT"

echo "==> Waiting for the job identity to be visible in Azure RBAC"
for attempt in {1..30}; do
  if roles_are_ready; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo "Exact job role assignments are not visible after 10 minutes" >&2
    echo "ARM assignments:" >&2
    cat "$ARM_ROLE_SNAPSHOT" >&2
    echo "Cosmos assignments:" >&2
    cat "$COSMOS_ROLE_SNAPSHOT" >&2
    exit 1
  fi
  sleep 20
done

echo "==> Updating the directive ingestion job image in preflight mode"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --image "$IMAGE" \
  --command directive-ingest \
  --args preflight \
  --output none

echo "==> Running managed-identity data-plane preflight"
PREFLIGHT_SUCCEEDED=false
for attempt in {1..5}; do
  PREFLIGHT_EXECUTION="$(
    az containerapp job start \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query name \
      --output tsv
  )"
  if [[ -z "$PREFLIGHT_EXECUTION" ]]; then
    echo "Container Apps did not return a preflight execution name" >&2
    exit 1
  fi
  echo "==> Preflight execution: $PREFLIGHT_EXECUTION"
  assert_execution_mode "$PREFLIGHT_EXECUTION" "preflight"
  if wait_for_execution "$PREFLIGHT_EXECUTION" "Preflight" 120 10; then
    PREFLIGHT_SUCCEEDED=true
    break
  fi
  if [[ "$attempt" -lt 5 ]]; then
    echo "==> Waiting for data-plane role propagation before retry"
    sleep 60
  fi
done
if [[ "$PREFLIGHT_SUCCEEDED" != true ]]; then
  echo "Managed-identity preflight failed after five attempts" >&2
  exit 1
fi

echo "==> Switching the directive ingestion job to publication mode"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --command directive-ingest \
  --args run-daily \
  --output none

echo "==> Starting directive ingestion"
EXECUTION_NAME="$(
  az containerapp job start \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --query name \
    --output tsv
)"
if [[ -z "$EXECUTION_NAME" ]]; then
  echo "Container Apps did not return an ingestion execution name" >&2
  exit 1
fi
echo "==> Ingestion execution: $EXECUTION_NAME"
assert_execution_mode "$EXECUTION_NAME" "run-daily"
wait_for_execution "$EXECUTION_NAME" "Directive ingestion" 240 30

echo "==> Verifying published directive state"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --command directive-ingest \
  --args verify \
  --output none
VERIFY_EXECUTION="$(
  az containerapp job start \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --query name \
    --output tsv
)"
if [[ -z "$VERIFY_EXECUTION" ]]; then
  echo "Container Apps did not return a verification execution name" >&2
  exit 1
fi
echo "==> Verification execution: $VERIFY_EXECUTION"
assert_execution_mode "$VERIFY_EXECUTION" "verify"
wait_for_execution "$VERIFY_EXECUTION" "Directive verification" 120 10

echo "==> Restoring the directive ingestion job publication mode"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --command directive-ingest \
  --args run-daily \
  --output none

echo "==> Directive ingestion succeeded: $EXECUTION_NAME"
