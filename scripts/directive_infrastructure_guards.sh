#!/usr/bin/env bash

directive_extract_producer_record() {
  local raw_file="$1"
  local output_file="$2"
  : >"$output_file"
  python3 - "$raw_file" "$output_file" <<'PY'
import json
import pathlib
import sys

raw_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
text = raw_path.read_text(encoding="utf-8", errors="replace")
decoder = json.JSONDecoder()
records = []
offset = 0

while True:
    start = text.find("{", offset)
    if start < 0:
        break
    try:
        value, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError:
        raise SystemExit(1)
    offset = end
    if isinstance(value, dict) and value.get("success") is True:
        records.append(value)

if len(records) != 1:
    raise SystemExit(1)

serialized = json.dumps(
    records[0], ensure_ascii=False, separators=(",", ":"), sort_keys=True
)
if len(serialized.encode("utf-8")) > 65536:
    raise SystemExit(1)
output_path.write_text(serialized + "\n", encoding="utf-8")
PY
}

directive_assert_execution_mode_json() {
  local container_json="$1"
  local expected_argument="$2"
  jq -e \
    --arg expected_argument "$expected_argument" \
    '
      (.command | if type == "array" then . else [.] end) == ["directive-ingest"] and
      (.args | if type == "array" then . else [.] end) == [$expected_argument]
    ' <<<"$container_json" >/dev/null
}

directive_assert_cosmos_recreation_plan() {
  local plan_json="$1"
  jq -e '
    [
      "azurerm_cosmosdb_sql_container.directive_catalog",
      "azurerm_cosmosdb_sql_container.directive_content",
      "azurerm_cosmosdb_sql_container.directive_mandates"
    ] as $allowed |
    (.resource_changes // []) as $changes |
    ($changes | map(select(.change.actions != ["no-op"]))) as $actionable |
    ($actionable | map(select(.address as $address |
      any($allowed[]; . == $address)))) as $targets |
    ($actionable | map(select(.address as $address |
      all($allowed[]; . != $address)))) as $unexpected |
    ($targets | length == 3) and
    ($unexpected | length == 0) and
    all($targets[]; .change.actions == ["create"])
  ' "$plan_json" >/dev/null
}
