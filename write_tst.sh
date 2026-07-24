#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")" && pwd)"
target="$repo_root/tst.txt"
printf 'A' > "$target"
sleep 30
printf 'B' >> "$target"
