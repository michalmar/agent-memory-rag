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
V1_INDEX="${DIRECTIVE_SEARCH_V1_INDEX:-directive-chunks-v1}"
V2_INDEX="${DIRECTIVE_SEARCH_V2_INDEX:-}"
MODE="dry-run"
CONFIRMATION_TOKEN=""
VERIFICATION_FILE=""

read -r -a AZ_CMD <<<"$AZ_BIN"
read -r -a TERRAFORM_CMD <<<"$TERRAFORM_BIN"

usage() {
  cat <<'EOF'
Usage:
  reset_directive_derived_data.sh [dry-run]
  reset_directive_derived_data.sh reset --execute --confirm TOKEN
  reset_directive_derived_data.sh finalize --execute \
    --verification-file FILE --confirm TOKEN

The reset and finalize commands are destructive. A dry-run is the default and
prints the exact environment inventory and a confirmation token. The token is
valid only while that inventory is unchanged.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

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
        [[ "$MODE" != "dry-run" ]] || MODE="reset"
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
      --v1-index)
        [[ $# -ge 2 ]] || die "--v1-index requires a name"
        V1_INDEX="$2"
        shift 2
        ;;
      --v2-index)
        [[ $# -ge 2 ]] || die "--v2-index requires a name"
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
  SUBSCRIPTION_NAME="$("${AZ_CMD[@]}" account show --query name --output tsv)"
  RG="$(tf_output resource_group)"
  STORAGE_ACCOUNT="$(tf_output directive_artifacts_storage_account)"
  ARTIFACT_CONTAINER="$(tf_output directive_artifacts_container)"
  SOURCE_CONTAINER="$(tf_output directive_source_container)"
  SOURCE_PREFIX="$(tf_output directive_source_prefix)"
  COSMOS_ENDPOINT="$(tf_output cosmos_endpoint)"
  COSMOS_ACCOUNT="$(account_name_from_endpoint "$COSMOS_ENDPOINT")"
  COSMOS_DATABASE="$(tf_output directive_cosmos_database)"
  CATALOG_CONTAINER="$(tf_output directive_catalog_container)"
  CONTENT_CONTAINER="$(tf_output directive_content_container)"
  MANDATES_CONTAINER="$(tf_output directive_mandates_container)"
  SEARCH_NAME="$(tf_output search_service_name)"
  JOB_NAME="$(tf_output directive_ingestion_job_name)"
  [[ -n "$V2_INDEX" ]] || V2_INDEX="$(tf_output directive_search_index_name)"
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

inventory_token() {
  local inventory
  inventory="$(inventory_text)"
  printf 'DIRECTIVE-RESET-V2-%s\n' "$(sha256_text "$inventory" | cut -c1-24)"
}

finalize_token() {
  local inventory verification_hash
  verification_hash="$(sha256_file "$VERIFICATION_FILE")"
  inventory="$(inventory_text)"
  printf 'DIRECTIVE-FINALIZE-V2-%s\n' \
    "$(sha256_text "$inventory
verification_evidence_sha256=$verification_hash" | cut -c1-24)"
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
  echo "confirmation_token=$token"
  echo "No Azure data was changed."
}

container_exists() {
  "${AZ_CMD[@]}" cosmosdb sql container show \
    --account-name "$COSMOS_ACCOUNT" \
    --resource-group "$RG" \
    --database-name "$COSMOS_DATABASE" \
    --name "$1" \
    --output none >/dev/null 2>&1
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
  load_inventory
  [[ -z "$ACTIVE_EXECUTIONS" ]] || die \
    "Container Apps job $JOB_NAME has active execution(s): $ACTIVE_EXECUTIONS"
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
  assert_no_active_execution
  [[ "$CONFIRMATION_TOKEN" == "$(inventory_token)" ]] || die \
    "Confirmation token is wrong or stale; run a fresh dry-run"
  [[ "$CONFIRMATION_TOKEN" == DIRECTIVE-RESET-V2-* ]] || die \
    "Reset requires a DIRECTIVE-RESET-V2 token"

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
  echo "==> Reset complete; v1 Search remains and v2 bootstrap is owned by the ingestion deployment"
}

verification_file_is_fresh() {
  local mtime now age
  [[ -f "$VERIFICATION_FILE" ]] || die \
    "A safe output file from the successful v2 verify execution is required"
  [[ -s "$VERIFICATION_FILE" ]] || die "Verification evidence file is empty"
  [[ "$(wc -c <"$VERIFICATION_FILE")" -le 65536 ]] || die \
    "Verification evidence file is unexpectedly large"
  if grep -Eiq '"(content|markdown|prompt|document_text|administrative_content)"[[:space:]]*:' "$VERIFICATION_FILE"; then
    die "Verification evidence contains a raw content field; use sanitized verify output"
  fi
  grep -Eq '"(published_chunks|directive_ids|current_versions|mandate_)' \
    "$VERIFICATION_FILE" || die \
    "Verification evidence does not contain the required v2 summary fields"
  mtime="$(stat -f %m "$VERIFICATION_FILE" 2>/dev/null || stat -c %Y "$VERIFICATION_FILE")"
  now="$(date +%s)"
  age=$((now - mtime))
  [[ "$age" -ge 0 && "$age" -le "$MAX_VERIFICATION_AGE_SECONDS" ]] || die \
    "Verification evidence is stale; run v2 verify again"
}

search_index_exists() {
  "${AZ_CMD[@]}" rest \
    --method get \
    --url "https://${SEARCH_NAME}.search.windows.net/indexes/$1?api-version=2026-04-01" \
    --resource "https://search.azure.com" \
    --output none >/dev/null 2>&1
}

finalize_v1() {
  verification_file_is_fresh
  load_inventory
  search_index_exists "$V2_INDEX" || die \
    "v2 Search index does not exist; refuse to finalize"
  search_index_exists "$V1_INDEX" || die \
    "v1 Search index is already absent; refuse stale or repeated finalize"
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

parse_args "$@"
load_inventory

case "$MODE" in
  dry-run)
    print_inventory
    ;;
  reset)
    [[ -n "$CONFIRMATION_TOKEN" ]] || die \
      "Reset is destructive; provide --execute --confirm TOKEN from a fresh dry-run"
    reset_derived_data
    ;;
  finalize)
    [[ -n "$VERIFICATION_FILE" ]] || die \
      "Finalize requires --verification-file from a successful v2 verify execution"
    if [[ -z "$CONFIRMATION_TOKEN" ]]; then
      print_inventory
    else
      finalize_v1
    fi
    ;;
  *)
    die "unsupported mode: $MODE"
    ;;
esac
