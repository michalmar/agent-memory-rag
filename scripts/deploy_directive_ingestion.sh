#!/usr/bin/env bash
# Build the directive ingestion image through ACR Tasks and update only its job.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/directive_infrastructure_guards.sh"
MANDATE_FILE="$REPO_ROOT/setup/directives/mandatory/mand.csv"
PHASE="${DIRECTIVE_INGEST_PHASE:-all}"
RECOVERY_CANDIDATES=()

load_recovery_candidates() {
  local candidates_file="$1"
  local execution_name
  RECOVERY_CANDIDATES=()
  while IFS= read -r execution_name || [[ -n "$execution_name" ]]; do
    RECOVERY_CANDIDATES+=("$execution_name")
  done <"$candidates_file"
}

adopt_recovered_publication_execution() {
  local recovery_status="$1"
  [[ "$recovery_status" -eq 0 && "${#RECOVERY_CANDIDATES[@]}" -eq 1 ]] || return 1
  STARTED_EXECUTION_NAME="${RECOVERY_CANDIDATES[0]}"
}

run_bash_compat_self_test() {
  local candidates_file execution_name execution_already_tracked=false
  candidates_file="$(mktemp)"
  printf '%s\n' "first-execution" " execution with spaces " >"$candidates_file"
  load_recovery_candidates "$candidates_file"
  rm -f "$candidates_file"
  [[ "${#RECOVERY_CANDIDATES[@]}" -eq 2 ]] || return 1
  [[ "${RECOVERY_CANDIDATES[0]}" == "first-execution" ]] || return 1
  [[ "${RECOVERY_CANDIDATES[1]}" == " execution with spaces " ]] || return 1
  RECOVERY_CANDIDATES=()
  [[ "${#RECOVERY_CANDIDATES[@]}" -eq 0 ]] || return 1
  : >"$candidates_file"
  load_recovery_candidates "$candidates_file"
  rm -f "$candidates_file"
  [[ "${#RECOVERY_CANDIDATES[@]}" -eq 0 ]] || return 1
  STARTED_EXECUTIONS=()
  if [[ "${#STARTED_EXECUTIONS[@]}" -gt 0 ]]; then
    for execution_name in "${STARTED_EXECUTIONS[@]}"; do
      execution_already_tracked=true
    done
  fi
  [[ "$execution_already_tracked" == false ]] || return 1
  [[ "${#STARTED_EXECUTIONS[@]}" -eq 0 ]] || return 1
  RECOVERY_CANDIDATES=("recovered-run")
  STARTED_EXECUTIONS=()
  adopt_recovered_publication_execution 0 || return 1
  [[ "$STARTED_EXECUTION_NAME" == recovered-run ]] || return 1
  RECOVERY_CANDIDATES=()
  STARTED_EXECUTIONS=()
  adopt_recovered_publication_execution 2 && return 1
  RECOVERY_CANDIDATES=("one" "two")
  STARTED_EXECUTIONS=()
  adopt_recovered_publication_execution 2 && return 1
  [[ "${#STARTED_EXECUTIONS[@]}" -eq 0 ]]
}

if [[ "${1:-}" == --self-test ]]; then
  run_bash_compat_self_test || {
    echo "ERROR: Bash 3 recovery candidate self-test failed" >&2
    exit 1
  }
  printf '%s\n' "deploy-directive-ingestion=bash3-self-test-pass"
  exit 0
fi

case "${1:-}" in
  validate|publish|all)
    PHASE="$1"
    shift
    ;;
esac

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
VALIDATION_EVIDENCE_FILE="${DIRECTIVE_VALIDATE_EVIDENCE_FILE:-}"
GENERATED_VALIDATION_EVIDENCE=false

die() {
  echo "ERROR: $*" >&2
  exit 1
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

[[ "$PHASE" == validate && -n "$VALIDATION_EVIDENCE_FILE" ]] || \
  [[ "$PHASE" != validate ]] || {
    echo "ERROR: validate phase requires DIRECTIVE_VALIDATE_EVIDENCE_FILE" >&2
    exit 2
  }
[[ "$PHASE" != publish || -n "$VALIDATION_EVIDENCE_FILE" ]] || {
  echo "ERROR: publish phase requires DIRECTIVE_VALIDATE_EVIDENCE_FILE" >&2
  exit 2
}
if [[ "$PHASE" == all && -z "$VALIDATION_EVIDENCE_FILE" ]]; then
  VALIDATION_EVIDENCE_FILE="$(mktemp)"
  GENERATED_VALIDATION_EVIDENCE=true
fi

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
COSMOS_CATALOG_CONTAINER="$(tf directive_catalog_container)"
COSMOS_CONTENT_CONTAINER="$(tf directive_content_container)"
COSMOS_MANDATE_CONTAINER="$(tf directive_mandates_container)"
STORAGE_ACCOUNT="$(tf directive_artifacts_storage_account)"
ARTIFACT_BLOB_CONTAINER="$(tf directive_artifacts_container)"
SOURCE_BLOB_CONTAINER="$(tf directive_source_container)"
SOURCE_PREFIX="$(tf directive_source_prefix)"
DOCUMENT_INTELLIGENCE_NAME="$(tf directive_document_intelligence_name)"
SEARCH_NAME="$(tf search_service_name)"
FOUNDRY_SCOPE="$(tf foundry_agents_account_id)"
TAG="${1:-$(date +%Y%m%d%H%M%S)}"
REPOSITORY="directive-ingestion"
IMAGE=""
IMAGE_DIGEST=""
JOB_CONTAINER="directive-ingestion"
VALIDATION_CONFIRMATION="${DIRECTIVE_VALIDATE_CONFIRMATION:-}"
VERIFY_EVIDENCE_FILE="${DIRECTIVE_VERIFY_EVIDENCE_FILE:-}"
EXPECTED_PROCESSING_VERSION="directive-v2-czech-layout"
EXPECTED_SEARCH_INDEX="directive-chunks-v2"
MAX_VALIDATION_EVIDENCE_AGE_SECONDS="${DIRECTIVE_VALIDATE_EVIDENCE_MAX_AGE_SECONDS:-86400}"
INDEX_SCHEMA_FILE="$(mktemp)"
EXPECTED_ENVIRONMENT_FILE="$(mktemp)"
VALIDATION_SUMMARY_FILE="$(mktemp)"
VERIFY_SUMMARY_FILE="$(mktemp)"
SOURCE_INVENTORY_FILE="$(mktemp)"
VALIDATION_RECORD_DIGEST=""
VALIDATION_PRODUCER_DIGEST=""
SOURCE_INVENTORY_DIGEST=""
EXPECTED_ENVIRONMENT_DIGEST=""
APPROVED_ENVIRONMENT_DIGEST=""
APPROVED_SOURCE_INVENTORY_DIGEST=""
STARTED_EXECUTIONS=()
PUBLICATION_MARKER_RESERVED=false
PUBLICATION_DISPATCH_ATTEMPTED=false
PUBLICATION_EXECUTION_SNAPSHOT="[]"

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
cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$PUBLICATION_MARKER_RESERVED" == true && "$PUBLICATION_DISPATCH_ATTEMPTED" != true ]]; then
    az storage blob delete \
      --account-name "$STORAGE_ACCOUNT" \
      --container-name "$ARTIFACT_BLOB_CONTAINER" \
      --name "publication-approval/$VALIDATION_PRODUCER_DIGEST.json" \
      --auth-mode login \
      --delete-snapshots include \
      --output none || echo "ERROR: failed to roll back unused publication approval reservation" >&2
    az storage blob delete \
      --account-name "$STORAGE_ACCOUNT" \
      --container-name "$ARTIFACT_BLOB_CONTAINER" \
      --name "publication-approval-provenance/$VALIDATION_PRODUCER_DIGEST.json" \
      --auth-mode login \
      --delete-snapshots include \
      --output none || echo "ERROR: failed to roll back approval provenance" >&2
  fi
  if [[ "$PUBLICATION_DISPATCH_ATTEMPTED" == true ]]; then
    recover_publication_execution "$PUBLICATION_EXECUTION_SNAPSHOT" || true
  fi
  if [[ "$status" -ne 0 && -n "${JOB_NAME:-}" ]]; then
    stop_started_executions
  fi
  rm -f \
    "$ARM_ROLE_SNAPSHOT" \
    "$COSMOS_ROLE_SNAPSHOT" \
    "$INDEX_SCHEMA_FILE" \
    "$EXPECTED_ENVIRONMENT_FILE" \
    "$VALIDATION_SUMMARY_FILE" \
    "$VERIFY_SUMMARY_FILE" \
    "$SOURCE_INVENTORY_FILE"
  if [[ "$GENERATED_VALIDATION_EVIDENCE" == true ]]; then
    rm -f "$VALIDATION_EVIDENCE_FILE"
  fi
  if [[ -n "${JOB_NAME:-}" ]]; then
    echo "==> Leaving the directive ingestion job in nonpublishing maintenance mode"
    az containerapp job update \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --container-name "$JOB_CONTAINER" \
      --command directive-ingest \
      --args maintenance \
      --output none || echo "ERROR: failed to leave maintenance mode" >&2
    assert_live_maintenance_mode || echo "ERROR: maintenance assertion failed" >&2
    assert_no_active_execution || echo "ERROR: active execution assertion failed" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

write_expected_environment() {
  jq -S -n \
    --arg source_kind azure_blob \
    --arg source_storage_account "$STORAGE_ACCOUNT" \
    --arg source_container "$SOURCE_BLOB_CONTAINER" \
    --arg source_prefix "$SOURCE_PREFIX" \
    --arg artifact_storage_account "$STORAGE_ACCOUNT" \
    --arg artifact_container "$ARTIFACT_BLOB_CONTAINER" \
    --arg cosmos_account "$COSMOS_ACCOUNT" \
    --arg cosmos_database "$COSMOS_DATABASE" \
    --arg catalog_container "$COSMOS_CATALOG_CONTAINER" \
    --arg content_container "$COSMOS_CONTENT_CONTAINER" \
    --arg mandate_container "$COSMOS_MANDATE_CONTAINER" \
    --arg search_service "$SEARCH_NAME" \
    --arg search_index "$EXPECTED_SEARCH_INDEX" \
    '{
      source_kind: $source_kind,
      source_storage_account: $source_storage_account,
      source_container: $source_container,
      source_prefix: $source_prefix,
      artifact_storage_account: $artifact_storage_account,
      artifact_container: $artifact_container,
      cosmos_account: $cosmos_account,
      cosmos_database: $cosmos_database,
      catalog_container: $catalog_container,
      content_container: $content_container,
      mandate_container: $mandate_container,
      search_service: $search_service,
      search_index: $search_index
    }' >"$EXPECTED_ENVIRONMENT_FILE"
}

write_expected_environment

EXPECTED_ENVIRONMENT_DIGEST="$(sha256_text "$(jq -S -c . "$EXPECTED_ENVIRONMENT_FILE")")"

stop_started_executions() {
  local execution_name status
  if [[ "${#STARTED_EXECUTIONS[@]}" -eq 0 ]]; then
    return 0
  fi
  for execution_name in "${STARTED_EXECUTIONS[@]}"; do
    if ! status="$(
      az containerapp job execution show \
        --name "$JOB_NAME" \
        --resource-group "$RG" \
        --job-execution-name "$execution_name" \
        --query properties.status \
        --output tsv 2>/dev/null
    )"; then
      status="__lookup_failed__"
    fi
    case "$status" in
      Succeeded|Failed|Stopped|Degraded|Canceled) continue ;;
    esac
    echo "==> Stopping failed-run execution $execution_name" >&2
    az containerapp job stop \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --output none || echo "ERROR: failed to stop execution $execution_name" >&2
  done
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if assert_no_active_execution; then
      return 0
    fi
    sleep 2
  done
  echo "ERROR: started executions did not drain after failure cleanup" >&2
  return 1
}

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

  if [[ "${#EXPECTED_ARM_ROLES[@]}" -gt 0 ]]; then
    for expected in "${EXPECTED_ARM_ROLES[@]}"; do
      expected_role="${expected%%|*}"
      expected_scope="${expected#*|}"
      has_exact_arm_role "$expected_role" "$expected_scope" || return 1
    done
  fi
  has_exact_cosmos_role
}

safe_summary_lines() {
  local raw_logs="$1"
  local output_file="$2"
  local raw_file
  raw_file="$(mktemp)"
  printf '%s\n' "$raw_logs" >"$raw_file"
  if ! directive_extract_producer_record "$raw_file" "$output_file"; then
    rm -f "$raw_file"
    echo "ERROR: ingestion logs must contain exactly one complete producer record" >&2
    return 1
  fi
  rm -f "$raw_file"
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
    "Metadata validation") safe_summary_lines "$raw_logs" "$VALIDATION_SUMMARY_FILE" || return 1 ;;
    "Directive verification") safe_summary_lines "$raw_logs" "$VERIFY_SUMMARY_FILE" || return 1 ;;
    *) : >"$INDEX_SCHEMA_FILE" ;;
  esac
  if [[ -s "$VALIDATION_SUMMARY_FILE" && "${CURRENT_EXECUTION_LABEL:-}" == "Metadata validation" ]]; then
    jq -c '{success, record_field_names: (keys | sort)}' "$VALIDATION_SUMMARY_FILE"
  elif [[ -s "$VERIFY_SUMMARY_FILE" && "${CURRENT_EXECUTION_LABEL:-}" == "Directive verification" ]]; then
    jq -c '{success, record_field_names: (keys | sort)}' "$VERIFY_SUMMARY_FILE"
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

reserve_publication_approval() {
  local marker_file provenance_file marker_name provenance_name
  marker_name="publication-approval/$VALIDATION_PRODUCER_DIGEST.json"
  provenance_name="publication-approval-provenance/$VALIDATION_PRODUCER_DIGEST.json"
  marker_file="$(mktemp)"
  provenance_file="$(mktemp)"
  jq -S -n \
    --arg record_schema "directive.approval.v2" \
    --arg validation_digest "$VALIDATION_PRODUCER_DIGEST" \
    --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
    --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
    --arg processing_hash "$(jq -r '.producer_record.processing_hash' "$VALIDATION_EVIDENCE_FILE")" \
    --arg mandate_checksum "$(jq -r '.producer_record.mandate_checksum' "$VALIDATION_EVIDENCE_FILE")" \
    '{
      record_schema: $record_schema,
      validation_digest: $validation_digest,
      source_inventory_digest: $source_digest,
      environment_digest: $environment_digest,
      processing_hash: $processing_hash,
      mandate_checksum: $mandate_checksum
    }' >"$marker_file"
  jq -S -n \
    --arg image_digest "$IMAGE_DIGEST" \
    --arg subscription "$SUBSCRIPTION_ID" \
    --arg resource_group "$RG" \
    --arg job "$JOB_NAME" \
    --arg processing_version "$EXPECTED_PROCESSING_VERSION" \
    --arg search_index "$EXPECTED_SEARCH_INDEX" \
    --arg validation_digest "$VALIDATION_PRODUCER_DIGEST" \
    '{
      validation_digest: $validation_digest,
      image_digest: $image_digest,
      subscription_id: $subscription,
      resource_group: $resource_group,
      job_name: $job,
      processing_version: $processing_version,
      search_index: $search_index
    }' >"$provenance_file"
  if ! az storage blob upload \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$ARTIFACT_BLOB_CONTAINER" \
    --name "$marker_name" \
    --file "$marker_file" \
    --auth-mode login \
    --if-none-match "*" \
    --output none; then
    rm -f "$marker_file" "$provenance_file"
    die "Publication approval has already been consumed or could not be reserved"
  fi
  PUBLICATION_MARKER_RESERVED=true
  if ! az storage blob upload \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$ARTIFACT_BLOB_CONTAINER" \
    --name "$provenance_name" \
    --file "$provenance_file" \
    --auth-mode login \
    --if-none-match "*" \
    --output none; then
    rm -f "$marker_file" "$provenance_file"
    die "Publication approval provenance could not be reserved"
  fi
  rm -f "$marker_file" "$provenance_file"
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

assert_live_maintenance_mode() {
  local actual_command actual_args
  actual_command="$(
    az containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "join(' ', properties.template.containers[?name=='$JOB_CONTAINER'].command)" \
      --output tsv
  )"
  actual_args="$(
    az containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "join(' ', properties.template.containers[?name=='$JOB_CONTAINER'].args)" \
      --output tsv
  )"
  [[ "$actual_command" == "directive-ingest" && "$actual_args" == "maintenance" ]] || {
    echo "ERROR: directive job template is not in nonpublishing maintenance mode" >&2
    return 1
  }
}

assert_no_active_execution() {
  local active
  if ! active="$(
    az containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "[?properties.status!='Succeeded' && properties.status!='Failed' && properties.status!='Stopped' && properties.status!='Degraded' && properties.status!='Canceled'].name" \
      --output tsv
  )"; then
    echo "ERROR: unable to query Container Apps executions" >&2
    return 1
  fi
  [[ -z "$active" ]] || {
    echo "ERROR: active directive execution(s) must drain before a phase change: $active" >&2
    return 1
  }
}

ensure_maintenance_mode() {
  assert_no_active_execution
  az containerapp job update \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --container-name "$JOB_CONTAINER" \
    --command directive-ingest \
    --args maintenance \
    --output none
  assert_live_maintenance_mode
}

assert_execution_image() {
  local execution_name="$1"
  local actual_image
  actual_image="$(
    az containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].image | [0]" \
      --output tsv
  )"
  [[ "$actual_image" == "$IMAGE" ]] || {
    echo "ERROR: execution $execution_name did not use pinned image $IMAGE" >&2
    return 1
  }
}

track_started_execution() {
  local execution_name="$1"
  local known
  [[ -n "$execution_name" ]] || return 1
  if [[ "${#STARTED_EXECUTIONS[@]}" -gt 0 ]]; then
    for known in "${STARTED_EXECUTIONS[@]}"; do
      [[ "$known" == "$execution_name" ]] && {
        STARTED_EXECUTION_NAME="$execution_name"
        return 0
      }
    done
  fi
  STARTED_EXECUTIONS+=("$execution_name")
  STARTED_EXECUTION_NAME="$execution_name"
}

snapshot_execution_ids() {
  az containerapp job execution list \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --output json
}

recover_publication_execution() {
  local before_json="$1" executions
  local candidates_file recovery_status=0 execution_name
  [[ "$PUBLICATION_DISPATCH_ATTEMPTED" == true ]] || return 0
  executions="$(
    az containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --output json 2>/dev/null
  )" || return 1
  candidates_file="$(mktemp)"
  if directive_select_new_approved_execution_names \
      "$before_json" "$executions" run-daily "$IMAGE" \
      "$EXPECTED_ENVIRONMENT_DIGEST" "$APPROVED_SOURCE_INVENTORY_DIGEST" \
      "$VALIDATION_PRODUCER_DIGEST" "$EXPECTED_PROCESSING_VERSION" \
      "$EXPECTED_SEARCH_INDEX" >"$candidates_file"
  then
    recovery_status=0
  else
    recovery_status=$?
  fi
  load_recovery_candidates "$candidates_file"
  rm -f "$candidates_file"
  if [[ "${#RECOVERY_CANDIDATES[@]}" -gt 0 ]]; then
    for execution_name in "${RECOVERY_CANDIDATES[@]}"; do
      [[ -n "$execution_name" ]] && track_started_execution "$execution_name"
    done
  fi
  adopt_recovered_publication_execution "$recovery_status"
}

start_job_execution() {
  local expected_argument="$1"
  local execution_name execution_name_file start_args=()
  local execution_snapshot
  assert_live_maintenance_mode
  assert_no_active_execution
  if [[ "$expected_argument" == run-daily || "$expected_argument" == verify ]]; then
    [[ "$VALIDATION_PRODUCER_DIGEST" =~ ^[0-9a-f]{64}$ ]] || die \
      "Approved validation digest is invalid"
    [[ "$EXPECTED_ENVIRONMENT_DIGEST" =~ ^[0-9a-f]{64}$ ]] || die \
      "Approved environment digest is invalid"
    [[ "$APPROVED_SOURCE_INVENTORY_DIGEST" =~ ^[0-9a-f]{64}$ ]] || die \
      "Approved source inventory digest is invalid"
    start_args=(
      --env-vars
      "DIRECTIVE_APPROVED_VALIDATION_DIGEST=$VALIDATION_PRODUCER_DIGEST"
      "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST=$EXPECTED_ENVIRONMENT_DIGEST"
      "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST=$APPROVED_SOURCE_INVENTORY_DIGEST"
      --query name
      --output tsv
    )
  else
    start_args=(--query name --output tsv)
  fi
  if [[ "$expected_argument" == run-daily ]]; then
    execution_snapshot="$(snapshot_execution_ids)" || die \
      "Could not snapshot executions before publication dispatch"
    PUBLICATION_EXECUTION_SNAPSHOT="$execution_snapshot"
    PUBLICATION_DISPATCH_ATTEMPTED=true
  fi
  execution_name_file="$(mktemp)"
  if ! az containerapp job start \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --command directive-ingest \
      --args "$expected_argument" \
      "${start_args[@]}" >"$execution_name_file"
  then
    rm -f "$execution_name_file"
    if [[ "$expected_argument" == run-daily ]]; then
      recover_publication_execution "$execution_snapshot" || return 1
      execution_name="$STARTED_EXECUTION_NAME"
    else
      return 1
    fi
  else
    execution_name="$(<"$execution_name_file")"
    rm -f "$execution_name_file"
  fi
  if [[ -z "$execution_name" ]]; then
    if [[ "$expected_argument" == run-daily ]]; then
      recover_publication_execution "$execution_snapshot" || return 1
      execution_name="$STARTED_EXECUTION_NAME"
    else
      echo "ERROR: Container Apps did not return an execution name" >&2
      return 1
    fi
  fi
  track_started_execution "$execution_name"
  assert_execution_mode "$execution_name" "$expected_argument"
  assert_execution_image "$execution_name"
  local execution_container
  execution_container="$(
    az containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'] | [0]" \
      --output json
  )"
  if [[ "$expected_argument" == run-daily || "$expected_argument" == verify ]]; then
    directive_assert_approved_execution_json \
      "$execution_container" "$expected_argument" "$IMAGE" \
      "$EXPECTED_ENVIRONMENT_DIGEST" "$APPROVED_SOURCE_INVENTORY_DIGEST" \
      "$VALIDATION_PRODUCER_DIGEST" "$EXPECTED_PROCESSING_VERSION" \
      "$EXPECTED_SEARCH_INDEX"
  else
    directive_assert_unapproved_execution_json \
      "$execution_container" "$expected_argument"
  fi
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

resolve_image_digest() {
  IMAGE_DIGEST="$(
    az acr manifest show-metadata \
      --registry "$ACR_NAME" \
      --name "$REPOSITORY:$TAG" \
      --query digest \
      --output tsv
  )"
  [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: ACR did not return an immutable image manifest digest" >&2
    return 1
  }
  IMAGE="$ACR_LOGIN/$REPOSITORY@$IMAGE_DIGEST"
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
  [[ "$(wc -c <"$file" | tr -d ' ')" -le 65536 ]] || {
    echo "ERROR: $label summary exceeds the safe record size limit" >&2
    return 1
  }
  jq -s -e 'length == 1 and (.[0] | type == "object")' "$file" >/dev/null || {
    echo "ERROR: $label summary is not a JSON object" >&2
    return 1
  }
}

validation_confirmation_token() {
  local record_digest
  record_digest="$(sha256_file "$VALIDATION_EVIDENCE_FILE")"
  printf 'DIRECTIVE-PUBLISH-V2-%s\n' \
    "$(sha256_text "$record_digest
$IMAGE_DIGEST
$SOURCE_INVENTORY_DIGEST
$EXPECTED_ENVIRONMENT_DIGEST
$VALIDATION_PRODUCER_DIGEST" | cut -c1-24)"
}

refresh_source_inventory() {
  local source_count blob_name relative source_hash extension source_prefix prefix_length
  source_prefix="$(tf directive_source_prefix)"
  : >"$SOURCE_INVENTORY_FILE"
  az storage blob list \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$SOURCE_BLOB_CONTAINER" \
    --prefix "$source_prefix" \
    --auth-mode login \
    --query "[].name" \
    --output tsv |
    while IFS= read -r blob_name; do
      extension="$(printf '%s' "${blob_name##*.}" | tr '[:upper:]' '[:lower:]')"
      [[ "$extension" == pdf ]] || continue
      prefix_length=${#source_prefix}
      [[ "${blob_name:0:prefix_length}" == "$source_prefix" ]] || {
        echo "ERROR: source blob is outside the literal configured prefix: $blob_name" >&2
        return 1
      }
      relative="${blob_name:prefix_length}"
      [[ "$relative" != */* && -n "$relative" ]] || {
        echo "ERROR: source blob is not a direct PDF child: $blob_name" >&2
        return 1
      }
      source_hash="$(
        source_file="$(mktemp)"
        trap 'rm -f "$source_file"' EXIT
        az storage blob download \
          --account-name "$STORAGE_ACCOUNT" \
          --container-name "$SOURCE_BLOB_CONTAINER" \
          --name "$blob_name" \
          --file "$source_file" \
          --auth-mode login \
          --overwrite \
          --output none
        sha256_file "$source_file"
      )"
      printf '%s\t%s\n' "$relative" "$source_hash"
    done >"$SOURCE_INVENTORY_FILE"
  source_count="$(awk 'NF { count++ } END { print count + 0 }' "$SOURCE_INVENTORY_FILE")"
  [[ "$source_count" -gt 0 ]] || {
    echo "ERROR: directive-source corpus is empty" >&2
    return 1
  }
  SOURCE_INVENTORY_DIGEST="$(
    sha256_text "$(
      LC_ALL=C sort "$SOURCE_INVENTORY_FILE" |
        jq -RnSc '[inputs | split("\t") | {source_name: .[0], source_hash: .[1]}] | sort_by(.source_name)'
    )"
  )"
}

load_validation_evidence() {
  [[ -s "$VALIDATION_EVIDENCE_FILE" ]] || {
    echo "ERROR: validation evidence file is required" >&2
    return 1
  }
  jq -s -e 'length == 1 and (.[0] | type == "object")' \
    "$VALIDATION_EVIDENCE_FILE" >/dev/null || {
    echo "ERROR: validation evidence must be one JSON object" >&2
    return 1
  }
  local created_at now age
  created_at="$(jq -r '.evidence_created_at // 0' "$VALIDATION_EVIDENCE_FILE")"
  now="$(date +%s)"
  age=$((now - created_at))
  [[ "$age" -ge 0 && "$age" -le "$MAX_VALIDATION_EVIDENCE_AGE_SECONDS" ]] || {
    echo "ERROR: validation evidence is stale or timestamped in the future" >&2
    return 1
  }
  IMAGE_DIGEST="$(jq -r '.wrapper.image_digest // empty' "$VALIDATION_EVIDENCE_FILE")"
  IMAGE="$ACR_LOGIN/$REPOSITORY@$IMAGE_DIGEST"
  VALIDATE_EXECUTION="$(jq -r '.wrapper.validation_execution_id // empty' "$VALIDATION_EVIDENCE_FILE")"
  SOURCE_INVENTORY_DIGEST="$(jq -r '.wrapper.source_inventory_digest // empty' "$VALIDATION_EVIDENCE_FILE")"
  APPROVED_SOURCE_INVENTORY_DIGEST="$SOURCE_INVENTORY_DIGEST"
  APPROVED_ENVIRONMENT_DIGEST="$(jq -r '.wrapper.environment_digest // empty' "$VALIDATION_EVIDENCE_FILE")"
  VALIDATION_RECORD_DIGEST="$(jq -r '.wrapper.validation_record_digest // empty' "$VALIDATION_EVIDENCE_FILE")"
  VALIDATION_PRODUCER_DIGEST="$(jq -r '.producer_record.validation_digest // empty' "$VALIDATION_EVIDENCE_FILE")"
  [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation evidence image digest is not immutable" >&2
    return 1
  }
  [[ -n "$VALIDATE_EXECUTION" && "$SOURCE_INVENTORY_DIGEST" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation evidence is missing execution or source digest" >&2
    return 1
  }
  [[ "$APPROVED_ENVIRONMENT_DIGEST" == "$EXPECTED_ENVIRONMENT_DIGEST" ]] || {
    echo "ERROR: validation evidence environment digest differs from live Terraform" >&2
    return 1
  }
  [[ "$VALIDATION_RECORD_DIGEST" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation evidence record digest is invalid" >&2
    return 1
  }
  [[ "$VALIDATION_PRODUCER_DIGEST" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation producer digest is invalid" >&2
    return 1
  }
}

write_validation_evidence() {
  [[ -n "$VALIDATION_EVIDENCE_FILE" ]] || {
    echo "ERROR: validation phase requires DIRECTIVE_VALIDATE_EVIDENCE_FILE" >&2
    return 1
  }
  local canonical_record record_digest created_at evidence_nonce
  processing_hash="$(jq -r '.processing_hash // empty' "$VALIDATION_SUMMARY_FILE")"
  [[ "$processing_hash" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation summary processing_hash is invalid" >&2
    return 1
  }
  canonical_record="$(
    jq -S -c \
      --arg image_digest "$IMAGE_DIGEST" \
      --arg image_reference "$IMAGE" \
      --arg execution "$VALIDATE_EXECUTION" \
      --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
      --arg subscription "$SUBSCRIPTION_ID" \
      --arg resource_group "$RG" \
      --arg job "$JOB_NAME" \
      --arg processing_version "$EXPECTED_PROCESSING_VERSION" \
      --arg search_index "$EXPECTED_SEARCH_INDEX" \
      --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
      '
        {
          producer_record: .,
          wrapper: {
            image_digest: $image_digest,
            image_reference: $image_reference,
            environment_digest: $environment_digest,
            source_inventory_digest: $source_digest,
            validation_execution_id: $execution,
            subscription_id: $subscription,
            resource_group: $resource_group,
            job_name: $job,
            processing_version: $processing_version,
            search_index: $search_index
          }
        }
    ' "$VALIDATION_SUMMARY_FILE"
  )"
  record_digest="$(sha256_text "$canonical_record")"
  created_at="$(date +%s)"
  evidence_nonce="$(sha256_text "$created_at:$RANDOM:$$:$record_digest" | cut -c1-32)"
  jq -S -c \
    --arg digest "$record_digest" \
    --argjson created_at "$created_at" \
    --arg nonce "$evidence_nonce" \
    '.wrapper.validation_record_digest = $digest |
     . + {evidence_created_at: $created_at, evidence_nonce: $nonce}' \
    <(printf '%s\n' "$canonical_record") >"$VALIDATION_EVIDENCE_FILE"
  VALIDATION_RECORD_DIGEST="$record_digest"
  VALIDATION_PRODUCER_DIGEST="$(jq -r '.validation_digest' "$VALIDATION_SUMMARY_FILE")"
  APPROVED_SOURCE_INVENTORY_DIGEST="$SOURCE_INVENTORY_DIGEST"
  APPROVED_ENVIRONMENT_DIGEST="$EXPECTED_ENVIRONMENT_DIGEST"
  echo "validation_record_digest=$VALIDATION_RECORD_DIGEST"
}

validate_metadata_summary() {
  require_one_summary_record "$VALIDATION_SUMMARY_FILE" "Metadata validation"
  local validated_file
  validated_file="$(mktemp)"
  directive_validate_producer_record \
    "$VALIDATION_SUMMARY_FILE" "$validated_file" \
    directive.validate.v2 "$EXPECTED_ENVIRONMENT_FILE" \
    "$SOURCE_INVENTORY_DIGEST" "$EXPECTED_PROCESSING_VERSION" \
    "$EXPECTED_SEARCH_INDEX" || {
    rm -f "$validated_file"
    echo "ERROR: validation summary does not match the complete v2 contract" >&2
    return 1
  }
  mv "$validated_file" "$VALIDATION_SUMMARY_FILE"
}

revalidate_validation_evidence() {
  local raw_logs canonical_record actual_digest evidence_digest
  assert_execution_image "$VALIDATE_EXECUTION"
  assert_execution_mode "$VALIDATE_EXECUTION" validate
  raw_logs="$(
    az containerapp job logs show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --execution "$VALIDATE_EXECUTION" \
      --container "$JOB_CONTAINER" \
      --tail 300 \
      --format text
  )"
  safe_summary_lines "$raw_logs" "$VALIDATION_SUMMARY_FILE"
  validate_metadata_summary
  [[ "$(jq -r '.source_inventory_digest' "$VALIDATION_SUMMARY_FILE")" == "$SOURCE_INVENTORY_DIGEST" ]] || \
    die "Validation execution source inventory differs from its evidence"
  [[ "$(jq -r '.producer_record.source_inventory_digest' "$VALIDATION_EVIDENCE_FILE")" == "$SOURCE_INVENTORY_DIGEST" ]] || \
    die "Validation evidence source inventory differs from the live corpus"
  canonical_record="$(
    jq -S -c '{producer_record: .}' "$VALIDATION_SUMMARY_FILE"
  )"
  canonical_record="$(
    jq -S -c \
      --arg image_digest "$IMAGE_DIGEST" \
      --arg image_reference "$IMAGE" \
      --arg execution "$VALIDATE_EXECUTION" \
      --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
      --arg subscription "$SUBSCRIPTION_ID" \
      --arg resource_group "$RG" \
      --arg job "$JOB_NAME" \
      --arg processing_version "$EXPECTED_PROCESSING_VERSION" \
      --arg search_index "$EXPECTED_SEARCH_INDEX" \
      --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
      '. + {
        wrapper: {
          image_digest: $image_digest,
          image_reference: $image_reference,
          environment_digest: $environment_digest,
          source_inventory_digest: $source_digest,
          validation_execution_id: $execution,
          subscription_id: $subscription,
          resource_group: $resource_group,
          job_name: $job,
          processing_version: $processing_version,
          search_index: $search_index
        }
      }' <(printf '%s\n' "$canonical_record")
  )"
  actual_digest="$(sha256_text "$canonical_record")"
  evidence_digest="$(jq -r '.wrapper.validation_record_digest' "$VALIDATION_EVIDENCE_FILE")"
  [[ "$actual_digest" == "$evidence_digest" ]] || die \
    "Validation evidence does not match the pinned Azure execution output"
  VALIDATION_RECORD_DIGEST="$actual_digest"
  VALIDATION_PRODUCER_DIGEST="$(jq -r '.validation_digest' "$VALIDATION_SUMMARY_FILE")"
  [[ "$(jq -r '.wrapper.environment_digest' "$VALIDATION_EVIDENCE_FILE")" == "$EXPECTED_ENVIRONMENT_DIGEST" ]] || \
    die "Validation evidence environment digest changed"
  [[ "$(jq -r '.producer_record.validation_digest' "$VALIDATION_EVIDENCE_FILE")" == "$VALIDATION_PRODUCER_DIGEST" ]] || \
    die "Validation evidence producer digest changed"
}

write_verification_evidence() {
  local source_record="$1"
  [[ -n "$VERIFY_EVIDENCE_FILE" ]] || return 0
  require_one_summary_record "$source_record" "Directive verification"
  local canonical_record record_digest validated_record
  validated_record="$(mktemp)"
  directive_validate_producer_record \
    "$source_record" "$validated_record" \
    directive.verify.v2 "$EXPECTED_ENVIRONMENT_FILE" \
    "$SOURCE_INVENTORY_DIGEST" "$EXPECTED_PROCESSING_VERSION" \
    "$EXPECTED_SEARCH_INDEX" || {
    rm -f "$validated_record"
    echo "ERROR: verification summary is incomplete for v2 finalization" >&2
    return 1
  }
  [[ "$(jq -r '.validation_digest' "$validated_record")" == "$VALIDATION_PRODUCER_DIGEST" ]] || {
    rm -f "$validated_record"
    echo "ERROR: verification is not linked to the approved validation digest" >&2
    return 1
  }
  canonical_record="$(
    jq -S -c \
      --arg image_digest "$IMAGE_DIGEST" \
      --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
      --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
      --arg validation_execution "$VALIDATE_EXECUTION" \
      --arg validation_digest "$VALIDATION_RECORD_DIGEST" \
      --arg validation_producer_digest "$VALIDATION_PRODUCER_DIGEST" \
      --arg execution "$VERIFY_EXECUTION" \
      --arg subscription "$SUBSCRIPTION_ID" \
      --arg resource_group "$RG" \
      --arg job "$JOB_NAME" \
      --arg processing_version "$EXPECTED_PROCESSING_VERSION" \
      --arg search_index "$EXPECTED_SEARCH_INDEX" \
      '
        {
          producer_record: .,
          wrapper: {
            image_digest: $image_digest,
            environment_digest: $environment_digest,
            source_inventory_digest: $source_digest,
            validation_execution_id: $validation_execution,
            validation_record_digest: $validation_digest,
            validation_producer_digest: $validation_producer_digest,
            verification_execution_id: $execution,
            subscription_id: $subscription,
            resource_group: $resource_group,
            job_name: $job,
            processing_version: $processing_version,
            search_index: $search_index
          }
        }
      ' "$validated_record"
  )"
  rm -f "$validated_record"
  record_digest="$(sha256_text "$canonical_record")"
  jq -S -c \
    --arg digest "$record_digest" \
    '.wrapper.verification_record_digest = $digest | .' \
    <(printf '%s\n' "$canonical_record") >"$VERIFY_EVIDENCE_FILE"
  jq -e \
    --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
    --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
    --arg validation_digest "$VALIDATION_PRODUCER_DIGEST" \
    '
      .producer_record.search_index == "directive-chunks-v2" and
      .producer_record.processing_version == "directive-v2-czech-layout" and
      .producer_record.validation_digest == $validation_digest and
      .wrapper.environment_digest == $environment_digest and
      .wrapper.source_inventory_digest == $source_digest
    ' "$VERIFY_EVIDENCE_FILE" >/dev/null || {
    echo "ERROR: verification evidence environment or v2 configuration mismatch" >&2
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
  local attempt container_json
  for attempt in {1..30}; do
    container_json="$(
      az containerapp job execution show \
        --name "$JOB_NAME" \
        --resource-group "$RG" \
        --job-execution-name "$execution_name" \
        --query "properties.template.containers[?name=='$JOB_CONTAINER'] | [0]" \
        --output json 2>/dev/null || true
    )"
    if directive_assert_execution_mode_json "$container_json" "$expected_argument" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Execution $execution_name did not use directive-ingest $expected_argument" >&2
  return 1
}

if [[ "$PHASE" == publish ]]; then
  load_validation_evidence
else
  echo "==> Building directive ingestion image through ACR Tasks"
  az acr build \
    --registry "$ACR_NAME" \
    --image "$REPOSITORY:$TAG" \
    --file "$REPO_ROOT/setup/directive_ingest/Dockerfile" \
    "$REPO_ROOT"
  resolve_image_digest
fi

echo "==> Registry : $ACR_LOGIN"
echo "==> Job      : $JOB_NAME"
echo "==> Identity : $IDENTITY_PRINCIPAL_ID"
echo "==> Image    : $IMAGE"
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

echo "==> Pinning the live job image while keeping its template nonpublishing"
az containerapp job update \
  --name "$JOB_NAME" \
  --resource-group "$RG" \
  --container-name "$JOB_CONTAINER" \
  --image "$IMAGE" \
  --command directive-ingest \
  --args maintenance \
  --output none
assert_live_v2_config
ensure_maintenance_mode

if [[ "$PHASE" != publish ]]; then
  echo "==> Bootstrapping the v2 Search index through a per-execution override"
  start_job_execution bootstrap
  BOOTSTRAP_EXECUTION="$STARTED_EXECUTION_NAME"
  echo "==> Bootstrap execution: $BOOTSTRAP_EXECUTION"
  wait_for_execution "$BOOTSTRAP_EXECUTION" "Search bootstrap" 120 10
  assert_live_v2_config
  assert_v2_search_schema

  echo "==> Running managed-identity data-plane preflight"
  PREFLIGHT_SUCCEEDED=false
  for attempt in {1..5}; do
    ensure_maintenance_mode
    start_job_execution preflight
    PREFLIGHT_EXECUTION="$STARTED_EXECUTION_NAME"
    echo "==> Preflight execution: $PREFLIGHT_EXECUTION"
    if wait_for_execution "$PREFLIGHT_EXECUTION" "Preflight" 120 10; then
      PREFLIGHT_SUCCEEDED=true
      break
    fi
    [[ "$attempt" -lt 5 ]] && sleep 60
  done
  [[ "$PREFLIGHT_SUCCEEDED" == true ]] || die "Managed-identity preflight failed"

  refresh_source_inventory
  echo "==> Running metadata-only validation"
  start_job_execution validate
  VALIDATE_EXECUTION="$STARTED_EXECUTION_NAME"
  echo "==> Validation execution: $VALIDATE_EXECUTION"
  wait_for_execution "$VALIDATE_EXECUTION" "Metadata validation" 120 10
  validate_metadata_summary
  [[ "$(jq -r '.source_inventory_digest' "$VALIDATION_SUMMARY_FILE")" == "$SOURCE_INVENTORY_DIGEST" ]] || \
    die "Source inventory changed during metadata validation"
  write_validation_evidence
  revalidate_validation_evidence
  VALIDATION_CONFIRMATION_TOKEN="$(validation_confirmation_token)"
  echo "==> Validation evidence: $VALIDATION_EVIDENCE_FILE"
  echo "publication_confirmation_token=$VALIDATION_CONFIRMATION_TOKEN"
  [[ "$PHASE" == validate ]] && exit 0
else
  [[ "$(jq -r '.confirmation_token // empty' "$VALIDATION_EVIDENCE_FILE")" == "" ]] || \
    die "Validation evidence must not contain a reusable confirmation token"
  refresh_source_inventory
  [[ "$SOURCE_INVENTORY_DIGEST" == "$(jq -r '.producer_record.source_inventory_digest' "$VALIDATION_EVIDENCE_FILE")" ]] || \
    die "Source inventory changed since validation evidence was issued"
  status="$(
    az containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$VALIDATE_EXECUTION" \
      --query properties.status \
      --output tsv
  )"
  [[ "$status" == Succeeded ]] || die "Pinned validation execution is not successful"
  revalidate_validation_evidence
  VALIDATION_CONFIRMATION_TOKEN="$(validation_confirmation_token)"
fi

confirm_validation "$VALIDATION_CONFIRMATION_TOKEN"
if [[ -z "${DIRECTIVE_APPROVED_VALIDATION_DIGEST:-}" ]] \
  || [[ -z "${DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST:-}" ]] \
  || [[ -z "${DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST:-}" ]]; then
  echo "Nonempty DIRECTIVE_APPROVED_*_DIGEST values are required before directive publication" >&2
  exit 1
fi

assert_live_v2_config
assert_live_maintenance_mode
assert_v2_search_schema
refresh_source_inventory
[[ "$SOURCE_INVENTORY_DIGEST" == "$(jq -r '.producer_record.source_inventory_digest' "$VALIDATION_EVIDENCE_FILE")" ]] || \
  die "Source inventory changed immediately before publication"
[[ "$SOURCE_INVENTORY_DIGEST" == "$APPROVED_SOURCE_INVENTORY_DIGEST" ]] || \
  die "Source inventory changed since approval evidence was issued"

echo "==> Starting approved publication through a per-execution override"
reserve_publication_approval
start_job_execution run-daily
EXECUTION_NAME="$STARTED_EXECUTION_NAME"
echo "==> Ingestion execution: $EXECUTION_NAME"
wait_for_execution "$EXECUTION_NAME" "Directive ingestion" 240 30

echo "==> Verifying published directive state"
start_job_execution verify
VERIFY_EXECUTION="$STARTED_EXECUTION_NAME"
echo "==> Verification execution: $VERIFY_EXECUTION"
wait_for_execution "$VERIFY_EXECUTION" "Directive verification" 120 10
refresh_source_inventory
[[ "$SOURCE_INVENTORY_DIGEST" == "$(jq -r '.producer_record.source_inventory_digest' "$VALIDATION_EVIDENCE_FILE")" ]] || \
  die "Source inventory changed before verification evidence was captured"
write_verification_evidence "$VERIFY_SUMMARY_FILE"

echo "==> Directive ingestion succeeded: $EXECUTION_NAME; job remains in maintenance mode"
