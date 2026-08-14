#!/usr/bin/env bash
# Build the directive ingestion image through ACR Tasks and update only its job.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
MANDATE_FILE="$REPO_ROOT/setup/directives/mandatory/mand.csv"

if [[ ! -f "$MANDATE_FILE" ]]; then
  echo "ERROR: target mandate file is missing: $MANDATE_FILE" >&2
  echo "Copy mand.csv.example to mand.csv and replace every sample identity." >&2
  exit 2
fi
if cmp -s "$MANDATE_FILE" "${MANDATE_FILE}.example"; then
  echo "ERROR: target mandate file still contains the sample assignment." >&2
  exit 2
fi
if [[ ! -s "$MANDATE_FILE" && "${ALLOW_EMPTY_DIRECTIVE_MANDATES:-false}" != true ]]; then
  echo "ERROR: target mandate file is empty." >&2
  echo "Set ALLOW_EMPTY_DIRECTIVE_MANDATES=true only to publish an intentional empty snapshot." >&2
  exit 2
fi

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

SUBSCRIPTION_ID="$(az account show --query id --output tsv)"
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
ARTIFACT_BLOB_CONTAINER="$(tf directive_artifacts_container)"
SOURCE_BLOB_CONTAINER="$(tf directive_source_container)"
DOCUMENT_INTELLIGENCE_NAME="$(tf directive_document_intelligence_name)"
SEARCH_NAME="$(tf search_service_name)"
FOUNDRY_SCOPE="$(tf foundry_agents_account_id)"
TAG="${1:-$(date +%Y%m%d%H%M%S)}"
REPOSITORY="directive-ingestion"
IMAGE="$ACR_LOGIN/$REPOSITORY:$TAG"
JOB_CONTAINER="directive-ingestion"
VALIDATION_CONFIRMATION="${DIRECTIVE_VALIDATE_CONFIRMATION:-}"
VERIFY_EVIDENCE_FILE="${DIRECTIVE_VERIFY_EVIDENCE_FILE:-}"
EXPECTED_PROCESSING_VERSION="directive-v2-czech-layout"
EXPECTED_SEARCH_INDEX="directive-chunks-v2"
INDEX_SCHEMA_FILE="$(mktemp)"
VALIDATION_SUMMARY_FILE="$(mktemp)"
VERIFY_SUMMARY_FILE="$(mktemp)"

ACR_SCOPE="$(
  az acr show --name "$ACR_NAME" --resource-group "$RG" --query id --output tsv
)"
STORAGE_ACCOUNT_SCOPE="$(
  az storage account show \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RG" \
    --query id \
    --output tsv
)"
ARTIFACT_STORAGE_SCOPE="$STORAGE_ACCOUNT_SCOPE/blobServices/default/containers/$ARTIFACT_BLOB_CONTAINER"
SOURCE_STORAGE_SCOPE="$STORAGE_ACCOUNT_SCOPE/blobServices/default/containers/$SOURCE_BLOB_CONTAINER"
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
RESTORE_PUBLICATION_MODE=false

cleanup() {
  local status=$?
  trap - EXIT
  rm -f \
    "$ARM_ROLE_SNAPSHOT" \
    "$COSMOS_ROLE_SNAPSHOT" \
    "$INDEX_SCHEMA_FILE" \
    "$VALIDATION_SUMMARY_FILE" \
    "$VERIFY_SUMMARY_FILE"
  if [[ "$RESTORE_PUBLICATION_MODE" == true ]]; then
    echo "==> Restoring the directive ingestion job publication mode"
    az containerapp job update \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --container-name "$JOB_CONTAINER" \
      --command directive-ingest \
      --args run-daily \
      --output none || echo "ERROR: failed to restore publication mode" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

EXPECTED_ARM_ROLES=(
  "AcrPull|$ACR_SCOPE"
  "Storage Blob Data Contributor|$ARTIFACT_STORAGE_SCOPE"
  "Storage Blob Data Reader|$SOURCE_STORAGE_SCOPE"
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

safe_summary_lines() {
  local raw_logs="$1"
  local output_file="$2"
  local line trimmed sanitized
  : >"$output_file"
  while IFS= read -r line; do
    trimmed="${line#"${line%%[![:space:]]*}"}"
    [[ "${trimmed:0:1}" == "{" ]] || continue
    sanitized="$(
      printf '%s\n' "$trimmed" | jq -ce '
        if type != "object" then empty else
          {
            status, run_id, source_count, directive_count, mandate_count,
            mandate_user_count, changed_count, skipped_count, chunk_count,
            published_chunks, published_directives, published_versions,
            current_directives, current_versions, mandate_assignment_count,
            acr_pull, document_intelligence, normalized_directive_ids,
            warnings, processing_version, search_index, source_versions,
            directive_ids
          } | with_entries(select(.value != null))
        end
      ' 2>/dev/null || true
    )"
    [[ -n "$sanitized" ]] && printf '%s\n' "$sanitized" >>"$output_file"
  done <<<"$raw_logs"
}

show_execution_logs() {
  local execution_name="$1"
  local raw_logs
  raw_logs="$(
    az containerapp job logs show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --execution "$execution_name" \
      --container "$JOB_CONTAINER" \
      --tail 300 \
      --format text 2>/dev/null || true
  )"
  case "${CURRENT_EXECUTION_LABEL:-}" in
    "Metadata validation") safe_summary_lines "$raw_logs" "$VALIDATION_SUMMARY_FILE" ;;
    "Directive verification") safe_summary_lines "$raw_logs" "$VERIFY_SUMMARY_FILE" ;;
    *) safe_summary_lines "$raw_logs" "$INDEX_SCHEMA_FILE" ;;
  esac
  if [[ -s "$VALIDATION_SUMMARY_FILE" && "${CURRENT_EXECUTION_LABEL:-}" == "Metadata validation" ]]; then
    cat "$VALIDATION_SUMMARY_FILE"
  elif [[ -s "$VERIFY_SUMMARY_FILE" && "${CURRENT_EXECUTION_LABEL:-}" == "Directive verification" ]]; then
    cat "$VERIFY_SUMMARY_FILE"
  else
    echo "[redacted] no approved ingestion summary lines were emitted"
  fi
}

confirm_validation() {
  local expected="$1"
  if [[ -n "$VALIDATION_CONFIRMATION" ]]; then
    [[ "$VALIDATION_CONFIRMATION" == "$expected" ]] || {
        echo "ERROR: DIRECTIVE_VALIDATE_CONFIRMATION must equal the token printed after validation" >&2
        return 1
    }
    return 0
  fi
  if [[ ! -t 0 ]]; then
    echo "ERROR: metadata validation requires operator confirmation" >&2
    echo "Set DIRECTIVE_VALIDATE_CONFIRMATION=$expected after inspecting the" >&2
    echo "validation summary, or run this command from a terminal." >&2
    return 1
  fi
  local answer
  read -r -p "Type $expected to publish the validated corpus: " answer
  [[ "$answer" == "$expected" ]]
}

assert_live_v2_config() {
  local live_image live_processing live_index
  live_image="$(
    az containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].image | [0]" \
      --output tsv
  )"
  live_processing="$(
    az containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].env[?name=='DIRECTIVE_PROCESSING_VERSION'].value | [0]" \
      --output tsv
  )"
  live_index="$(
    az containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].env[?name=='DIRECTIVE_SEARCH_INDEX'].value | [0]" \
      --output tsv
  )"
  [[ "$live_image" == "$IMAGE" ]] || {
    echo "ERROR: live job image is not the requested immutable image" >&2
    return 1
  }
  [[ "$live_processing" == "$EXPECTED_PROCESSING_VERSION" ]] || {
    echo "ERROR: live job processing version is not $EXPECTED_PROCESSING_VERSION" >&2
    return 1
  }
  [[ "$live_index" == "$EXPECTED_SEARCH_INDEX" ]] || {
    echo "ERROR: live job Search index is not $EXPECTED_SEARCH_INDEX" >&2
    return 1
  }
}

assert_v2_search_schema() {
  az rest \
    --method get \
    --url "https://${SEARCH_NAME}.search.windows.net/indexes/${EXPECTED_SEARCH_INDEX}?api-version=2026-04-01" \
    --resource "https://search.azure.com" \
    --output json >"$INDEX_SCHEMA_FILE"
  jq -e \
    --arg expected_index "$EXPECTED_SEARCH_INDEX" \
    '
      .name == $expected_index and
      ([.fields[]?.name] as $names |
        (["id", "directive_id", "directive_version_id", "is_valid",
          "content", "content_vector"] - $names | length) == 0) and
      ([.fields[]? | select(.name == "content_vector") | .dimensions]
        | any(. == 3072))
    ' "$INDEX_SCHEMA_FILE" >/dev/null || {
      echo "ERROR: v2 Search index schema is incompatible" >&2
      return 1
    }
}

run_image_cli_smoke() {
  command -v jq >/dev/null 2>&1 || {
    echo "ERROR: jq is required for safe summary and schema checks" >&2
    return 1
  }
  echo "==> Running directive-ingest CLI smoke check from the built image"
  az acr run \
    --registry "$ACR_NAME" \
    --cmd "$IMAGE directive-ingest --help" \
    /dev/null \
    --output none
}

require_one_summary_record() {
  local file="$1"
  local label="$2"
  [[ -s "$file" ]] || {
    echo "ERROR: $label did not emit a sanitized JSON summary" >&2
    return 1
  }
  [[ "$(wc -l <"$file" | tr -d ' ')" == "1" ]] || {
    echo "ERROR: $label emitted a partial or ambiguous summary" >&2
    return 1
  }
  jq -s -e 'length == 1 and (.[0] | type == "object")' "$file" >/dev/null || {
    echo "ERROR: $label summary is not a JSON object" >&2
    return 1
  }
}

validation_confirmation_token() {
  local summary_hash
  summary_hash="$(sha256_file "$VALIDATION_SUMMARY_FILE")"
  printf 'DIRECTIVE-PUBLISH-V2-%s\n' \
    "$(sha256_text "$VALIDATE_EXECUTION
$summary_hash" | cut -c1-24)"
}

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "ERROR: shasum or sha256sum is required" >&2
    return 1
  fi
}

sha256_text() {
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | awk '{print $1}'
  else
    echo "ERROR: shasum or sha256sum is required" >&2
    return 1
  fi
}

validate_metadata_summary() {
  require_one_summary_record "$VALIDATION_SUMMARY_FILE" "Metadata validation"
  jq -e \
    --arg processing "$EXPECTED_PROCESSING_VERSION" \
    --arg search_index "$EXPECTED_SEARCH_INDEX" \
    '
      (.normalized_directive_ids | type == "array") and
      (.warnings | type == "array") and
      .processing_version == $processing and
      .search_index == $search_index
    ' "$VALIDATION_SUMMARY_FILE" >/dev/null || {
    echo "ERROR: validation summary lacks normalized IDs, warnings, or exact v2 config" >&2
    return 1
  }
}

write_verification_evidence() {
  local source_record="$1"
  [[ -n "$VERIFY_EVIDENCE_FILE" ]] || return 0
  require_one_summary_record "$source_record" "Directive verification"
  jq -e \
    --arg subscription "$SUBSCRIPTION_ID" \
    --arg resource_group "$RG" \
    --arg job "$JOB_NAME" \
    --arg search_index "$EXPECTED_SEARCH_INDEX" \
    --arg processing "$EXPECTED_PROCESSING_VERSION" \
    --arg execution "$VERIFY_EXECUTION" \
    '
      {
        status: "succeeded",
        environment: {
          subscription_id: $subscription,
          resource_group: $resource_group,
          job_name: $job
        },
        search_index: $search_index,
        processing_version: $processing,
        execution_name: $execution,
        cross_store: {
          source_versions: .source_versions,
          directive_ids: .directive_ids,
          current_versions: .current_versions,
          published_chunks: .published_chunks,
          published_directives: .published_directives,
          published_versions: .published_versions,
          mandate_assignment_count: .mandate_assignment_count
        }
      }
    ' "$source_record" >"$VERIFY_EVIDENCE_FILE"
  jq -e '
    .status == "succeeded" and
    .environment.subscription_id != "" and
    .environment.resource_group != "" and
    .environment.job_name != "" and
    .search_index == "directive-chunks-v2" and
    .processing_version == "directive-v2-czech-layout" and
    .execution_name != "" and
    (.cross_store | to_entries | all(.value != null))
  ' "$VERIFY_EVIDENCE_FILE" >/dev/null || {
    echo "ERROR: verification summary is incomplete for v2 finalization" >&2
    return 1
  }
}

wait_for_execution() {
  local execution_name="$1"
  local label="$2"
  local max_attempts="$3"
  local delay_seconds="$4"
  local attempt status
  CURRENT_EXECUTION_LABEL="$label"
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
      Failed | Stopped | Degraded | Canceled)
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
run_image_cli_smoke

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
assert_live_v2_config

echo "==> Bootstrapping the v2 Search index explicitly"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --command directive-ingest \
  --args bootstrap \
  --output none
BOOTSTRAP_EXECUTION="$(
  az containerapp job start \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --query name \
    --output tsv
)"
if [[ -z "$BOOTSTRAP_EXECUTION" ]]; then
  echo "Container Apps did not return a bootstrap execution name" >&2
  exit 1
fi
echo "==> Bootstrap execution: $BOOTSTRAP_EXECUTION"
assert_execution_mode "$BOOTSTRAP_EXECUTION" "bootstrap"
wait_for_execution "$BOOTSTRAP_EXECUTION" "Search bootstrap" 120 10
assert_live_v2_config
assert_v2_search_schema

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

echo "==> Running metadata-only validation"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --command directive-ingest \
  --args validate \
  --output none
VALIDATE_EXECUTION="$(
  az containerapp job start \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --query name \
    --output tsv
)"
if [[ -z "$VALIDATE_EXECUTION" ]]; then
  echo "Container Apps did not return a validation execution name" >&2
  exit 1
fi
echo "==> Validation execution: $VALIDATE_EXECUTION"
assert_execution_mode "$VALIDATE_EXECUTION" "validate"
wait_for_execution "$VALIDATE_EXECUTION" "Metadata validation" 120 10
validate_metadata_summary
VALIDATION_CONFIRMATION_TOKEN="$(validation_confirmation_token)"
echo "==> Inspect the complete metadata summary above"
echo "publication_confirmation_token=$VALIDATION_CONFIRMATION_TOKEN"
confirm_validation "$VALIDATION_CONFIRMATION_TOKEN"

if [[ -z "${DIRECTIVE_APPROVED_VALIDATION_DIGEST:-}" ]] \
  || [[ -z "${DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST:-}" ]] \
  || [[ -z "${DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST:-}" ]]; then
  echo "Nonempty DIRECTIVE_APPROVED_*_DIGEST values are required before directive publication" >&2
  exit 1
fi

echo "==> Switching the directive ingestion job to publication mode"
assert_live_v2_config
assert_v2_search_schema
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --command directive-ingest \
  --args run-daily \
  --output none
RESTORE_PUBLICATION_MODE=true

echo "==> Starting directive ingestion"
EXECUTION_NAME="$(
  az containerapp job start \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --env-vars \
      "DIRECTIVE_APPROVED_VALIDATION_DIGEST=$DIRECTIVE_APPROVED_VALIDATION_DIGEST" \
      "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST=$DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST" \
      "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST=$DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST" \
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
    --env-vars \
      "DIRECTIVE_APPROVED_VALIDATION_DIGEST=$DIRECTIVE_APPROVED_VALIDATION_DIGEST" \
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
write_verification_evidence "$VERIFY_SUMMARY_FILE"

echo "==> Restoring the directive ingestion job publication mode"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --command directive-ingest \
  --args run-daily \
  --output none
RESTORE_PUBLICATION_MODE=false

echo "==> Directive ingestion succeeded: $EXECUTION_NAME"
