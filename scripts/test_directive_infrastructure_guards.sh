#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESET_SCRIPT="$SCRIPT_DIR/reset_directive_derived_data.sh"
FIXTURE="$(mktemp)"
PLAN="$(mktemp)"
BAD_PLAN="$(mktemp)"
RAW_LOG="$(mktemp)"
RECORD="$(mktemp)"
OVERSIZE_LOG="$(mktemp)"
MOCK_AZ="$(mktemp)"
MOCK_TERRAFORM="$(mktemp)"
MOCK_LOG="$(mktemp)"
EVIDENCE="$(mktemp)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/directive_infrastructure_guards.sh"
trap 'rm -f "$FIXTURE" "$PLAN" "$BAD_PLAN" "$RAW_LOG" "$RECORD" "$OVERSIZE_LOG" "$MOCK_AZ" "$MOCK_TERRAFORM" "$MOCK_LOG" "$EVIDENCE"' EXIT

cat >"$FIXTURE" <<'EOF'
{"name":"directive-ingestion","command":["directive-ingest"],"args":["verify"],"image":"registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
EOF

directive_assert_execution_mode_json "$(<"$FIXTURE")" verify

printf '%s\n' 'INFO prefix' '{"success":true,"environment":{},"cross_store":{"content":{"count":1}}}' >"$RAW_LOG"
directive_extract_producer_record "$RAW_LOG" "$RECORD"
jq -e '.success == true and .cross_store.content.count == 1' "$RECORD" >/dev/null

printf '%s\n' '{"success":true}' '{"success":true}' >"$RAW_LOG"
if directive_extract_producer_record "$RAW_LOG" "$RECORD"; then
  echo "duplicate producer records were accepted" >&2
  exit 1
fi
printf '%s\n' '{"success":true}' '{"success":true' >"$RAW_LOG"
if directive_extract_producer_record "$RAW_LOG" "$RECORD"; then
  echo "complete plus truncated producer records were accepted" >&2
  exit 1
fi
printf '%s\n' '{"outer":{"success":true}' >"$RAW_LOG"
if directive_extract_producer_record "$RAW_LOG" "$RECORD"; then
  echo "truncated outer producer candidate was accepted" >&2
  exit 1
fi
printf '%s' '{"success":true' >"$RAW_LOG"
if directive_extract_producer_record "$RAW_LOG" "$RECORD"; then
  echo "partial producer record was accepted" >&2
  exit 1
fi
python3 - "$OVERSIZE_LOG" <<'PY'
import json
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(
    json.dumps({"success": True, "padding": "x" * 65536}) + "\n",
    encoding="utf-8",
)
PY
if directive_extract_producer_record "$OVERSIZE_LOG" "$RECORD"; then
  echo "oversize producer record was accepted" >&2
  exit 1
fi

cat >"$PLAN" <<'EOF'
{"resource_changes":[
  {"address":"azurerm_cosmosdb_sql_container.directive_catalog","change":{"actions":["no-op"]}},
  {"address":"azurerm_cosmosdb_sql_container.directive_catalog","change":{"actions":["create"]}},
  {"address":"azurerm_cosmosdb_sql_container.directive_content","change":{"actions":["create"]}},
  {"address":"azurerm_cosmosdb_sql_container.directive_mandates","change":{"actions":["create"]}}
]}
EOF
directive_assert_cosmos_recreation_plan "$PLAN"
sed 's/"actions":\["create"\]}/"actions":["update"]}/' "$PLAN" >"$BAD_PLAN"
if directive_assert_cosmos_recreation_plan "$BAD_PLAN"; then
  echo "non-create Terraform plan was accepted" >&2
  exit 1
fi

cat >"$MOCK_AZ" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$MOCK_LOG"
case "$*" in
  *"account show"*"--query id"*) printf 'sub-123\n' ;;
  *"account show"*"--query name"*) printf 'test-subscription\n' ;;
  *"storage blob list"*) printf 'source.pdf\n' ;;
  *"storage blob download"*)
    previous=""
    for arg in "$@"; do
      if [[ "$previous" == "--file" ]]; then
        printf 'mock-source-content\n' >"$arg"
      fi
      previous="$arg"
    done
    ;;
  *"containerapp job execution list"*) ;;
  *) printf '\n' ;;
esac
EOF

cat >"$MOCK_TERRAFORM" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$MOCK_LOG"
case "$*" in
  *" plan "*|*" apply "*) echo "unexpected Terraform mutation" >&2; exit 99 ;;
esac
case "$*" in
  *"output -raw resource_group"*) printf 'rg-test\n' ;;
  *"output -raw directive_artifacts_storage_account"*) printf 'sttest\n' ;;
  *"output -raw directive_artifacts_container"*) printf 'directive-artifacts\n' ;;
  *"output -raw directive_source_container"*) printf 'directive-source\n' ;;
  *"output -raw directive_source_prefix"*) printf '\n' ;;
  *"output -raw cosmos_endpoint"*) printf 'https://costest.documents.azure.com:443/\n' ;;
  *"output -raw directive_cosmos_database"*) printf 'directives\n' ;;
  *"output -raw directive_catalog_container"*) printf 'catalog\n' ;;
  *"output -raw directive_content_container"*) printf 'directive_content\n' ;;
  *"output -raw directive_mandates_container"*) printf 'user_mandates\n' ;;
  *"output -raw search_service_name"*) printf 'searchtest\n' ;;
  *"output -raw directive_ingestion_job_name"*) printf 'job-test-directive-ingest\n' ;;
  *) printf '\n' ;;
esac
EOF
chmod +x "$MOCK_AZ" "$MOCK_TERRAFORM"

MOCK_LOG="$MOCK_LOG" AZ_BIN="$MOCK_AZ" TERRAFORM_BIN="$MOCK_TERRAFORM" \
  bash "$RESET_SCRIPT" dry-run --inventory-evidence "$EVIDENCE" >/dev/null
if grep -Eq 'containerapp job update|containerapp job stop|cosmosdb sql container delete|terraform.*(plan|apply)' "$MOCK_LOG"; then
  echo "dry-run issued a mutating Azure or Terraform command" >&2
  exit 1
fi
if grep -Eq '(^| )plan( |$)|(^| )apply( |$)' "$MOCK_LOG"; then
  echo "dry-run issued an unexpected Terraform plan/apply operation" >&2
  exit 1
fi

printf 'directive-infrastructure-guards=pass\n'
