#!/usr/bin/env bash
# Move sensitive deployment artifacts into the marked evidence directory without
# overwriting any existing evidence.
set -euo pipefail

INPUTS_FILE=""
DESTINATION_NAME=""
sources=()

usage() {
  cat <<'USAGE' >&2
Usage:
  archive_deployment_evidence.sh --inputs <file> <source> [<source> ...]
  archive_deployment_evidence.sh --inputs <file> --name <name> <source>
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inputs)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      INPUTS_FILE="$2"
      shift 2
      ;;
    --name)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      DESTINATION_NAME="$2"
      shift 2
      ;;
    -*)
      echo "ERROR: unknown argument: $1" >&2
      usage
      exit 2
      ;;
    *)
      sources+=("$1")
      shift
      ;;
  esac
done

[[ -n "$INPUTS_FILE" && -f "$INPUTS_FILE" ]] || {
  echo "ERROR: --inputs must reference an existing file" >&2
  exit 2
}
[[ "${#sources[@]}" -gt 0 ]] || { usage; exit 2; }
if [[ -n "$DESTINATION_NAME" && "${#sources[@]}" -ne 1 ]]; then
  echo "ERROR: --name requires exactly one source file" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$INPUTS_FILE"
set +a

DEPLOYMENT_RUN_ID="${DEPLOYMENT_RUN_ID:-}"
SECURE_EVIDENCE_DIR="${SECURE_EVIDENCE_DIR:-}"
marker="$SECURE_EVIDENCE_DIR/.agent-memory-rag-deployment-id"

[[ -d "$SECURE_EVIDENCE_DIR" && -w "$SECURE_EVIDENCE_DIR" ]] || {
  echo "ERROR: SECURE_EVIDENCE_DIR must exist and be writable" >&2
  exit 1
}
[[ -f "$marker" && "$(cat "$marker")" == "$DEPLOYMENT_RUN_ID" ]] || {
  echo "ERROR: evidence directory marker does not match DEPLOYMENT_RUN_ID" >&2
  exit 1
}

if command -v sha256sum >/dev/null; then
  SHA256=(sha256sum)
elif command -v shasum >/dev/null; then
  SHA256=(shasum -a 256)
else
  echo "ERROR: sha256sum or shasum is required" >&2
  exit 1
fi

for source_file in "${sources[@]}"; do
  [[ -f "$source_file" ]] || {
    echo "ERROR: evidence source is not a regular file: ${source_file}" >&2
    exit 1
  }

  destination_name="${DESTINATION_NAME:-$(basename "$source_file")}"
  case "$destination_name" in
    ""|"."|".."|*/*)
      echo "ERROR: invalid evidence destination name: ${destination_name}" >&2
      exit 2
      ;;
  esac
  destination="$SECURE_EVIDENCE_DIR/$destination_name"
  [[ ! -e "$destination" ]] || {
    echo "ERROR: evidence destination already exists: ${destination}" >&2
    exit 1
  }

  chmod 600 "$source_file"
  mv -n "$source_file" "$destination"
  [[ ! -e "$source_file" && -f "$destination" ]] || {
    echo "ERROR: evidence move did not complete without collision" >&2
    exit 1
  }

  (
    cd "$SECURE_EVIDENCE_DIR"
    "${SHA256[@]}" "$destination_name" >>EVIDENCE-SHA256SUMS
    chmod 600 EVIDENCE-SHA256SUMS
  )
  echo "Archived evidence: ${destination}"
done
