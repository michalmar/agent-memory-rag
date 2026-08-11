#!/usr/bin/env bash
# Select whether Terraform creates a fresh directive model deployment or adopts
# an existing deployment through the checked-in import block.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_FILE="$REPO_ROOT/infra/directive_data.tf"
MODE="${1:-}"

usage() {
  echo "Usage: $0 fresh|adopt" >&2
}

case "$MODE" in
  adopt)
    grep -q 'to = azurerm_cognitive_deployment.directive' "$TARGET_FILE" || {
      echo "ERROR: adoption mode requires the directive deployment import block" >&2
      exit 1
    }
    echo "Directive model mode: adopt the exact target deployment at the generated import ID."
    ;;
  fresh)
    if ! grep -q 'to = azurerm_cognitive_deployment.directive' "$TARGET_FILE"; then
      echo "Directive model mode is already fresh; no import block remains."
      exit 0
    fi

    rendered="$(mktemp)"
    cleanup() {
      rm -f "$rendered"
    }
    trap cleanup EXIT

    awk '
      BEGIN {
        in_import = 0
        removed = 0
        block = ""
      }
      /^[[:space:]]*import[[:space:]]*\{/ {
        in_import = 1
        block = $0 ORS
        next
      }
      in_import {
        block = block $0 ORS
        if ($0 ~ /^[[:space:]]*}[[:space:]]*$/) {
          if (block ~ /to[[:space:]]*=[[:space:]]*azurerm_cognitive_deployment.directive/) {
            removed += 1
          } else {
            printf "%s", block
          }
          in_import = 0
          block = ""
        }
        next
      }
      { print }
      END {
        if (in_import || removed != 1) {
          exit 1
        }
      }
    ' "$TARGET_FILE" >"$rendered" || {
      echo "ERROR: expected exactly one directive deployment import block" >&2
      exit 1
    }

    mv "$rendered" "$TARGET_FILE"
    trap - EXIT
    terraform -chdir="$REPO_ROOT/infra" fmt directive_data.tf >/dev/null
    echo "Directive model mode: fresh deployment; removed the target-sensitive import block."
    ;;
  *)
    usage
    exit 2
    ;;
esac
