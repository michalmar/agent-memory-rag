#!/usr/bin/env bash
# Build the directive ingestion image through ACR Tasks and update only its job.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
MANDATE_FILE="$REPO_ROOT/setup/directives/mandatory/mand.csv"
PHASE="${DIRECTIVE_INGEST_PHASE:-all}"

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
IMAGE=""
IMAGE_DIGEST=""
JOB_CONTAINER="directive-ingestion"
VALIDATION_CONFIRMATION="${DIRECTIVE_VALIDATE_CONFIRMATION:-}"
VERIFY_EVIDENCE_FILE="${DIRECTIVE_VERIFY_EVIDENCE_FILE:-}"
EXPECTED_PROCESSING_VERSION="directive-v2-czech-layout"
EXPECTED_SEARCH_INDEX="directive-chunks-v2"
MAX_VALIDATION_EVIDENCE_AGE_SECONDS="${DIRECTIVE_VALIDATE_EVIDENCE_MAX_AGE_SECONDS:-86400}"
INDEX_SCHEMA_FILE="$(mktemp)"
VALIDATION_SUMMARY_FILE="$(mktemp)"
VERIFY_SUMMARY_FILE="$(mktemp)"
SOURCE_INVENTORY_FILE="$(mktemp)"
VALIDATION_RECORD_DIGEST=""
SOURCE_INVENTORY_DIGEST=""
STARTED_EXECUTIONS=()

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
  if [[ "$status" -ne 0 && -n "${JOB_NAME:-}" ]]; then
    stop_started_executions
  fi
  rm -f \
    "$ARM_ROLE_SNAPSHOT" \
    "$COSMOS_ROLE_SNAPSHOT" \
    "$INDEX_SCHEMA_FILE" \
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
  fi
  exit "$status"
}
trap cleanup EXIT

stop_started_executions() {
  local execution_name status
  for execution_name in "${STARTED_EXECUTIONS[@]}"; do
    status="$(
      az containerapp job execution show \
        --name "$JOB_NAME" \
        --resource-group "$RG" \
        --job-execution-name "$execution_name" \
        --query properties.status \
        --output tsv 2>/dev/null || true
    )"
    case "$status" in
      Succeeded|Failed|Stopped|Degraded|Canceled|"") continue ;;
    esac
    echo "==> Stopping failed-run execution $execution_name" >&2
    az containerapp job stop \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --job-execution-name "$execution_name" \
      --output none || true
  done
  for ((attempt = 1; attempt <= 30; attempt++)); do
    assert_no_active_execution && return 0
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
    if printf '%s\n' "$trimmed" |
      grep -Eiq '"(content|markdown|prompt|document_text|administrative_content|raw_response)"[[:space:]]*:'; then
      continue
    fi
    sanitized="$(
      printf '%s\n' "$trimmed" |
        jq -ce 'if type == "object" then . else empty end' 2>/dev/null || true
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
  active="$(
    az containerapp job execution list \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --query "[?properties.status!='Succeeded' && properties.status!='Failed' && properties.status!='Stopped' && properties.status!='Degraded' && properties.status!='Canceled'].name" \
      --output tsv
  )"
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

start_job_execution() {
  local expected_argument="$1"
  local execution_name
  local -a execution_env
  execution_env=()
  case "$expected_argument" in
    run-daily)
      execution_env=(
        --env-vars
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST=$DIRECTIVE_APPROVED_VALIDATION_DIGEST"
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST=$DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST"
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST=$DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST"
      )
      ;;
    verify)
      execution_env=(
        --env-vars
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST=$DIRECTIVE_APPROVED_VALIDATION_DIGEST"
      )
      ;;
  esac
  assert_live_maintenance_mode
  assert_no_active_execution
  execution_name="$(
    az containerapp job start \
      --name "$JOB_NAME" \
      --resource-group "$RG" \
      --command directive-ingest \
      --args "$expected_argument" \
      "${execution_env[@]}" \
      --query name \
      --output tsv
  )"
  [[ -n "$execution_name" ]] || {
    echo "ERROR: Container Apps did not return an execution name" >&2
    return 1
  }
  STARTED_EXECUTIONS+=("$execution_name")
  assert_execution_mode "$execution_name" "$expected_argument"
  assert_execution_image "$execution_name"
  printf '%s\n' "$execution_name"
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
$SOURCE_INVENTORY_DIGEST" | cut -c1-24)"
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
  VALIDATION_RECORD_DIGEST="$(jq -r '.wrapper.validation_record_digest // empty' "$VALIDATION_EVIDENCE_FILE")"
  [[ "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation evidence image digest is not immutable" >&2
    return 1
  }
  [[ -n "$VALIDATE_EXECUTION" && "$SOURCE_INVENTORY_DIGEST" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation evidence is missing execution or source digest" >&2
    return 1
  }
  [[ "$VALIDATION_RECORD_DIGEST" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: validation evidence record digest is invalid" >&2
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
      '
        {
          producer_record: .,
          wrapper: {
            image_digest: $image_digest,
            image_reference: $image_reference,
            source_inventory_digest: $source_digest,
            validation_execution_id: $execution
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
  echo "validation_record_digest=$VALIDATION_RECORD_DIGEST"
}

validate_metadata_summary() {
  require_one_summary_record "$VALIDATION_SUMMARY_FILE" "Metadata validation"
  jq -e \
    --arg processing "$EXPECTED_PROCESSING_VERSION" \
    --arg search_index "$EXPECTED_SEARCH_INDEX" \
    '
      .success == true and
      (.normalized_directive_ids | type == "array") and
      (.directive_version_ids | type == "array") and
      (.warnings | type == "array" and length <= 100) and
      (.environment | type == "object") and
      .processing_version == $processing and
      (.processing_hash | test("^[0-9a-f]{64}$")) and
      .search_index == $search_index and
      (.source_count | type == "number" and . > 0) and
      (.source_inventory_digest | test("^[0-9a-f]{64}$")) and
      (.validation_execution_id | type == "string" and length > 0) and
      (.validation_digest | test("^[0-9a-f]{64}$"))
    ' "$VALIDATION_SUMMARY_FILE" >/dev/null || {
    echo "ERROR: validation summary does not match the complete v2 contract" >&2
    return 1
  }
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
      '. + {
        wrapper: {
          image_digest: $image_digest,
          image_reference: $image_reference,
          source_inventory_digest: $source_digest,
          validation_execution_id: $execution
        }
      }' <(printf '%s\n' "$canonical_record")
  )"
  actual_digest="$(sha256_text "$canonical_record")"
  evidence_digest="$(jq -r '.wrapper.validation_record_digest' "$VALIDATION_EVIDENCE_FILE")"
  [[ "$actual_digest" == "$evidence_digest" ]] || die \
    "Validation evidence does not match the pinned Azure execution output"
  VALIDATION_RECORD_DIGEST="$actual_digest"
}

write_verification_evidence() {
  local source_record="$1"
  [[ -n "$VERIFY_EVIDENCE_FILE" ]] || return 0
  require_one_summary_record "$source_record" "Directive verification"
  local canonical_record record_digest
  canonical_record="$(
    jq -S -c \
      --arg image_digest "$IMAGE_DIGEST" \
      --arg source_digest "$SOURCE_INVENTORY_DIGEST" \
      --arg validation_execution "$VALIDATE_EXECUTION" \
      --arg validation_digest "$VALIDATION_RECORD_DIGEST" \
      --arg execution "$VERIFY_EXECUTION" \
      '
        {
          producer_record: .,
          wrapper: {
            image_digest: $image_digest,
            source_inventory_digest: $source_digest,
            validation_execution_id: $validation_execution,
            validation_record_digest: $validation_digest,
            verification_execution_id: $execution
          }
        }
      ' "$source_record"
  )"
  record_digest="$(sha256_text "$canonical_record")"
  jq -e \
    --arg digest "$record_digest" \
    --arg verify_digest "$(sha256_text "$canonical_record")" \
    '
      (.producer_record | type == "object") and
      .producer_record.success == true and
      (.producer_record.verify_execution_id | type == "string" and length > 0) and
      (.producer_record.environment | type == "object") and
      .producer_record.search_index == "directive-chunks-v2" and
      .producer_record.processing_version == "directive-v2-czech-layout" and
      (.producer_record.processing_hash | test("^[0-9a-f]{64}$")) and
      (.producer_record.source_inventory_digest | test("^[0-9a-f]{64}$")) and
      (.producer_record.verify_digest | type == "string" and length > 0) and
      (.producer_record.cross_store | type == "object") and
      .wrapper.verification_execution_id != "" and
      .wrapper.validation_execution_id != "" and
      .wrapper.image_digest != "" and
      .wrapper.source_inventory_digest != "" and
      $digest == $verify_digest
    ' <(printf '%s\n' "$canonical_record") >/dev/null || {
    echo "ERROR: verification summary is incomplete for v2 finalization" >&2
    return 1
  }
  jq -S -c \
    --arg digest "$record_digest" \
    '.wrapper.verification_record_digest = $digest | .' \
    <(printf '%s\n' "$canonical_record") >"$VERIFY_EVIDENCE_FILE"
  jq -e \
    '
      .producer_record.search_index == "directive-chunks-v2" and
      .producer_record.processing_version == "directive-v2-czech-layout"
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
  BOOTSTRAP_EXECUTION="$(start_job_execution bootstrap)"
  echo "==> Bootstrap execution: $BOOTSTRAP_EXECUTION"
  wait_for_execution "$BOOTSTRAP_EXECUTION" "Search bootstrap" 120 10
  assert_live_v2_config
  assert_v2_search_schema

  echo "==> Running managed-identity data-plane preflight"
  PREFLIGHT_SUCCEEDED=false
  for attempt in {1..5}; do
    ensure_maintenance_mode
    PREFLIGHT_EXECUTION="$(start_job_execution preflight)"
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
  VALIDATE_EXECUTION="$(start_job_execution validate)"
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

echo "==> Starting approved publication through a per-execution override"
EXECUTION_NAME="$(start_job_execution run-daily)"
echo "==> Ingestion execution: $EXECUTION_NAME"
wait_for_execution "$EXECUTION_NAME" "Directive ingestion" 240 30

echo "==> Verifying published directive state"
VERIFY_EXECUTION="$(start_job_execution verify)"
echo "==> Verification execution: $VERIFY_EXECUTION"
wait_for_execution "$VERIFY_EXECUTION" "Directive verification" 120 10
refresh_source_inventory
[[ "$SOURCE_INVENTORY_DIGEST" == "$(jq -r '.producer_record.source_inventory_digest' "$VALIDATION_EVIDENCE_FILE")" ]] || \
  die "Source inventory changed before verification evidence was captured"
write_verification_evidence "$VERIFY_SUMMARY_FILE"

echo "==> Directive ingestion succeeded: $EXECUTION_NAME; job remains in maintenance mode"
