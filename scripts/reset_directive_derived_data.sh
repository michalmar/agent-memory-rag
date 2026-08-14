#!/usr/bin/env bash
# Guarded destructive reset and Search cutover for directive v2.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/directive_infrastructure_guards.sh"

AZ_BIN="${AZ_BIN:-az}"
TERRAFORM_BIN="${TERRAFORM_BIN:-terraform}"
MAX_INVENTORY_EVIDENCE_AGE_SECONDS="${DIRECTIVE_RESET_EVIDENCE_MAX_AGE_SECONDS:-1800}"
MAINTENANCE_DRAIN_ATTEMPTS="${DIRECTIVE_MAINTENANCE_DRAIN_ATTEMPTS:-60}"
MAINTENANCE_DRAIN_DELAY_SECONDS="${DIRECTIVE_MAINTENANCE_DRAIN_DELAY_SECONDS:-10}"
V1_INDEX="${DIRECTIVE_SEARCH_V1_INDEX:-directive-chunks-v1}"
V2_INDEX="directive-chunks-v2"
JOB_CONTAINER="directive-ingestion"
JOB_CPU="1"
JOB_MEMORY="2Gi"
PROCESSING_VERSION="directive-v2-czech-layout"
MODE="dry-run"
EXECUTE_FLAG=false
CONFIRMATION_TOKEN=""
VERIFICATION_FILE=""
INVENTORY_EVIDENCE_FILE=""
SOURCE_INVENTORY_FILE=""
EXPECTED_ENVIRONMENT_FILE="$(mktemp)"
EXPECTED_ENVIRONMENT_DIGEST=""
SOURCE_INVENTORY_DIGEST=""
SOURCE_COUNT=0
STARTED_EXECUTIONS=()
FRESH_VERIFY_ENV_VARS=()
COSMOS_PLAN_FILE=""
COSMOS_PLAN_JSON_FILE=""
MAINTENANCE_TOUCHED=false
APPROVED_VALIDATION_DIGEST=""
APPROVED_ENVIRONMENT_DIGEST=""
APPROVED_SOURCE_INVENTORY_DIGEST=""
RECOVERED_EXECUTION_NAME=""
RECOVERY_CANDIDATES=()

read -r -a AZ_CMD <<<"$AZ_BIN"
read -r -a TERRAFORM_CMD <<<"$TERRAFORM_BIN"

usage() {
  cat <<'EOF'
Usage:
  reset_directive_derived_data.sh [dry-run]
  reset_directive_derived_data.sh dry-run --inventory-evidence FILE
  reset_directive_derived_data.sh reset --execute --inventory-evidence FILE \
    --confirm TOKEN
  reset_directive_derived_data.sh finalize --execute \
    --verification-file FILE --inventory-evidence FILE --confirm TOKEN

The reset and finalize commands are destructive. A dry-run is the default and
prints the exact environment inventory and a confirmation token. Execute
requires the persisted dry-run evidence file and expires it after 30 minutes.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

load_recovery_candidates() {
  local candidates_file="$1"
  local execution_name
  RECOVERY_CANDIDATES=()
  while IFS= read -r execution_name || [[ -n "$execution_name" ]]; do
    RECOVERY_CANDIDATES+=("$execution_name")
  done <"$candidates_file"
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
  stop_started_executions
  if [[ "${#STARTED_EXECUTIONS[@]}" -gt 0 ]]; then
    for execution_name in "${STARTED_EXECUTIONS[@]}"; do
      execution_already_tracked=true
    done
  fi
  [[ "$execution_already_tracked" == false ]] || return 1
  [[ "${#STARTED_EXECUTIONS[@]}" -eq 0 ]]
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$status" -ne 0 && -n "${JOB_NAME:-}" &&
    ( "$MAINTENANCE_TOUCHED" == true || "${#STARTED_EXECUTIONS[@]}" -gt 0 ) ]]; then
    stop_started_executions || true
  fi
  if [[ -n "${JOB_NAME:-}" &&
    ( "$MAINTENANCE_TOUCHED" == true || "${#STARTED_EXECUTIONS[@]}" -gt 0 ) ]]; then
    "${AZ_CMD[@]}" containerapp job update \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --container-name directive-ingestion \
      --command directive-ingest \
      --args maintenance \
      --output none || true
    assert_no_active_execution || true
  fi
  [[ -z "$SOURCE_INVENTORY_FILE" ]] || rm -f "$SOURCE_INVENTORY_FILE"
  rm -f "$EXPECTED_ENVIRONMENT_FILE"
  [[ -z "$COSMOS_PLAN_FILE" ]] || rm -f "$COSMOS_PLAN_FILE"
  [[ -z "$COSMOS_PLAN_JSON_FILE" ]] || rm -f "$COSMOS_PLAN_JSON_FILE"
  exit "$status"
}
trap cleanup EXIT

sha256_text() {
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | awk '{print $1}'
  else
    die "shasum or sha256sum is required to bind the confirmation token"
  fi
}

sha256_file() {
  local file="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    die "shasum or sha256sum is required to bind the confirmation token"
  fi
}

stop_started_executions() {
  local execution_name status
  if [[ "${#STARTED_EXECUTIONS[@]}" -eq 0 ]]; then
    return 0
  fi
  for execution_name in "${STARTED_EXECUTIONS[@]}"; do
    if ! status="$(
      "${AZ_CMD[@]}" containerapp job execution show \
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
    "${AZ_CMD[@]}" containerapp job stop \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --output none || true
  done
  wait_for_active_executions_to_drain || true
}

if [[ "${1:-}" == --self-test ]]; then
  run_bash_compat_self_test || die "Bash 3 recovery candidate self-test failed"
  printf '%s\n' "reset-directive-derived-data=bash3-self-test-pass"
  exit 0
fi

tf_output() {
  "${TERRAFORM_CMD[@]}" -chdir="$INFRA_DIR" output -raw "$1"
}

account_name_from_endpoint() {
  local endpoint="$1"
  endpoint="${endpoint#https://}"
  printf '%s\n' "${endpoint%%.*}"
}

parse_args() {
  local arg
  if [[ $# -gt 0 ]]; then
    case "$1" in
      dry-run) MODE="dry-run"; shift ;;
      reset) MODE="reset"; shift ;;
      finalize) MODE="finalize"; shift ;;
      --help|-h) usage; exit 0 ;;
    esac
  fi

  while [[ $# -gt 0 ]]; do
    arg="$1"
    case "$arg" in
      --execute)
        EXECUTE_FLAG=true
        shift
        ;;
      --confirm)
        [[ $# -ge 2 ]] || die "--confirm requires a token"
        CONFIRMATION_TOKEN="$2"
        shift 2
        ;;
      --verification-file)
        [[ $# -ge 2 ]] || die "--verification-file requires a path"
        VERIFICATION_FILE="$2"
        shift 2
        ;;
      --inventory-evidence)
        [[ $# -ge 2 ]] || die "--inventory-evidence requires a path"
        INVENTORY_EVIDENCE_FILE="$2"
        shift 2
        ;;
      --v1-index)
        [[ $# -ge 2 ]] || die "--v1-index requires a name"
        V1_INDEX="$2"
        shift 2
        ;;
      --v2-index)
        [[ $# -ge 2 ]] || die "--v2-index requires a name"
        [[ "$2" == "directive-chunks-v2" ]] || die \
          "--v2-index must be the explicit target directive-chunks-v2"
        V2_INDEX="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        die "unknown argument: $arg"
        ;;
    esac
  done
}

load_inventory() {
  SUBSCRIPTION_ID="$("${AZ_CMD[@]}" account show --query id --output tsv)"
  local blob_name relative source_hash extension source_prefix_length
  SUBSCRIPTION_NAME="$("${AZ_CMD[@]}" account show --query name --output tsv)"
  RG="$(tf_output resource_group)"
  STORAGE_ACCOUNT="$(tf_output directive_artifacts_storage_account)"
  ARTIFACT_CONTAINER="$(tf_output directive_artifacts_container)"
  SOURCE_CONTAINER="$(tf_output directive_source_container)"
  SOURCE_PREFIX="$(tf_output directive_source_prefix)"
  source_prefix_length=${#SOURCE_PREFIX}
  rm -f "$SOURCE_INVENTORY_FILE"
  SOURCE_INVENTORY_FILE="$(mktemp)"
  "${AZ_CMD[@]}" storage blob list \
    --account-name "$STORAGE_ACCOUNT" \
    --container-name "$SOURCE_CONTAINER" \
    --prefix "$SOURCE_PREFIX" \
    --auth-mode login \
    --query "[].name" \
    --output tsv |
    while IFS= read -r blob_name; do
      extension="$(printf '%s' "${blob_name##*.}" | tr '[:upper:]' '[:lower:]')"
      [[ "$extension" == pdf ]] || continue
      [[ "${blob_name:0:source_prefix_length}" == "$SOURCE_PREFIX" ]] || die \
        "Source listing returned a blob outside the literal source prefix"
      relative="${blob_name:source_prefix_length}"
      [[ "$relative" != */* && -n "$relative" ]] || die \
        "Source blob is not a direct PDF child: $blob_name"
      source_hash="$(
        source_file="$(mktemp)"
        trap 'rm -f "$source_file"' EXIT
        "${AZ_CMD[@]}" storage blob download \
          --account-name "$STORAGE_ACCOUNT" \
          --container-name "$SOURCE_CONTAINER" \
          --name "$blob_name" \
          --file "$source_file" \
          --auth-mode login \
          --overwrite \
          --output none
        sha256_file "$source_file"
      )"
      printf '%s\t%s\n' "$relative" "$source_hash"
    done >"$SOURCE_INVENTORY_FILE"
  SOURCE_COUNT="$(awk 'NF { count++ } END { print count + 0 }' "$SOURCE_INVENTORY_FILE")"
  [[ "$SOURCE_COUNT" -gt 0 ]] || die \
    "Protected directive-source corpus is empty; refusing reset or token generation"
  SOURCE_INVENTORY_DIGEST="$(
    sha256_text "$(
      LC_ALL=C sort "$SOURCE_INVENTORY_FILE" |
        jq -RnSc '[inputs | split("\t") | {source_name: .[0], source_hash: .[1]}] | sort_by(.source_name)'
    )"
  )"
  COSMOS_ENDPOINT="$(tf_output cosmos_endpoint)"
  COSMOS_ACCOUNT="$(account_name_from_endpoint "$COSMOS_ENDPOINT")"
  COSMOS_DATABASE="$(tf_output directive_cosmos_database)"
  CATALOG_CONTAINER="$(tf_output directive_catalog_container)"
  CONTENT_CONTAINER="$(tf_output directive_content_container)"
  MANDATES_CONTAINER="$(tf_output directive_mandates_container)"
  SEARCH_NAME="$(tf_output search_service_name)"
  JOB_NAME="$(tf_output directive_ingestion_job_name)"
  jq -S -n \
    --arg source_kind azure_blob \
    --arg source_storage_account "$STORAGE_ACCOUNT" \
    --arg source_container "$SOURCE_CONTAINER" \
    --arg source_prefix "$SOURCE_PREFIX" \
    --arg artifact_storage_account "$STORAGE_ACCOUNT" \
    --arg artifact_container "$ARTIFACT_CONTAINER" \
    --arg cosmos_account "$COSMOS_ACCOUNT" \
    --arg cosmos_database "$COSMOS_DATABASE" \
    --arg catalog_container "$CATALOG_CONTAINER" \
    --arg content_container "$CONTENT_CONTAINER" \
    --arg mandate_container "$MANDATES_CONTAINER" \
    --arg search_service "$SEARCH_NAME" \
    --arg search_index "$V2_INDEX" \
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
  EXPECTED_ENVIRONMENT_DIGEST="$(sha256_text "$(jq -S -c . "$EXPECTED_ENVIRONMENT_FILE")")"
  ACTIVE_EXECUTIONS="$(
    "${AZ_CMD[@]}" containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "[?properties.status!='Succeeded' && properties.status!='Failed' && properties.status!='Stopped' && properties.status!='Degraded' && properties.status!='Canceled'].name" \
      --output tsv
  )"
  ACTIVE_EXECUTIONS="${ACTIVE_EXECUTIONS//$'\n'/,}"
}

inventory_text() {
  cat <<EOF
subscription_id=$SUBSCRIPTION_ID
subscription_name=$SUBSCRIPTION_NAME
resource_group=$RG
storage_account=$STORAGE_ACCOUNT
artifact_container=$ARTIFACT_CONTAINER
source_container=$SOURCE_CONTAINER
source_prefix=${SOURCE_PREFIX:-<root>}
source_count=$SOURCE_COUNT
source_inventory_digest=$SOURCE_INVENTORY_DIGEST
artifact_prefix=directives/
artifact_prefix=source-state/
artifact_prefix=quarantine/ (obsolete quarantine)
artifact_prefix=publication-approval/
artifact_prefix=publication-approval-provenance/
artifact_prefix=publication-commit/
artifact_prefix=publication-lock/
artifact_prefix=publication-claims/
cosmos_account=$COSMOS_ACCOUNT
cosmos_database=$COSMOS_DATABASE
cosmos_container=$CATALOG_CONTAINER partition_key=/directive_id
cosmos_container=$CONTENT_CONTAINER partition_key=/directive_version_id
cosmos_container=$MANDATES_CONTAINER partition_key=/user_id
search_service=$SEARCH_NAME
search_v1_index=$V1_INDEX
search_v2_index=$V2_INDEX
job_name=$JOB_NAME
active_executions=${ACTIVE_EXECUTIONS:-<none>}
protected_source=$STORAGE_ACCOUNT/$SOURCE_CONTAINER (NEVER delete or mutate)
EOF
}

token_inventory_text() {
  inventory_text | sed 's/^active_executions=.*/active_executions=<must-drain-before-delete>/'
}

confirmation_token_for() {
  local kind="$1"
  local created_at="$2"
  local nonce="$3"
  local state_digest="${4:-}"
  local image_digest="${5:-}"
  local verification_hash="${6:-}"
  local validation_digest="${7:-}"
  local environment_digest="${8:-$EXPECTED_ENVIRONMENT_DIGEST}"
  local mandate_checksum="${9:-}"
  local prefix="DIRECTIVE-RESET-V2"
  [[ "$kind" == finalize ]] && prefix="DIRECTIVE-FINALIZE-V2"
  printf '%s-%s\n' "$prefix" "$(
    sha256_text "$(token_inventory_text)
evidence_created_at=$created_at
evidence_nonce=$nonce
state_digest=$state_digest
image_digest=$image_digest
verification_hash=$verification_hash
validation_digest=$validation_digest
environment_digest=$environment_digest
mandate_checksum=$mandate_checksum" | cut -c1-24
  )"
}

write_inventory_evidence() {
  local token="$1"
  local kind="$2"
  local created_at="$3"
  local nonce="$4"
  local inventory_hash verification_hash="" state_digest="" image_digest="" validation_digest=""
  local mandate_checksum=""
  [[ -n "$INVENTORY_EVIDENCE_FILE" ]] || return 0
  inventory_hash="$(sha256_text "$(token_inventory_text)")"
  if [[ "$kind" == finalize ]]; then
    state_digest="$(jq -r '.producer_record.state_digest' "$VERIFICATION_FILE")"
    image_digest="$(jq -r '.wrapper.image_digest' "$VERIFICATION_FILE")"
    validation_digest="$(jq -r '.producer_record.validation_digest' "$VERIFICATION_FILE")"
    mandate_checksum="$(jq -r '.producer_record.mandate_checksum' "$VERIFICATION_FILE")"
    verification_hash="$(sha256_file "$VERIFICATION_FILE")"
  fi
  jq -S -n \
    --arg kind "$kind" \
    --argjson created_at "$created_at" \
    --arg nonce "$nonce" \
    --arg token "$token" \
    --arg inventory_hash "$inventory_hash" \
    --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
    --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
    --arg v2 "$V2_INDEX" \
    --arg verification_hash "$verification_hash" \
    --arg state_digest "$state_digest" \
    --arg image_digest "$image_digest" \
    --arg validation_digest "$validation_digest" \
    --arg mandate_checksum "$mandate_checksum" \
    --arg processing_version "directive-v2-czech-layout" \
    --arg search_index "$V2_INDEX" \
    '{
      kind: $kind,
      created_at: $created_at,
      nonce: $nonce,
      confirmation_token: $token,
      inventory_hash: $inventory_hash,
      source_inventory_digest: $source_digest,
      environment_digest: $environment_digest,
      v2_index: $v2,
      verification_evidence_sha256: $verification_hash,
      state_digest: ($state_digest | if . == "" then null else . end),
      verification_image_digest: ($image_digest | if . == "" then null else . end),
      verification_validation_digest: ($validation_digest | if . == "" then null else . end),
      verification_mandate_checksum: ($mandate_checksum | if . == "" then null else . end),
      verification_processing_version: ($processing_version | if $kind == "finalize" then . else null end),
      verification_search_index: ($search_index | if $kind == "finalize" then . else null end)
    }' >"$INVENTORY_EVIDENCE_FILE"
}

validate_inventory_evidence() {
  local now created_at age expected_kind expected_token verification_hash="" expected_derived_token
  [[ -s "$INVENTORY_EVIDENCE_FILE" ]] || die \
    "Execute requires a persisted fresh dry-run inventory evidence file"
  jq -e 'type == "object" and (.nonce | type == "string" and test("^[0-9a-f]{32}$"))' \
    "$INVENTORY_EVIDENCE_FILE" >/dev/null || die \
    "Inventory evidence is malformed"
  created_at="$(jq -r '.created_at // 0' "$INVENTORY_EVIDENCE_FILE")"
  now="$(date +%s)"
  age=$((now - created_at))
  [[ "$age" -ge 0 && "$age" -le "$MAX_INVENTORY_EVIDENCE_AGE_SECONDS" ]] || die \
    "Inventory evidence is stale or timestamped in the future"
  expected_kind="$1"
  expected_token="$2"
  [[ "$expected_kind" != finalize ]] || verification_hash="$(
    jq -r '.verification_evidence_sha256 // empty' "$INVENTORY_EVIDENCE_FILE"
  )"
  expected_derived_token="$(confirmation_token_for \
    "$expected_kind" "$created_at" \
    "$(jq -r '.nonce' "$INVENTORY_EVIDENCE_FILE")" \
    "$(jq -r '.state_digest // empty' "$INVENTORY_EVIDENCE_FILE")" \
    "$(jq -r '.verification_image_digest // empty' "$INVENTORY_EVIDENCE_FILE")" \
    "$(jq -r '.verification_evidence_sha256 // empty' "$INVENTORY_EVIDENCE_FILE")" \
    "$(jq -r '.verification_validation_digest // empty' "$INVENTORY_EVIDENCE_FILE")" \
    "$EXPECTED_ENVIRONMENT_DIGEST" \
    "$(jq -r '.verification_mandate_checksum // empty' "$INVENTORY_EVIDENCE_FILE")")"
  [[ "$expected_token" == "$expected_derived_token" ]] || die \
    "Inventory evidence token is not bound to its timestamp, nonce, and inventory"
  jq -e \
    --arg kind "$expected_kind" \
    --arg token "$expected_token" \
    --arg digest "$SOURCE_INVENTORY_DIGEST" \
    --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
    --arg inventory_hash "$(sha256_text "$(token_inventory_text)")" \
    --arg v2 "$V2_INDEX" \
    --arg verification_hash "$verification_hash" \
    --arg state_digest "$(jq -r '.producer_record.state_digest // empty' "$VERIFICATION_FILE" 2>/dev/null || true)" \
    --arg image_digest "$(jq -r '.wrapper.image_digest // empty' "$VERIFICATION_FILE" 2>/dev/null || true)" \
    --arg validation_digest "$(jq -r '.producer_record.validation_digest // empty' "$VERIFICATION_FILE" 2>/dev/null || true)" \
    --arg mandate_checksum "$(jq -r '.producer_record.mandate_checksum // empty' "$VERIFICATION_FILE" 2>/dev/null || true)" \
    --arg processing_version "$(jq -r '.wrapper.processing_version // empty' "$VERIFICATION_FILE" 2>/dev/null || true)" \
    --arg search_index "$(jq -r '.wrapper.search_index // empty' "$VERIFICATION_FILE" 2>/dev/null || true)" \
    '
      .kind == $kind and
      .confirmation_token == $token and
      .source_inventory_digest == $digest and
      .environment_digest == $environment_digest and
      .inventory_hash == $inventory_hash and
      .v2_index == $v2 and
      (if $kind == "finalize"
       then .state_digest == $state_digest
         and .verification_image_digest == $image_digest
         and .verification_evidence_sha256 == $verification_hash
         and .verification_processing_version == $processing_version
         and .verification_search_index == $search_index
         and .verification_validation_digest == $validation_digest
         and .verification_mandate_checksum == $mandate_checksum
       else .verification_evidence_sha256 == ""
       end)
    ' "$INVENTORY_EVIDENCE_FILE" >/dev/null || die \
    "Inventory evidence does not match the current environment or token"
}

print_inventory() {
  local token created_at nonce verification_state_digest="" verification_image_digest=""
  local verification_validation_digest="" verification_mandate_checksum="" verification_hash=""
  echo "==> Directive derived-data $MODE inventory"
  inventory_text
  echo "directive-source is PROTECTED and is never a reset target"
  created_at="$(date +%s)"
  nonce="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  [[ "$nonce" =~ ^[0-9a-f]{32}$ ]] || die "CSPRNG nonce is invalid"
  if [[ "$MODE" == "finalize" ]]; then
    start_fresh_verify
    verification_file_is_fresh
    verification_state_digest="$(jq -r '.producer_record.state_digest' "$VERIFICATION_FILE")"
    verification_image_digest="$(jq -r '.wrapper.image_digest' "$VERIFICATION_FILE")"
    verification_validation_digest="$(jq -r '.producer_record.validation_digest' "$VERIFICATION_FILE")"
    verification_mandate_checksum="$(jq -r '.producer_record.mandate_checksum' "$VERIFICATION_FILE")"
    verification_hash="$(sha256_file "$VERIFICATION_FILE")"
  fi
  token="$(confirmation_token_for \
    "$([[ "$MODE" == finalize ]] && echo finalize || echo reset)" \
    "$created_at" "$nonce" "$verification_state_digest" \
    "$verification_image_digest" "$verification_hash" \
    "$verification_validation_digest" "$EXPECTED_ENVIRONMENT_DIGEST" \
    "$verification_mandate_checksum")"
  write_inventory_evidence \
    "$token" \
    "$([[ "$MODE" == finalize ]] && echo finalize || echo reset)" \
    "$created_at" \
    "$nonce"
  echo "confirmation_token=$token"
  echo "No Azure data was changed."
}

container_exists() {
  local error_file status
  error_file="$(mktemp)"
  if "${AZ_CMD[@]}" cosmosdb sql container show \
    --account-name "$COSMOS_ACCOUNT" \
    --resource-group "$RG" \
    --database-name "$COSMOS_DATABASE" \
    --name "$1" \
    --output none >/dev/null 2>"$error_file"; then
    rm -f "$error_file"
    return 0
  else
    status="$?"
  fi
  if grep -Eiq '(^|[^0-9])404([^0-9]|$)|not.?found|resourcenotfound' "$error_file"; then
    rm -f "$error_file"
    return 1
  fi
  cat "$error_file" >&2
  rm -f "$error_file"
  die "Cosmos container existence check failed for $1 (status $status)"
}

assert_container_partition() {
  local container="$1"
  local expected_path="$2"
  local actual_path
  actual_path="$(
    "${AZ_CMD[@]}" cosmosdb sql container show \
      --account-name "$COSMOS_ACCOUNT" \
      --resource-group "$RG" \
      --database-name "$COSMOS_DATABASE" \
      --name "$container" \
      --query "resource.partitionKey.paths[0]" \
      --output tsv
  )"
  [[ "$actual_path" == "$expected_path" ]] || die \
    "Cosmos container $container has partition key $actual_path; expected $expected_path"
}

expected_partition_for() {
  case "$1" in
    "$CATALOG_CONTAINER") printf '/directive_id\n' ;;
    "$CONTENT_CONTAINER") printf '/directive_version_id\n' ;;
    "$MANDATES_CONTAINER") printf '/user_id\n' ;;
    *) die "Unexpected Cosmos container target: $1" ;;
  esac
}

validate_cosmos_container_names() {
  [[ "$CATALOG_CONTAINER" != "$CONTENT_CONTAINER" &&
    "$CATALOG_CONTAINER" != "$MANDATES_CONTAINER" &&
    "$CONTENT_CONTAINER" != "$MANDATES_CONTAINER" ]] || die \
    "Cosmos derived container names must be three distinct exact targets"
}

assert_no_active_execution() {
  refresh_active_executions || {
    echo "ERROR: unable to query Container Apps executions" >&2
    return 1
  }
  [[ -z "$ACTIVE_EXECUTIONS" ]] || die \
    "Container Apps job $JOB_NAME has active execution(s): $ACTIVE_EXECUTIONS"
}

refresh_active_executions() {
  if ! ACTIVE_EXECUTIONS="$(
    "${AZ_CMD[@]}" containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "[?properties.status!='Succeeded' && properties.status!='Failed' && properties.status!='Stopped' && properties.status!='Degraded' && properties.status!='Canceled'].name" \
      --output tsv
  )"; then
    return 1
  fi
  ACTIVE_EXECUTIONS="${ACTIVE_EXECUTIONS//$'\n'/,}"
}

enter_maintenance_mode() {
  echo "==> Putting directive ingestion job into nonpublishing maintenance mode"
  MAINTENANCE_TOUCHED=true
  "${AZ_CMD[@]}" containerapp job update \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --container-name directive-ingestion \
    --command directive-ingest \
    --args maintenance \
    --output none
}

wait_for_active_executions_to_drain() {
  local attempt
  for ((attempt = 1; attempt <= MAINTENANCE_DRAIN_ATTEMPTS; attempt++)); do
    if refresh_active_executions && [[ -z "$ACTIVE_EXECUTIONS" ]]; then
      return 0
    fi
    echo "==> Waiting for active execution(s) to drain: $ACTIVE_EXECUTIONS"
    sleep "$MAINTENANCE_DRAIN_DELAY_SECONDS"
  done
  echo "Active execution(s) did not drain or could not be queried: ${ACTIVE_EXECUTIONS:-<query-failed>}" >&2
  return 1
}

guard_artifact_target() {
  local prefix="$1"
  [[ "$ARTIFACT_CONTAINER" != "$SOURCE_CONTAINER" ]] || die \
    "Refusing to operate because artifact and protected source containers match"
  [[ "$prefix" != /* && "$prefix" != *..* ]] || die \
    "Unsafe artifact prefix: $prefix"
  [[ "$prefix" != "$SOURCE_CONTAINER" && "$prefix" != "$SOURCE_CONTAINER/"* ]] || die \
    "Refusing to operate on a prefix equal to or under directive-source"
}

purge_prefix() {
  local prefix="$1"
  local blob_name prefix_length remaining
  guard_artifact_target "$prefix"
  echo "==> Purging $ARTIFACT_CONTAINER/$prefix"
  prefix_length=${#prefix}
  while IFS= read -r blob_name; do
    [[ -n "$blob_name" ]] || continue
    [[ "${blob_name:0:prefix_length}" == "$prefix" ]] || die \
      "Storage listing returned a blob outside the literal purge prefix"
    "${AZ_CMD[@]}" storage blob delete \
      --account-name "$STORAGE_ACCOUNT" \
      --container-name "$ARTIFACT_CONTAINER" \
      --name "$blob_name" \
      --auth-mode login \
      --delete-snapshots include \
      --output none
  done < <(
    "${AZ_CMD[@]}" storage blob list \
      --account-name "$STORAGE_ACCOUNT" \
      --container-name "$ARTIFACT_CONTAINER" \
      --prefix "$prefix" \
      --auth-mode login \
      --query "[].name" \
      --output tsv
  )
  remaining="$(
    "${AZ_CMD[@]}" storage blob list \
      --account-name "$STORAGE_ACCOUNT" \
      --container-name "$ARTIFACT_CONTAINER" \
      --prefix "$prefix" \
      --num-results 1 \
      --auth-mode login \
      --query "[].name" \
      --output tsv
  )"
  [[ -z "$remaining" ]] || die \
    "Partial cleanup: artifacts remain under $ARTIFACT_CONTAINER/$prefix"
}

recreate_cosmos_containers() {
  COSMOS_PLAN_FILE="$(mktemp)"
  COSMOS_PLAN_JSON_FILE="$(mktemp)"
  echo "==> Creating and inspecting a targeted Terraform recreation plan"
  "${TERRAFORM_CMD[@]}" -chdir="$INFRA_DIR" plan \
    -input=false \
    -out="$COSMOS_PLAN_FILE" \
    -target=azurerm_cosmosdb_sql_container.directive_catalog \
    -target=azurerm_cosmosdb_sql_container.directive_content \
    -target=azurerm_cosmosdb_sql_container.directive_mandates
  "${TERRAFORM_CMD[@]}" -chdir="$INFRA_DIR" show \
    -json "$COSMOS_PLAN_FILE" >"$COSMOS_PLAN_JSON_FILE"
  directive_assert_cosmos_recreation_plan "$COSMOS_PLAN_JSON_FILE" || die \
    "Terraform plan is not an exact create-only plan for the three Cosmos containers"
  echo "==> Applying the inspected saved Terraform plan"
  "${TERRAFORM_CMD[@]}" -chdir="$INFRA_DIR" apply \
    -input=false \
    "$COSMOS_PLAN_FILE"
}

reset_derived_data() {
  local container
  [[ "$CONFIRMATION_TOKEN" == DIRECTIVE-RESET-V2-* ]] || die \
    "Reset requires a DIRECTIVE-RESET-V2 token"
  enter_maintenance_mode
  wait_for_active_executions_to_drain
  load_inventory
  validate_cosmos_container_names
  validate_inventory_evidence reset "$CONFIRMATION_TOKEN"
  assert_no_active_execution

  for container in "$CATALOG_CONTAINER" "$CONTENT_CONTAINER" "$MANDATES_CONTAINER"; do
    if ! container_exists "$container"; then
      echo "==> Cosmos container $container is already absent; recreation will restore it"
      continue
    fi
    assert_container_partition "$container" "$(expected_partition_for "$container")"
  done

  for container in "$CATALOG_CONTAINER" "$CONTENT_CONTAINER" "$MANDATES_CONTAINER"; do
    container_exists "$container" || continue
    echo "==> Deleting Cosmos container $COSMOS_DATABASE/$container"
    "${AZ_CMD[@]}" cosmosdb sql container delete \
      --account-name "$COSMOS_ACCOUNT" \
      --resource-group "$RG" \
      --database-name "$COSMOS_DATABASE" \
      --name "$container" \
      --yes \
      --output none
    container_exists "$container" && die \
      "Partial cleanup: Cosmos container still exists after delete: $container"
  done

  recreate_cosmos_containers

  assert_container_partition "$CATALOG_CONTAINER" "$(expected_partition_for "$CATALOG_CONTAINER")"
  assert_container_partition "$CONTENT_CONTAINER" "$(expected_partition_for "$CONTENT_CONTAINER")"
  assert_container_partition "$MANDATES_CONTAINER" "$(expected_partition_for "$MANDATES_CONTAINER")"

  purge_prefix "directives/"
  purge_prefix "source-state/"
  purge_prefix "quarantine/"
  purge_prefix "publication-approval/"
  purge_prefix "publication-approval-provenance/"
  purge_prefix "publication-commit/"
  purge_prefix "publication-lock/"
  purge_prefix "publication-claims/"
  delete_v2_index
  rm -f "$INVENTORY_EVIDENCE_FILE"
  echo "==> Reset complete; v1 Search remains and v2 bootstrap is owned by the ingestion deployment"
}

verification_file_is_fresh() {
  local calculated_digest record_without_digest validated_record
  [[ -f "$VERIFICATION_FILE" ]] || die \
    "A safe output file from the successful v2 verify execution is required"
  [[ -s "$VERIFICATION_FILE" ]] || die "Verification evidence file is empty"
  [[ "$(wc -c <"$VERIFICATION_FILE")" -le 65536 ]] || die \
    "Verification evidence file is unexpectedly large"
  validated_record="$(mktemp)"
  jq -c '.producer_record' "$VERIFICATION_FILE" >"$validated_record"
  directive_validate_producer_record \
    "$validated_record" "$validated_record.normalized" \
    directive.verify.v2 "$EXPECTED_ENVIRONMENT_FILE" \
    "$SOURCE_INVENTORY_DIGEST" directive-v2-czech-layout "$V2_INDEX" || {
    rm -f "$validated_record" "$validated_record.normalized"
    die "Verification evidence is not one complete successful pinned v2 verify record"
  }
  rm -f "$validated_record" "$validated_record.normalized"
  jq -e \
    --arg subscription "$SUBSCRIPTION_ID" \
    --arg resource_group "$RG" \
    --arg job "$JOB_NAME" \
    --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
    --arg search_index "$V2_INDEX" \
    --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
    '
      .wrapper.subscription_id == $subscription and
      .wrapper.resource_group == $resource_group and
      .wrapper.job_name == $job and
      .wrapper.environment_digest == $environment_digest and
      .wrapper.search_index == $search_index and
      .wrapper.processing_version == "directive-v2-czech-layout" and
      (.wrapper.image_digest | test("^sha256:[0-9a-f]{64}$")) and
      .wrapper.source_inventory_digest == $source_digest and
      (.wrapper.verification_execution_id | type == "string" and length > 0) and
      (.producer_record.validation_digest | test("^[0-9a-f]{64}$")) and
      (.wrapper.verification_record_digest | test("^[0-9a-f]{64}$"))
    ' "$VERIFICATION_FILE" >/dev/null || die \
      "Verification wrapper is not bound to the current Azure environment"
  record_without_digest="$(jq -S -c 'del(.wrapper.verification_record_digest, .verification_record_digest)' "$VERIFICATION_FILE")"
  calculated_digest="$(sha256_text "$record_without_digest")"
  [[ "$calculated_digest" == "$(jq -r '.wrapper.verification_record_digest // .verification_record_digest' "$VERIFICATION_FILE")" ]] || die \
    "Verification evidence digest does not match its complete record"
}

search_index_exists() {
  local error_file status
  error_file="$(mktemp)"
  if "${AZ_CMD[@]}" rest \
    --method get \
    --url "https://${SEARCH_NAME}.search.windows.net/indexes/$1?api-version=2026-04-01" \
    --resource "https://search.azure.com" \
    --output none >/dev/null 2>"$error_file"; then
    rm -f "$error_file"
    return 0
  else
    status="$?"
  fi
  if grep -Eiq '(^|[^0-9])404([^0-9]|$)|not.?found|resourcenotfound' "$error_file"; then
    rm -f "$error_file"
    return 1
  fi
  cat "$error_file" >&2
  rm -f "$error_file"
  die "Search index existence check failed for $1 (status $status)"
}

delete_v2_index() {
  [[ "$V2_INDEX" == directive-chunks-v2 ]] || die \
    "Refusing to delete a non-v2 Search index"
  if search_index_exists "$V2_INDEX"; then
    echo "==> Deleting derived Search v2 index $V2_INDEX"
    "${AZ_CMD[@]}" rest \
      --method delete \
      --url "https://${SEARCH_NAME}.search.windows.net/indexes/$V2_INDEX?api-version=2026-04-01" \
      --resource "https://search.azure.com" \
      --output none
    search_index_exists "$V2_INDEX" && die \
      "Partial cleanup: Search v2 index still exists after delete"
  fi
}

assert_verify_execution_contract() {
  local execution_name="$1"
  local expected_image="$2"
  local container_json actual_image
  container_json="$(
    "${AZ_CMD[@]}" containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'] | [0]" \
      --output json
  )"
  actual_image="$(
    "${AZ_CMD[@]}" containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].image | [0]" \
      --output tsv
  )"
  directive_assert_approved_execution_json \
    "$container_json" verify "$expected_image" \
    "$APPROVED_ENVIRONMENT_DIGEST" "$APPROVED_SOURCE_INVENTORY_DIGEST" \
    "$APPROVED_VALIDATION_DIGEST" "$PROCESSING_VERSION" "$V2_INDEX" || \
    die "Fresh execution did not use the exact approved directive-ingest verify contract"
  [[ "$actual_image" == "$expected_image" ]] || \
    die "Fresh verification execution image changed from the pinned live image"
}

prepare_fresh_verify_env_vars() {
  local env_json env_file name value
  env_json="$(
    "${AZ_CMD[@]}" containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].env | [0]" \
      --output json
  )"
  env_file="$(mktemp)"
  if ! directive_render_execution_env_vars \
    "$env_json" verify "$PROCESSING_VERSION" "$V2_INDEX" \
    "$APPROVED_VALIDATION_DIGEST" "$APPROVED_ENVIRONMENT_DIGEST" \
    "$APPROVED_SOURCE_INVENTORY_DIGEST" >"$env_file"; then
    rm -f "$env_file"
    die "Live job environment cannot be used as a complete v2 verify override"
  fi
  FRESH_VERIFY_ENV_VARS=()
  while IFS=$'\t' read -r name value; do
    [[ -n "$name" ]] || continue
    FRESH_VERIFY_ENV_VARS+=("$name=$value")
  done <"$env_file"
  rm -f "$env_file"
  [[ "${#FRESH_VERIFY_ENV_VARS[@]}" -gt 0 ]] || die \
    "Complete v2 verify override has no environment values"
}

set_verify_approval_overrides() {
  [[ -f "$VERIFICATION_FILE" ]] || die \
    "Fresh verify requires the prior verification evidence file for approval binding"
  APPROVED_VALIDATION_DIGEST="$(jq -r '.producer_record.validation_digest // empty' "$VERIFICATION_FILE")"
  APPROVED_ENVIRONMENT_DIGEST="$(jq -r '.producer_record.environment_digest // empty' "$VERIFICATION_FILE")"
  APPROVED_SOURCE_INVENTORY_DIGEST="$(jq -r '.producer_record.source_inventory_digest // empty' "$VERIFICATION_FILE")"
  [[ "$APPROVED_VALIDATION_DIGEST" =~ ^[0-9a-f]{64}$ ]] || die \
    "Fresh verify approval validation_digest is invalid"
  [[ "$APPROVED_ENVIRONMENT_DIGEST" == "$EXPECTED_ENVIRONMENT_DIGEST" ]] || die \
    "Fresh verify approval environment_digest does not match live environment"
  [[ "$APPROVED_SOURCE_INVENTORY_DIGEST" == "$SOURCE_INVENTORY_DIGEST" ]] || die \
    "Fresh verify approval source inventory digest is stale"
}

recover_fresh_verify_execution() {
  local execution_snapshot="$1"
  local live_image="$2"
  local current_executions candidates_file recovery_status=0 candidate
  RECOVERED_EXECUTION_NAME=""
  current_executions="$(
    "${AZ_CMD[@]}" containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --output json
  )"
  candidates_file="$(mktemp)"
  if directive_select_new_approved_execution_names \
      "$execution_snapshot" "$current_executions" verify "$live_image" \
      "$APPROVED_ENVIRONMENT_DIGEST" "$APPROVED_SOURCE_INVENTORY_DIGEST" \
      "$APPROVED_VALIDATION_DIGEST" directive-v2-czech-layout "$V2_INDEX" \
      >"$candidates_file"; then
    recovery_status=0
  else
    recovery_status=$?
  fi
  load_recovery_candidates "$candidates_file"
  rm -f "$candidates_file"
  if [[ "${#RECOVERY_CANDIDATES[@]}" -gt 0 ]]; then
    for candidate in "${RECOVERY_CANDIDATES[@]}"; do
      [[ -n "$candidate" ]] || continue
      STARTED_EXECUTIONS+=("$candidate")
    done
  fi
  [[ "$recovery_status" -eq 0 && "${#RECOVERY_CANDIDATES[@]}" -eq 1 ]] || return 1
  RECOVERED_EXECUTION_NAME="${RECOVERY_CANDIDATES[0]}"
}

start_fresh_verify() {
  local execution_name status log_file live_image execution_image record_digest started_at
  local execution_name_file execution_snapshot job_start_args=()
  set_verify_approval_overrides
  prepare_fresh_verify_env_vars
  enter_maintenance_mode
  wait_for_active_executions_to_drain
  live_image="$(
    "${AZ_CMD[@]}" containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "properties.template.containers[?name=='directive-ingestion'].image | [0]" \
      --output tsv
  )"
  [[ "$live_image" == *@sha256:* ]] || die "Fresh verify requires an immutable live job image"
  execution_snapshot="$(
    "${AZ_CMD[@]}" containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --output json
  )"
  execution_name_file="$(mktemp)"
  local dispatch_status=0
  directive_build_job_start_override_args \
    verify "$JOB_NAME" "$RG" "$JOB_CONTAINER" "$live_image" \
    "$JOB_CPU" "$JOB_MEMORY" "${FRESH_VERIFY_ENV_VARS[@]}" || \
    die "Complete fresh verify override is malformed"
  job_start_args=("${DIRECTIVE_JOB_START_ARGS[@]}")
  if "${AZ_CMD[@]}" containerapp job start "${job_start_args[@]}" \
    >"$execution_name_file"; then
    dispatch_status=0
  else
    dispatch_status=$?
  fi
  execution_name="$(<"$execution_name_file")"
  rm -f "$execution_name_file"
  if [[ "$dispatch_status" -ne 0 || -z "$execution_name" ]]; then
    recover_fresh_verify_execution "$execution_snapshot" "$live_image" || die \
      "Fresh verify dispatch response was ambiguous or unrecoverable"
    execution_name="$RECOVERED_EXECUTION_NAME"
  fi
  [[ -n "$execution_name" ]] || die "Fresh verify did not return an execution name"
  local execution_already_tracked=false
  if [[ "${#STARTED_EXECUTIONS[@]}" -gt 0 ]]; then
    case " ${STARTED_EXECUTIONS[*]} " in
      *" $execution_name "*) execution_already_tracked=true ;;
    esac
  fi
  if [[ "$execution_already_tracked" == false ]]; then
    STARTED_EXECUTIONS+=("$execution_name")
  fi
  started_at="$(
    "${AZ_CMD[@]}" containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --query properties.startTime \
      --output tsv
  )"
  [[ -n "$started_at" ]] || die "Fresh verify execution has no start timestamp"
  status=""
  for ((attempt = 1; attempt <= MAINTENANCE_DRAIN_ATTEMPTS; attempt++)); do
    status="$(
      "${AZ_CMD[@]}" containerapp job execution show \
        --name "$JOB_NAME" \
        --resource-group "$RG" \
        --job-execution-name "$execution_name" \
        --query properties.status \
        --output tsv
    )"
    case "$status" in
      Succeeded) break ;;
      Failed|Stopped|Canceled|Degraded) die "Fresh v2 verify execution $execution_name failed: $status" ;;
      *) sleep "$MAINTENANCE_DRAIN_DELAY_SECONDS" ;;
    esac
  done
  [[ "$status" == Succeeded ]] || die "Fresh v2 verify execution did not finish successfully"
  log_file="$(mktemp)"
  "${AZ_CMD[@]}" containerapp job logs show \
    --name "$JOB_NAME" \
    --resource-group "$RG" \
    --container "$JOB_CONTAINER" \
    --execution "$execution_name" \
    --tail 300 \
    --format text >"$log_file"
  directive_extract_producer_record "$log_file" "$log_file.record" || {
    rm -f "$log_file"
    die "Fresh v2 verify did not emit exactly one complete producer record"
  }
  directive_validate_producer_record \
    "$log_file.record" "$log_file.validated" \
    directive.verify.v2 "$EXPECTED_ENVIRONMENT_FILE" \
    "$SOURCE_INVENTORY_DIGEST" "$PROCESSING_VERSION" "$V2_INDEX" || {
    rm -f "$log_file" "$log_file.record" "$log_file.validated"
    die "Fresh v2 verify producer record failed the exact schema and digest checks"
  }
  execution_image="$(
    "${AZ_CMD[@]}" containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].image | [0]" \
      --output tsv
  )"
  [[ "$execution_image" == "$live_image" && "$execution_image" == *@sha256:* ]] || \
    die "Fresh verify execution is not pinned to an immutable image"
  assert_verify_execution_contract "$execution_name" "$live_image"
  jq -S -c \
    --arg image "$execution_image" \
    --arg execution "$execution_name" \
    --arg started_at "$started_at" \
    --arg subscription "$SUBSCRIPTION_ID" \
    --arg resource_group "$RG" \
    --arg job "$JOB_NAME" \
    --arg environment_digest "$EXPECTED_ENVIRONMENT_DIGEST" \
    --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
    --arg processing_version "$PROCESSING_VERSION" \
    --arg search_index "$V2_INDEX" \
    '{producer_record: .,
      wrapper: {
        subscription_id: $subscription,
        resource_group: $resource_group,
        job_name: $job,
        image_digest: ($image | split("@")[1]),
        image_reference: $image,
        environment_digest: $environment_digest,
        verification_execution_id: $execution,
        verification_started_at: $started_at,
        source_inventory_digest: $source_digest,
        processing_version: $processing_version,
        search_index: $search_index
      }}' \
    "$log_file.validated" >"$VERIFICATION_FILE"
  record_digest="$(sha256_text "$(jq -S -c 'del(.wrapper.verification_record_digest)' "$VERIFICATION_FILE")")"
  jq --arg digest "$record_digest" '.wrapper.verification_record_digest = $digest' \
    "$VERIFICATION_FILE" >"$log_file.evidence"
  mv "$log_file.evidence" "$VERIFICATION_FILE"
  rm -f "$log_file.record"
  rm -f "$log_file.validated"
  rm -f "$log_file"
}

finalize_v1() {
  local expected_state_digest expected_image_digest expected_processing_version expected_search_index
  local expected_validation_digest expected_mandate_checksum
  expected_state_digest="$(jq -r '.state_digest' "$INVENTORY_EVIDENCE_FILE")"
  expected_image_digest="$(jq -r '.verification_image_digest' "$INVENTORY_EVIDENCE_FILE")"
  expected_processing_version="$(jq -r '.verification_processing_version' "$INVENTORY_EVIDENCE_FILE")"
  expected_search_index="$(jq -r '.verification_search_index' "$INVENTORY_EVIDENCE_FILE")"
  expected_validation_digest="$(jq -r '.verification_validation_digest' "$INVENTORY_EVIDENCE_FILE")"
  expected_mandate_checksum="$(jq -r '.verification_mandate_checksum' "$INVENTORY_EVIDENCE_FILE")"
  validate_index_names
  load_inventory
  enter_maintenance_mode
  wait_for_active_executions_to_drain
  start_fresh_verify
  verification_file_is_fresh
  load_inventory
  validate_inventory_evidence finalize "$CONFIRMATION_TOKEN"
  local evidence_image evidence_source evidence_processing_hash evidence_state_digest evidence_validation_digest
  local evidence_mandate_checksum
  local verification_execution
  evidence_image="$(jq -r '.wrapper.image_digest // .image_digest' "$VERIFICATION_FILE")"
  evidence_source="$(jq -r '.producer_record.source_inventory_digest // .source_inventory_digest' "$VERIFICATION_FILE")"
  evidence_processing_hash="$(jq -r '.producer_record.processing_hash // .processing_hash' "$VERIFICATION_FILE")"
  verification_execution="$(jq -r '.wrapper.verification_execution_id' "$VERIFICATION_FILE")"
  evidence_state_digest="$(jq -r '.producer_record.state_digest' "$VERIFICATION_FILE")"
  evidence_validation_digest="$(jq -r '.producer_record.validation_digest' "$VERIFICATION_FILE")"
  evidence_mandate_checksum="$(jq -r '.producer_record.mandate_checksum' "$VERIFICATION_FILE")"
  [[ "$evidence_state_digest" == "$expected_state_digest" ]] || die \
    "Fresh verification state_digest differs from the finalize dry-run evidence"
  [[ "$evidence_image" == "$expected_image_digest" ]] || die \
    "Fresh verification image differs from the finalize dry-run evidence"
  [[ "$evidence_validation_digest" == "$expected_validation_digest" ]] || die \
    "Fresh verification validation_digest differs from the finalize dry-run evidence"
  [[ "$evidence_mandate_checksum" == "$expected_mandate_checksum" ]] || die \
    "Fresh verification mandate_checksum differs from the finalize dry-run evidence"
  [[ "$(jq -r '.wrapper.processing_version' "$VERIFICATION_FILE")" == "$expected_processing_version" &&
      "$(jq -r '.wrapper.search_index' "$VERIFICATION_FILE")" == "$expected_search_index" ]] || die \
    "Fresh verification configuration differs from the finalize dry-run evidence"
  [[ "$SOURCE_INVENTORY_DIGEST" == "$evidence_source" ]] || die \
    "Source inventory changed since v2 verification"
  [[ "$evidence_processing_hash" =~ ^[0-9a-f]{64}$ ]] || die \
    "Verification evidence processing hash is invalid"
  [[ "$(jq -r '.producer_record.processing_hash // .processing_hash' "$VERIFICATION_FILE")" == "$evidence_processing_hash" ]] || die \
    "Verification evidence processing hash does not match the verify record"
  search_index_exists "$V2_INDEX" || die \
    "v2 Search index does not exist; refuse to finalize"
  search_index_exists "$V1_INDEX" || die \
    "v1 Search index is already absent; refuse stale or repeated finalize"
  local live_image verification_status live_chunk_count expected_chunk_count
  live_image="$(
    "${AZ_CMD[@]}" containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "properties.template.containers[?name=='$JOB_CONTAINER'].image | [0]" \
      --output tsv
  )"
  [[ "$live_image" == *"@$evidence_image" ]] || die \
    "Live job image does not match the pinned verification image"
  verification_status="$(
    "${AZ_CMD[@]}" containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$verification_execution" \
      --query properties.status \
      --output tsv
  )"
  [[ "$verification_status" == Succeeded ]] || die \
    "Fresh verification execution is not currently successful"
  live_chunk_count="$(
    "${AZ_CMD[@]}" rest \
      --method get \
      --url "https://${SEARCH_NAME}.search.windows.net/indexes/$V2_INDEX/docs/\$count?api-version=2026-04-01" \
      --resource "https://search.azure.com" \
      --output tsv
  )"
  expected_chunk_count="$(jq -r '.producer_record.cross_store.search.document_count // empty' "$VERIFICATION_FILE")"
  [[ "$expected_chunk_count" =~ ^[0-9]+$ ]] || die \
    "Fresh verification is missing the exact Search cross-store count"
  [[ "$live_chunk_count" == "$expected_chunk_count" ]] || die \
    "Live v2 Search count does not match the pinned verification record"
  [[ "$CONFIRMATION_TOKEN" == DIRECTIVE-FINALIZE-V2-* ]] || die \
    "Finalize requires a DIRECTIVE-FINALIZE-V2 token"
  assert_no_active_execution

  echo "==> Deleting Search v1 only after v2 verification evidence passed"
  "${AZ_CMD[@]}" rest \
    --method delete \
    --url "https://${SEARCH_NAME}.search.windows.net/indexes/$V1_INDEX?api-version=2026-04-01" \
    --resource "https://search.azure.com" \
    --output none
  search_index_exists "$V1_INDEX" && die \
    "Search v1 still exists after delete"
  search_index_exists "$V2_INDEX" || die \
    "Verification mismatch: Search v2 disappeared during finalize"
  echo "==> Search cutover finalized; v2 retained and v1 deleted"
}

validate_index_names() {
  [[ -n "$V1_INDEX" && -n "$V2_INDEX" ]] || die \
    "Both Search index names are required"
  [[ "$V1_INDEX" != "$V2_INDEX" ]] || die \
    "Search v1 and v2 index names must be different"
  [[ "$V1_INDEX" != *[^a-z0-9-]* && "$V2_INDEX" != *[^a-z0-9-]* ]] || die \
    "Search index names must contain only lowercase letters, digits, and hyphens"
  [[ "$V1_INDEX" == directive-chunks-v1 ]] || die \
    "Finalize is only permitted for directive-chunks-v1"
  [[ "$V2_INDEX" == directive-chunks-v2 ]] || die \
    "Finalize is only permitted for directive-chunks-v2"
}

parse_args "$@"
load_inventory
validate_index_names
validate_cosmos_container_names

case "$MODE" in
  dry-run)
    print_inventory
    ;;
  reset)
    [[ "$EXECUTE_FLAG" == true && -n "$CONFIRMATION_TOKEN" ]] || die \
      "Reset requires explicit --execute, --inventory-evidence FILE, and --confirm TOKEN"
    validate_inventory_evidence reset "$CONFIRMATION_TOKEN"
    reset_derived_data
    ;;
  finalize)
    [[ -n "$VERIFICATION_FILE" ]] || die \
      "Finalize requires --verification-file from a successful v2 verify execution"
    if [[ "$EXECUTE_FLAG" != true || -z "$CONFIRMATION_TOKEN" ]]; then
      print_inventory
    else
      finalize_v1
    fi
    ;;
  *)
    die "unsupported mode: $MODE"
    ;;
esac
