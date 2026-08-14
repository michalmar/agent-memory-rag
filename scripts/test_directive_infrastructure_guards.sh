#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESET_SCRIPT="$SCRIPT_DIR/reset_directive_derived_data.sh"
FIXTURE="$(mktemp)"
MOCK_AZ="$(mktemp)"
MOCK_TERRAFORM="$(mktemp)"
MOCK_LOG="$(mktemp)"
EVIDENCE="$(mktemp)"
trap 'rm -f "$FIXTURE" "$MOCK_AZ" "$MOCK_TERRAFORM" "$MOCK_LOG" "$EVIDENCE"' EXIT

cat >"$FIXTURE" <<'EOF'
{"name":"directive-ingestion","command":["directive-ingest"],"args":["verify"],"image":"registry.example/directive-ingestion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
EOF

jq -e '
  (.command | if type == "array" then . else [.] end) == ["directive-ingest"] and
  (.args | if type == "array" then . else [.] end) == ["verify"]
' "$FIXTURE" >/dev/null

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

printf 'directive-infrastructure-guards=pass\n'
