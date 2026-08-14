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
INVENTORY_OUTPUT="$(mktemp)"
ENVIRONMENT="$(mktemp)"
VALIDATE_RECORD="$(mktemp)"
VERIFY_RECORD="$(mktemp)"
NORMALIZED_RECORD="$(mktemp)"
BAD_RECORD="$(mktemp)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/directive_infrastructure_guards.sh"
trap 'rm -f "$FIXTURE" "$FIXTURE.verify" "$PLAN" "$BAD_PLAN" "$RAW_LOG" "$RECORD" "$OVERSIZE_LOG" "$MOCK_AZ" "$MOCK_TERRAFORM" "$MOCK_LOG" "$EVIDENCE" "$INVENTORY_OUTPUT" "$ENVIRONMENT" "$VALIDATE_RECORD" "$VERIFY_RECORD" "$NORMALIZED_RECORD" "$BAD_RECORD"' EXIT

/bin/bash -u "$SCRIPT_DIR/deploy_directive_ingestion.sh" --self-test >/dev/null
/bin/bash -u "$RESET_SCRIPT" --self-test >/dev/null
if grep -REq 'reset-publication-guards|reconcile-documents|publish-mandates' \
  "$SCRIPT_DIR/deploy_directive_ingestion.sh" \
  "$RESET_SCRIPT" \
  "$SCRIPT_DIR/directive_infrastructure_guards.sh"; then
  echo "removed publication CLI was referenced by infrastructure" >&2
  exit 1
fi
if grep -q 'Nonempty DIRECTIVE_APPROVED_\*_DIGEST' \
  "$SCRIPT_DIR/deploy_directive_ingestion.sh"; then
  echo "deployment retained the obsolete caller-controlled approval gate" >&2
  exit 1
fi

cat >"$FIXTURE" <<'EOF'
{"name":"directive-ingestion","command":["directive-ingest"],"args":["verify"],"image":"registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
EOF

directive_assert_execution_mode_json "$(<"$FIXTURE")" verify
cat >"$FIXTURE" <<'EOF'
{"name":"directive-ingestion","command":["directive-ingest"],"args":["validate"],"image":"registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
EOF
directive_assert_unapproved_execution_json "$(<"$FIXTURE")" validate

cat >"$FIXTURE" <<'EOF'
{"name":"directive-ingestion","command":["directive-ingest"],"args":["run-daily"],"image":"registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","env":[{"name":"DIRECTIVE_PROCESSING_VERSION","value":"directive-v2-czech-layout"},{"name":"DIRECTIVE_SEARCH_INDEX","value":"directive-chunks-v2"},{"name":"DIRECTIVE_APPROVED_VALIDATION_DIGEST","value":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},{"name":"DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST","value":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},{"name":"DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST","value":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"}]}
EOF
directive_assert_approved_execution_json \
  "$(<"$FIXTURE")" \
  run-daily \
  registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee \
  ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
  dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd \
  directive-v2-czech-layout directive-chunks-v2
BEFORE_EXECUTIONS='[{"name":"old-run"}]'
NEW_EXECUTION='[{"name":"old-run"},{"name":"new-run","properties":{"template":{"containers":[{"name":"directive-ingestion","command":["directive-ingest"],"args":["run-daily"],"image":"registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","env":[{"name":"DIRECTIVE_PROCESSING_VERSION","value":"directive-v2-czech-layout"},{"name":"DIRECTIVE_SEARCH_INDEX","value":"directive-chunks-v2"},{"name":"DIRECTIVE_APPROVED_VALIDATION_DIGEST","value":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},{"name":"DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST","value":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},{"name":"DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST","value":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"}]}]}}}]'
if directive_select_new_approved_execution_names \
  "$BEFORE_EXECUTIONS" "$BEFORE_EXECUTIONS" run-daily \
  registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee \
  ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
  dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd \
  directive-v2-czech-layout directive-chunks-v2 >/dev/null 2>&1; then
  echo "zero-candidate lost-response recovery was accepted" >&2
  exit 1
fi
 [[ "$(directive_select_new_approved_execution_names \
  "$BEFORE_EXECUTIONS" "$NEW_EXECUTION" run-daily \
  registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee \
  ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
  dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd \
  directive-v2-czech-layout directive-chunks-v2)" == new-run ]]
 AMBIGUOUS_EXECUTION="$(jq -c '. + [.[1] | .name = "new-run-2"]' <<<"$NEW_EXECUTION")"
 if directive_select_new_approved_execution_names \
  "$BEFORE_EXECUTIONS" \
  "$AMBIGUOUS_EXECUTION" \
  run-daily \
  registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee \
  ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
  dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd \
  directive-v2-czech-layout directive-chunks-v2 >/dev/null; then
  echo "ambiguous new execution recovery was accepted" >&2
  exit 1
 fi
sed 's/"run-daily"/"verify"/' "$FIXTURE" >"$FIXTURE.verify"
directive_assert_approved_execution_json \
  "$(<"$FIXTURE.verify")" \
  verify \
  registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee \
  ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
  dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd \
  directive-v2-czech-layout directive-chunks-v2
cat >"$FIXTURE" <<'EOF'
{"record_schema":"directive.approval.v2","validation_digest":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","environment_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","source_inventory_digest":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","processing_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","mandate_checksum":"2222222222222222222222222222222222222222222222222222222222222222"}
EOF
jq -e 'keys == ["environment_digest","mandate_checksum","processing_hash","record_schema","source_inventory_digest","validation_digest"]' "$FIXTURE" >/dev/null
if jq -e '.wrapper' "$FIXTURE" >/dev/null 2>&1; then
  echo "approval producer marker contains wrapper provenance" >&2
  exit 1
fi

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

python3 - "$ENVIRONMENT" "$VALIDATE_RECORD" "$VERIFY_RECORD" <<'PY'
import hashlib
import json
import pathlib
import sys

environment_path, validate_path, verify_path = map(pathlib.Path, sys.argv[1:])
environment = {
    "source_kind": "azure_blob",
    "source_storage_account": "sttest",
    "source_container": "directive-source",
    "source_prefix": "",
    "artifact_storage_account": "sttest",
    "artifact_container": "directive-artifacts",
    "cosmos_account": "costest",
    "cosmos_database": "directives",
    "catalog_container": "catalog",
    "content_container": "directive_content",
    "mandate_container": "user_mandates",
    "search_service": "searchtest",
    "search_index": "directive-chunks-v2",
}
digest = lambda value: hashlib.sha256(
    json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
source = "b" * 64
base = {
    "record_schema": "directive.validate.v2",
    "success": True,
    "run_id": "validate-run",
    "environment": environment,
    "environment_digest": digest(environment),
    "processing_version": "directive-v2-czech-layout",
    "processing_hash": "a" * 64,
    "search_index": "directive-chunks-v2",
    "source_inventory_digest": source,
    "source_count": 1,
    "directive_count": 1,
    "normalized_directive_ids": ["D-1"],
    "directive_version_ids": ["V-1"],
    "mandate_count": 0,
    "mandate_user_count": 0,
    "mandate_checksum": "2" * 64,
    "warnings": [],
    "warning_count": 0,
    "failures": [],
}
base["validation_digest"] = digest({
    k: v for k, v in base.items() if k not in {"run_id", "validation_digest"}
})
validate_path.write_text(json.dumps(base, separators=(",", ":")) + "\n")
cross_store = {
    "catalog": {"directive_count": 1, "version_count": 1, "current_count": 1, "identity_digest": "c" * 64},
    "content": {"item_count": 1, "section_count": 1, "part_count": 1, "identity_digest": "d" * 64},
    "artifacts": {"object_count": 1, "required_count": 1, "identity_digest": "e" * 64},
    "source_state": {"record_count": 1, "identity_digest": "f" * 64},
    "search": {
        "document_count": 1, "current_document_count": 1, "directive_count": 1,
        "version_count": 1, "vector_dimensions": 3072, "vector_profile": "p",
        "vectorizer": "v", "semantic_configuration": "s", "direct_hybrid_query": "true",
        "identity_digest": "1" * 64,
    },
    "mandates": {
        "snapshot_id": "snapshot", "checksum": "2" * 64, "assignment_count": 0,
        "user_count": 0, "identity_digest": "3" * 64,
    },
}
verify = {k: v for k, v in base.items() if k not in {"record_schema", "mandate_count", "mandate_user_count", "failures", "validation_digest"}}
verify.update({"record_schema": "directive.verify.v2", "run_id": "verify-run", "warnings": [], "warning_count": 0, "cross_store": cross_store})
verify["validation_digest"] = base["validation_digest"]
projection = {k: verify[k] for k in (
    "record_schema", "environment", "environment_digest",
    "processing_version", "processing_hash",
    "search_index", "source_count", "source_inventory_digest", "directive_count",
    "normalized_directive_ids", "directive_version_ids", "validation_digest",
    "mandate_checksum",
    "cross_store",
)}
verify["state_digest"] = digest(projection)
verify["verify_digest"] = digest({k: v for k, v in verify.items() if k != "verify_digest"})
verify_path.write_text(json.dumps(verify, separators=(",", ":")) + "\n")
environment_path.write_text(json.dumps(environment, separators=(",", ":")))
PY
directive_validate_producer_record \
  "$VALIDATE_RECORD" "$NORMALIZED_RECORD" \
  directive.validate.v2 "$ENVIRONMENT" bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  directive-v2-czech-layout directive-chunks-v2
directive_validate_producer_record \
  "$VERIFY_RECORD" "$NORMALIZED_RECORD" \
  directive.verify.v2 "$ENVIRONMENT" bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  directive-v2-czech-layout directive-chunks-v2

expect_invalid_verify() {
  local label="$1"
  if directive_validate_producer_record \
    "$BAD_RECORD" "$NORMALIZED_RECORD" \
    directive.verify.v2 "$ENVIRONMENT" bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
    directive-v2-czech-layout directive-chunks-v2 >/dev/null 2>&1; then
    echo "$label was accepted" >&2
    exit 1
  fi
}

while IFS= read -r invalid_label; do
  python3 - "$VERIFY_RECORD" "$BAD_RECORD" "$invalid_label" <<'PY'
import json
import pathlib
import sys
record = json.loads(pathlib.Path(sys.argv[1]).read_text())
label = sys.argv[3]
if label == "bool warning_count":
    record["warning_count"] = True
elif label == "extra top-level field":
    record["unexpected"] = "field"
elif label == "wrapper execution id":
    record["verification_execution_id"] = "azure-name"
elif label == "invalid severity":
    record["warnings"] = [{"code": "W1", "severity": "info"}]
    record["warning_count"] = 1
elif label == "duplicate warnings":
    record["warnings"] = [{"code": "W1", "severity": "warning"}] * 2
    record["warning_count"] = 2
elif label == "unsorted warnings":
    record["warnings"] = [
        {"code": "W2", "severity": "warning"},
        {"code": "W1", "severity": "warning"},
    ]
    record["warning_count"] = 2
elif label == "mandate checksum mismatch":
    record["cross_store"]["mandates"]["checksum"] = "3" * 64
pathlib.Path(sys.argv[2]).write_text(json.dumps(record), encoding="utf-8")
PY
  expect_invalid_verify "$invalid_label"
done <<'EOF'
bool warning_count
extra top-level field
wrapper execution id
invalid severity
duplicate warnings
unsorted warnings
mandate checksum mismatch
EOF

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
  /bin/bash -u "$RESET_SCRIPT" dry-run --inventory-evidence "$EVIDENCE" >"$INVENTORY_OUTPUT"
if MOCK_LOG="$MOCK_LOG" AZ_BIN="$MOCK_AZ" TERRAFORM_BIN="$MOCK_TERRAFORM" \
  /bin/bash -u "$RESET_SCRIPT" finalize >/dev/null 2>/dev/null; then
  echo "finalize without evidence was accepted before execution" >&2
  exit 1
fi
grep -q '^artifact_prefix=publication-lock/$' "$INVENTORY_OUTPUT"
grep -q '^artifact_prefix=publication-claims/$' "$INVENTORY_OUTPUT"
if grep -Eq 'containerapp job update|containerapp job stop|cosmosdb sql container delete|terraform.*(plan|apply)' "$MOCK_LOG"; then
  echo "dry-run issued a mutating Azure or Terraform command" >&2
  exit 1
fi
if grep -Eq '(^| )plan( |$)|(^| )apply( |$)' "$MOCK_LOG"; then
  echo "dry-run issued an unexpected Terraform plan/apply operation" >&2
  exit 1
fi

printf 'directive-infrastructure-guards=pass\n'
