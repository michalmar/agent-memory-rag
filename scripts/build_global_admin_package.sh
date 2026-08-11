#!/usr/bin/env bash
# Build a sanitized, self-contained deployment archive for a Global
# Administrator who cannot access this repository.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="${1:-$REPO_ROOT/dist}"
BUILD_ID="$(date -u +%Y%m%dT%H%M%SZ)"
PACKAGE_NAME="agent-memory-rag-global-admin-${BUILD_ID}"
ARCHIVE="$OUTPUT_DIR/${PACKAGE_NAME}.zip"
ARCHIVE_CHECKSUM="${ARCHIVE}.sha256"

for tool in git zip find awk grep; do
  command -v "$tool" >/dev/null || {
    echo "ERROR: ${tool} is required to build the package" >&2
    exit 1
  }
done

if command -v sha256sum >/dev/null; then
  SHA256=(sha256sum)
elif command -v shasum >/dev/null; then
  SHA256=(shasum -a 256)
else
  echo "ERROR: sha256sum or shasum is required" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
work_dir="$(mktemp -d)"
stage="$work_dir/$PACKAGE_NAME"
mkdir -p "$stage"

cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

copy_file() {
  local source_path="$1"
  local destination_path="${2:-$1}"
  [[ -f "$REPO_ROOT/$source_path" ]] || {
    echo "ERROR: required package file is missing: ${source_path}" >&2
    exit 1
  }
  mkdir -p "$(dirname "$stage/$destination_path")"
  cp "$REPO_ROOT/$source_path" "$stage/$destination_path"
}

include_tracked_path() {
  case "$1" in
    .dockerignore|README.md|agent_contracts/*|agents/*|backend/*|\
    directive_contracts/*|frontend/*|infra/*|maf_hosting/*|scripts/*|setup/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

exclude_tracked_path() {
  case "$1" in
    */.DS_Store|*/tests/*|*.test.ts|infra/tfplan|\
    scripts/build_global_admin_package.sh)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

while IFS= read -r -d '' path; do
  include_tracked_path "$path" || continue
  exclude_tracked_path "$path" && continue
  copy_file "$path"
done < <(git -C "$REPO_ROOT" ls-files -z)

# These files can be packaged before they are committed.
for path in \
  scripts/global_admin_access.sh \
  scripts/archive_deployment_evidence.sh \
  scripts/validate_global_admin_inputs.sh \
  scripts/configure_directive_model_mode.sh \
  scripts/configure_hosted_agent_endpoint.sh \
  scripts/register_providers.sh; do
  copy_file "$path"
done

copy_file \
  docs/GLOBAL-ADMIN-CROSS-TENANT-PLAN.md \
  START-HERE.md
copy_file \
  docs/GLOBAL-ADMIN-CROSS-TENANT-PLAN.md \
  docs/GLOBAL-ADMIN-CROSS-TENANT-PLAN.md
copy_file \
  docs/CROSS-TENANT-AZURE-DEPLOYMENT.md \
  docs/CROSS-TENANT-AZURE-DEPLOYMENT.md
copy_file \
  docs/global-admin-package/global-admin-inputs.env.example \
  global-admin-inputs.env.example

sanitize_manifest() {
  local manifest="$1"
  local image_name="$2"
  local rendered
  rendered="$(mktemp)"
  if ! awk \
    -v endpoint="https://replace-after-terraform.invalid/api/projects/replace-after-terraform" \
    -v image="replace-after-build.invalid/${image_name}:replace-before-azd-deploy" '
      /^[[:space:]]*endpoint:[[:space:]]*/ {
        endpoint_count += 1
        match($0, /^[[:space:]]*/)
        print substr($0, RSTART, RLENGTH) "endpoint: " endpoint
        next
      }
      /^[[:space:]]*image:[[:space:]]*/ {
        image_count += 1
        match($0, /^[[:space:]]*/)
        print substr($0, RSTART, RLENGTH) "image: " image
        next
      }
      { print }
      END {
        if (endpoint_count != 1 || image_count != 1) {
          exit 1
        }
      }
    ' "$manifest" >"$rendered"; then
    rm -f "$rendered"
    echo "ERROR: could not sanitize ${manifest#"$stage"/}" >&2
    exit 1
  fi
  mv "$rendered" "$manifest"
}

sanitize_manifest \
  "$stage/agents/customer-support-maf/azure.yaml" \
  customer-support-maf-hosted
sanitize_manifest \
  "$stage/agents/directive-rag-maf/azure.yaml" \
  directive-rag-maf-hosted

chmod +x "$stage"/scripts/*.sh

source_commit="$(git -C "$REPO_ROOT" rev-parse HEAD)"
cat >"$stage/PACKAGE-SOURCE.txt" <<EOF
Repository: michalmar/agent-memory-rag
Source commit: ${source_commit}
Built UTC: ${BUILD_ID}

The archive contains current working-tree versions of the selected deployment
files. It intentionally excludes Git metadata, local Terraform state and
variables, saved plans, caches, tests, and the historical source-environment
.azure/deployment-plan.md.
EOF

# Fail closed if known local target/source identifiers survived the export.
blocklist="$work_dir/source-values.txt"
: >"$blocklist"
if [[ -f "$REPO_ROOT/infra/terraform.tfvars" ]]; then
  grep -Eo \
    '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' \
    "$REPO_ROOT/infra/terraform.tfvars" |
    sort -u >>"$blocklist" || true
fi
for manifest in \
  "$REPO_ROOT/agents/customer-support-maf/azure.yaml" \
  "$REPO_ROOT/agents/directive-rag-maf/azure.yaml"; do
  awk '/^[[:space:]]*(endpoint|image):[[:space:]]*/ { print $2 }' \
    "$manifest" >>"$blocklist"
done
sort -u "$blocklist" -o "$blocklist"

while IFS= read -r value; do
  [[ -n "$value" ]] || continue
  if LC_ALL=C grep -R -F -l -- "$value" "$stage" >/dev/null; then
    echo "ERROR: source-environment value survived package sanitization" >&2
    exit 1
  fi
done <"$blocklist"

if find "$stage" \
  \( -name .git -o -name .terraform -o -name terraform.tfvars \
     -o -name '*.tfstate' -o -name '*.tfstate.*' -o -name '*.tfplan' \
     -o -name tfplan -o -name .env -o -path '*/.azure/*' \) \
  -print | grep -q .; then
  echo "ERROR: local state, credentials, or saved plans entered the package" >&2
  exit 1
fi

(
  cd "$stage"
  find . -type f ! -name SHA256SUMS -print |
    LC_ALL=C sort >SOURCE-MANIFEST.txt
  while IFS= read -r file; do
    "${SHA256[@]}" "$file"
  done <SOURCE-MANIFEST.txt >SHA256SUMS
)

(
  cd "$work_dir"
  zip -q -r "$ARCHIVE" "$PACKAGE_NAME"
)
(
  cd "$OUTPUT_DIR"
  "${SHA256[@]}" "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE_CHECKSUM")"
)

printf 'Package:  %s\n' "$ARCHIVE"
printf 'Checksum: %s\n' "$ARCHIVE_CHECKSUM"
