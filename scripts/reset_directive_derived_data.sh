#!/usr/bin/env bash
# Guarded destructive reset and Search cutover for directive v2.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

AZ_BIN="${AZ_BIN:-az}"
TERRAFORM_BIN="${TERRAFORM_BIN:-terraform}"
MAX_VERIFICATION_AGE_SECONDS="${DIRECTIVE_VERIFICATION_MAX_AGE_SECONDS:-86400}"
MAX_INVENTORY_EVIDENCE_AGE_SECONDS="${DIRECTIVE_RESET_EVIDENCE_MAX_AGE_SECONDS:-1800}"
MAINTENANCE_DRAIN_ATTEMPTS="${DIRECTIVE_MAINTENANCE_DRAIN_ATTEMPTS:-60}"
MAINTENANCE_DRAIN_DELAY_SECONDS="${DIRECTIVE_MAINTENANCE_DRAIN_DELAY_SECONDS:-10}"
V1_INDEX="${DIRECTIVE_SEARCH_V1_INDEX:-directive-chunks-v1}"
V2_INDEX="directive-chunks-v2"
MODE="dry-run"
EXECUTE_FLAG=false
CONFIRMATION_TOKEN=""
VERIFICATION_FILE=""
INVENTORY_EVIDENCE_FILE=""
SOURCE_INVENTORY_FILE=""
SOURCE_INVENTORY_DIGEST=""
SOURCE_COUNT=0

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

cleanup() {
  rm -f "$SOURCE_INVENTORY_FILE"
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
  local blob_name relative source_hash extension
  SUBSCRIPTION_NAME="$("${AZ_CMD[@]}" account show --query name --output tsv)"
  RG="$(tf_output resource_group)"
  STORAGE_ACCOUNT="$(tf_output directive_artifacts_storage_account)"
  ARTIFACT_CONTAINER="$(tf_output directive_artifacts_container)"
  SOURCE_CONTAINER="$(tf_output directive_source_container)"
  SOURCE_PREFIX="$(tf_output directive_source_prefix)"
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
      relative="${blob_name#$SOURCE_PREFIX}"
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

inventory_token() {
  local inventory
  inventory="$(token_inventory_text)"
  printf 'DIRECTIVE-RESET-V2-%s\n' "$(sha256_text "$inventory" | cut -c1-24)"
}

finalize_token() {
  local inventory verification_hash validation_digest source_digest image_digest verify_digest
  verification_hash="$(sha256_file "$VERIFICATION_FILE")"
  validation_digest="$(jq -r '.validation_record_digest' "$VERIFICATION_FILE")"
  source_digest="$(jq -r '.source_inventory_digest' "$VERIFICATION_FILE")"
  image_digest="$(jq -r '.image_digest' "$VERIFICATION_FILE")"
  verify_digest="$(jq -r '.verification_record_digest' "$VERIFICATION_FILE")"
  inventory="$(token_inventory_text)"
  printf 'DIRECTIVE-FINALIZE-V2-%s\n' \
    "$(sha256_text "$inventory
verification_evidence_sha256=$verification_hash
validation_record_digest=$validation_digest
source_inventory_digest=$source_digest
image_digest=$image_digest
verification_record_digest=$verify_digest" | cut -c1-24)"
}

write_inventory_evidence() {
  local token="$1"
  local kind="$2"
  local created_at nonce inventory_hash verification_hash=""
  [[ -n "$INVENTORY_EVIDENCE_FILE" ]] || return 0
  created_at="$(date +%s)"
  nonce="$(
    sha256_text "$created_at:$RANDOM:$$:$SOURCE_INVENTORY_DIGEST" | cut -c1-32
  )"
  inventory_hash="$(sha256_text "$(token_inventory_text)")"
  if [[ "$kind" == finalize ]]; then
    verification_hash="$(sha256_file "$VERIFICATION_FILE")"
  fi
  jq -S -n \
    --arg kind "$kind" \
    --argjson created_at "$created_at" \
    --arg nonce "$nonce" \
    --arg token "$token" \
    --arg inventory_hash "$inventory_hash" \
    --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
    --arg v2 "$V2_INDEX" \
    --arg verification_hash "$verification_hash" \
    '{
      kind: $kind,
      created_at: $created_at,
      nonce: $nonce,
      confirmation_token: $token,
      inventory_hash: $inventory_hash,
      source_inventory_digest: $source_digest,
      v2_index: $v2,
      verification_evidence_sha256: $verification_hash
    }' >"$INVENTORY_EVIDENCE_FILE"
}

validate_inventory_evidence() {
  local now created_at age expected_kind expected_token verification_hash=""
  [[ -s "$INVENTORY_EVIDENCE_FILE" ]] || die \
    "Execute requires a persisted fresh dry-run inventory evidence file"
  jq -e 'type == "object" and (.nonce | type == "string" and length > 0)' \
    "$INVENTORY_EVIDENCE_FILE" >/dev/null || die \
    "Inventory evidence is malformed"
  created_at="$(jq -r '.created_at // 0' "$INVENTORY_EVIDENCE_FILE")"
  now="$(date +%s)"
  age=$((now - created_at))
  [[ "$age" -ge 0 && "$age" -le "$MAX_INVENTORY_EVIDENCE_AGE_SECONDS" ]] || die \
    "Inventory evidence is stale or timestamped in the future"
  expected_kind="$1"
  expected_token="$2"
  [[ "$expected_kind" != finalize ]] || verification_hash="$(sha256_file "$VERIFICATION_FILE")"
  jq -e \
    --arg kind "$expected_kind" \
    --arg token "$expected_token" \
    --arg digest "$SOURCE_INVENTORY_DIGEST" \
    --arg inventory_hash "$(sha256_text "$(token_inventory_text)")" \
    --arg v2 "$V2_INDEX" \
    --arg verification_hash "$verification_hash" \
    '
      .kind == $kind and
      .confirmation_token == $token and
      .source_inventory_digest == $digest and
      .inventory_hash == $inventory_hash and
      .v2_index == $v2 and
      (if $kind == "finalize"
       then .verification_evidence_sha256 == $verification_hash
       else .verification_evidence_sha256 == ""
       end)
    ' "$INVENTORY_EVIDENCE_FILE" >/dev/null || die \
    "Inventory evidence does not match the current environment or token"
}

print_inventory() {
  local token
  echo "==> Directive derived-data $MODE inventory"
  inventory_text
  echo "directive-source is PROTECTED and is never a reset target"
  if [[ "$MODE" == "finalize" ]]; then
    verification_file_is_fresh
    token="$(finalize_token)"
  else
    token="$(inventory_token)"
  fi
  write_inventory_evidence "$token" "$([[ "$MODE" == finalize ]] && echo finalize || echo reset)"
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

assert_no_active_execution() {
  refresh_active_executions
  [[ -z "$ACTIVE_EXECUTIONS" ]] || die \
    "Container Apps job $JOB_NAME has active execution(s): $ACTIVE_EXECUTIONS"
}

refresh_active_executions() {
  ACTIVE_EXECUTIONS="$(
    "${AZ_CMD[@]}" containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "[?properties.status!='Succeeded' && properties.status!='Failed' && properties.status!='Stopped' && properties.status!='Degraded' && properties.status!='Canceled'].name" \
      --output tsv
  )"
  ACTIVE_EXECUTIONS="${ACTIVE_EXECUTIONS//$'\n'/,}"
}

enter_maintenance_mode() {
  echo "==> Putting directive ingestion job into nonpublishing maintenance mode"
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
    refresh_active_executions
    if [[ -z "$ACTIVE_EXECUTIONS" ]]; then
      return 0
    fi
    echo "==> Waiting for active execution(s) to drain: $ACTIVE_EXECUTIONS"
    sleep "$MAINTENANCE_DRAIN_DELAY_SECONDS"
  done
  die "Active execution(s) did not drain in maintenance mode: $ACTIVE_EXECUTIONS"
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
  local remaining
  guard_artifact_target "$prefix"
  echo "==> Purging $ARTIFACT_CONTAINER/$prefix"
  "${AZ_CMD[@]}" storage blob delete-batch \
    --account-name "$STORAGE_ACCOUNT" \
    --source "$ARTIFACT_CONTAINER" \
    --pattern "${prefix}*" \
    --auth-mode login \
    --delete-snapshots include \
    --output none
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

reset_derived_data() {
  local container
  [[ "$CONFIRMATION_TOKEN" == "$(inventory_token)" ]] || die \
    "Confirmation token is wrong or stale; run a fresh dry-run"
  [[ "$CONFIRMATION_TOKEN" == DIRECTIVE-RESET-V2-* ]] || die \
    "Reset requires a DIRECTIVE-RESET-V2 token"
  enter_maintenance_mode
  wait_for_active_executions_to_drain
  load_inventory
  [[ "$CONFIRMATION_TOKEN" == "$(inventory_token)" ]] || die \
    "Source inventory changed while entering maintenance; run a fresh dry-run"
  assert_no_active_execution

  for container in "$CATALOG_CONTAINER" "$CONTENT_CONTAINER" "$MANDATES_CONTAINER"; do
    container_exists "$container" || die \
      "Expected Cosmos container is missing before reset: $container"
  done
  assert_container_partition "$CATALOG_CONTAINER" "/directive_id"
  assert_container_partition "$CONTENT_CONTAINER" "/directive_version_id"
  assert_container_partition "$MANDATES_CONTAINER" "/user_id"

  for container in "$CATALOG_CONTAINER" "$CONTENT_CONTAINER" "$MANDATES_CONTAINER"; do
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

  echo "==> Recreating the same named Cosmos containers with Terraform"
  "${TERRAFORM_CMD[@]}" -chdir="$INFRA_DIR" apply \
    -input=false \
    -auto-approve \
    -target=azurerm_cosmosdb_sql_container.directive_catalog \
    -target=azurerm_cosmosdb_sql_container.directive_content \
    -target=azurerm_cosmosdb_sql_container.directive_mandates

  assert_container_partition "$CATALOG_CONTAINER" "/directive_id"
  assert_container_partition "$CONTENT_CONTAINER" "/directive_version_id"
  assert_container_partition "$MANDATES_CONTAINER" "/user_id"

  purge_prefix "directives/"
  purge_prefix "source-state/"
  purge_prefix "quarantine/"
  delete_v2_index
  echo "==> Reset complete; v1 Search remains and v2 bootstrap is owned by the ingestion deployment"
}

verification_file_is_fresh() {
  local mtime now age calculated_digest record_without_digest
  [[ -f "$VERIFICATION_FILE" ]] || die \
    "A safe output file from the successful v2 verify execution is required"
  [[ -s "$VERIFICATION_FILE" ]] || die "Verification evidence file is empty"
  [[ "$(wc -c <"$VERIFICATION_FILE")" -le 65536 ]] || die \
    "Verification evidence file is unexpectedly large"
  if grep -Eiq '"(content|markdown|prompt|document_text|administrative_content|raw_response)"[[:space:]]*:' "$VERIFICATION_FILE"; then
    die "Verification evidence contains a raw content field; use sanitized verify output"
  fi
  jq -s -e \
    --arg subscription "$SUBSCRIPTION_ID" \
    --arg resource_group "$RG" \
    --arg job "$JOB_NAME" \
    --arg search_index "directive-chunks-v2" \
    '
      length == 1 and
      (.[0] | type == "object") and
      .[0].verify_record.success == true and
      .[0].environment.subscription_id == $subscription and
      .[0].environment.resource_group == $resource_group and
      .[0].environment.job_name == $job and
      .[0].search_index == $search_index and
      .[0].processing_version == "directive-v2-czech-layout" and
      (.[0].processing_hash | test("^[0-9a-f]{64}$")) and
      (.[0].image_digest | test("^sha256:[0-9a-f]{64}$")) and
      (.[0].source_inventory_digest | test("^[0-9a-f]{64}$")) and
      (.[0].validation_execution_id | type == "string" and length > 0) and
      (.[0].validation_record_digest | test("^[0-9a-f]{64}$")) and
      (.[0].verification_execution_id | type == "string" and length > 0) and
      (.[0].verification_record_digest | test("^[0-9a-f]{64}$")) and
      (.[0].verify_record.verify_execution_id | type == "string" and length > 0) and
      (.[0].verify_record.source_versions | type == "number") and
      (.[0].verify_record.directive_ids | type == "number") and
      (.[0].verify_record.current_versions | type == "number") and
      (.[0].verify_record.accepted_relations | type == "number") and
      (.[0].verify_record.required_artifacts | type == "number") and
      (.[0].verify_record.content_sections | type == "number") and
      (.[0].verify_record.content_parts | type == "number") and
      (.[0].verify_record.split_sections | type == "number") and
      (.[0].verify_record.published_chunks | type == "number") and
      (.[0].verify_record.published_directives | type == "number") and
      (.[0].verify_record.published_versions | type == "number") and
      (.[0].verify_record.current_directives | type == "number") and
      (.[0].verify_record.current_versions | type == "number") and
      (.[0].verify_record.mandate_assignment_count | type == "number")
    ' "$VERIFICATION_FILE" >/dev/null || die \
      "Verification evidence is not one complete successful pinned v2 verify record"
  record_without_digest="$(jq -S -c 'del(.verification_record_digest)' "$VERIFICATION_FILE")"
  calculated_digest="$(sha256_text "$record_without_digest")"
  [[ "$calculated_digest" == "$(jq -r '.verification_record_digest' "$VERIFICATION_FILE")" ]] || die \
    "Verification evidence digest does not match its complete record"
  mtime="$(stat -f %m "$VERIFICATION_FILE" 2>/dev/null || stat -c %Y "$VERIFICATION_FILE")"
  now="$(date +%s)"
  age=$((now - mtime))
  [[ "$age" -ge 0 && "$age" -le "$MAX_VERIFICATION_AGE_SECONDS" ]] || die \
    "Verification evidence is stale; run v2 verify again"
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

finalize_v1() {
  validate_index_names
  verification_file_is_fresh
  enter_maintenance_mode
  wait_for_active_executions_to_drain
  load_inventory
  local evidence_image evidence_source evidence_validation_digest evidence_processing_hash
  local validation_execution verification_execution
  evidence_image="$(jq -r '.image_digest' "$VERIFICATION_FILE")"
  evidence_source="$(jq -r '.source_inventory_digest' "$VERIFICATION_FILE")"
  evidence_validation_digest="$(jq -r '.validation_record_digest' "$VERIFICATION_FILE")"
  evidence_processing_hash="$(jq -r '.processing_hash' "$VERIFICATION_FILE")"
  validation_execution="$(jq -r '.validation_execution_id' "$VERIFICATION_FILE")"
  verification_execution="$(jq -r '.verification_execution_id' "$VERIFICATION_FILE")"
  [[ "$SOURCE_INVENTORY_DIGEST" == "$evidence_source" ]] || die \
    "Source inventory changed since v2 verification"
  [[ "$evidence_processing_hash" =~ ^[0-9a-f]{64}$ ]] || die \
    "Verification evidence processing hash is invalid"
  [[ "$(jq -r '.verify_record.processing_hash' "$VERIFICATION_FILE")" == "$evidence_processing_hash" ]] || die \
    "Verification evidence processing hash does not match the verify record"
  [[ -n "$evidence_validation_digest" ]] || die \
    "Verification evidence is missing the validation record digest"
  search_index_exists "$V2_INDEX" || die \
    "v2 Search index does not exist; refuse to finalize"
  search_index_exists "$V1_INDEX" || die \
    "v1 Search index is already absent; refuse stale or repeated finalize"
  local live_image validation_status verification_status live_chunk_count expected_chunk_count
  live_image="$(
    "${AZ_CMD[@]}" containerapp job show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "properties.template.containers[?name=='directive-ingestion'].image | [0]" \
      --output tsv
  )"
  [[ "$live_image" == *"@$evidence_image" ]] || die \
    "Live job image does not match the pinned verification image"
  validation_status="$(
    "${AZ_CMD[@]}" containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$validation_execution" \
      --query properties.status \
      --output tsv
  )"
  verification_status="$(
    "${AZ_CMD[@]}" containerapp job execution show \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$verification_execution" \
      --query properties.status \
      --output tsv
  )"
  [[ "$validation_status" == Succeeded && "$verification_status" == Succeeded ]] || die \
    "Validation or verification execution is not currently successful"
  live_chunk_count="$(
    "${AZ_CMD[@]}" rest \
      --method get \
      --url "https://${SEARCH_NAME}.search.windows.net/indexes/$V2_INDEX/docs/\$count?api-version=2026-04-01" \
      --resource "https://search.azure.com" \
      --output tsv
  )"
  expected_chunk_count="$(jq -r '.verify_record.published_chunks' "$VERIFICATION_FILE")"
  [[ "$live_chunk_count" == "$expected_chunk_count" ]] || die \
    "Live v2 Search count does not match the pinned verification record"
  [[ "$CONFIRMATION_TOKEN" == "$(finalize_token)" ]] || die \
    "Finalize token is wrong or stale; run a fresh finalize dry-run"
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
      validate_inventory_evidence finalize "$CONFIRMATION_TOKEN"
      finalize_v1
    fi
    ;;
  *)
    die "unsupported mode: $MODE"
    ;;
esac
