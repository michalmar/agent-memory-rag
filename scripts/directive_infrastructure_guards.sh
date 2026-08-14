#!/usr/bin/env bash

# Sourceable guards shared by deployment, reset, and their fixture tests.

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
candidate_started = False

while offset < len(text):
    start = text.find("{", offset)
    if start < 0:
        break
    candidate_started = True
    try:
        value, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError:
        # A producer candidate is authoritative once it starts. Never scan
        # inside malformed JSON for a nested success-looking object.
        raise SystemExit(1)
    offset = end
    if isinstance(value, dict) and value.get("success") is True:
        records.append(value)

if not candidate_started or len(records) != 1:
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

directive_assert_publication_execution_json() {
  local container_json="$1"
  local expected_image="$2"
  local expected_environment_digest="$3"
  local expected_source_digest="$4"
  local expected_validation_digest="$5"
  local expected_processing_version="$6"
  local expected_search_index="$7"
  python3 - "$expected_image" "$expected_environment_digest" \
    "$expected_source_digest" "$expected_validation_digest" \
    "$expected_processing_version" "$expected_search_index" "$container_json" <<'PY'
import json
import sys

expected_image, expected_environment_digest, expected_source_digest, \
    expected_validation_digest, expected_processing_version, \
    expected_search_index = sys.argv[1:7]
container = json.loads(sys.argv[7])
if not isinstance(container, dict):
    raise SystemExit("execution container is not an object")
command = container.get("command")
args = container.get("args")
if command != ["directive-ingest"] or args != ["run-daily"]:
    raise SystemExit("publication execution command is not pinned")
if container.get("image") != expected_image:
    raise SystemExit("publication execution image is not pinned")
env = {
    item.get("name"): item.get("value")
    for item in container.get("env", [])
    if isinstance(item, dict)
}
expected = {
    "DIRECTIVE_PROCESSING_VERSION": expected_processing_version,
    "DIRECTIVE_SEARCH_INDEX": expected_search_index,
    "DIRECTIVE_APPROVED_VALIDATION_DIGEST": expected_validation_digest,
    "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST": expected_environment_digest,
    "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST": expected_source_digest,
}
if any(env.get(key) != value for key, value in expected.items()):
    raise SystemExit("publication execution approval/configuration overrides are not exact")
PY
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
    ([$targets[].address] | unique | length == 3) and
    all($targets[]; .change.actions == ["create"])
  ' "$plan_json" >/dev/null
}

directive_validate_producer_record() {
  local record_file="$1"
  local output_file="$2"
  local schema="$3"
  local expected_environment_file="$4"
  local source_digest="$5"
  local expected_processing_version="$6"
  local expected_search_index="$7"
  python3 - "$record_file" "$output_file" "$schema" \
    "$expected_environment_file" "$source_digest" \
    "$expected_processing_version" "$expected_search_index" <<'PY'
import hashlib
import json
import pathlib
import sys

record_path, output_path, schema, environment_path, source_digest, processing_version, search_index = sys.argv[1:]
record_path = pathlib.Path(record_path)
output_path = pathlib.Path(output_path)
raw = record_path.read_bytes()
if len(raw) > 65536:
    raise SystemExit("producer record exceeds 64 KiB")
try:
    record = json.loads(raw.decode("utf-8"))
    expected_environment = json.loads(pathlib.Path(environment_path).read_text(encoding="utf-8"))
except (UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
    raise SystemExit(f"invalid producer record: {exc}")

validate_keys = {
    "record_schema", "success", "run_id", "environment",
    "processing_version", "processing_hash", "search_index",
    "source_count", "directive_count", "normalized_directive_ids",
    "directive_version_ids", "mandate_count", "mandate_user_count",
    "warnings", "warning_count", "failures", "source_inventory_digest",
    "validation_digest",
}
verify_keys = {
    "record_schema", "success", "run_id", "environment",
    "processing_version", "processing_hash", "search_index",
    "source_inventory_digest", "source_count", "directive_count",
    "normalized_directive_ids", "directive_version_ids", "warnings",
    "warning_count", "cross_store", "state_digest", "validation_digest",
    "verify_digest",
}
environment_keys = {
    "source_kind", "source_storage_account", "source_container",
    "source_prefix", "artifact_storage_account", "artifact_container",
    "cosmos_account", "cosmos_database", "catalog_container",
    "content_container", "mandate_container", "search_service",
    "search_index",
}

def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)

def _has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_has_float(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_float(v) for v in value)
    return False

if not isinstance(record, dict) or record.get("record_schema") != schema:
    raise SystemExit("unexpected producer record schema")
expected_keys = validate_keys if schema == "directive.validate.v2" else verify_keys
if set(record) != expected_keys:
    raise SystemExit("producer record has missing or unexpected top-level fields")
if record.get("success") is not True or not isinstance(record.get("run_id"), str) or not record["run_id"]:
    raise SystemExit("producer record is not a successful run record")
if any(key in record for key in (
    "execution_id", "validation_execution_id", "verification_execution_id",
    "verify_execution_id",
)):
    raise SystemExit("Azure execution IDs belong only in the infrastructure wrapper")
environment = record.get("environment")
if (
    not isinstance(environment, dict)
    or set(environment) != environment_keys
    or set(expected_environment) != environment_keys
    or any(not isinstance(value, str) for value in environment.values())
    or any(not isinstance(value, str) for value in expected_environment.values())
):
    raise SystemExit("producer environment has missing, unexpected, or invalid fields")
if environment != expected_environment:
    raise SystemExit("producer environment does not exactly match live Terraform environment")
if record.get("search_index") != search_index or environment.get("search_index") != search_index:
    raise SystemExit("producer Search index does not match the expected environment")
if record.get("processing_version") != processing_version:
    raise SystemExit("producer processing version does not match")
if not isinstance(record.get("processing_hash"), str) or not _hex64(record["processing_hash"]):
    raise SystemExit("producer processing hash is invalid")
if record.get("source_inventory_digest") != source_digest:
    raise SystemExit("producer source inventory digest does not match")
if not _int(record.get("source_count")) or record["source_count"] <= 0:
    raise SystemExit("producer source_count is invalid")
if not _int(record.get("directive_count")) or not 0 <= record["directive_count"] <= 32:
    raise SystemExit("producer directive_count is invalid")
for key in ("normalized_directive_ids", "directive_version_ids"):
    values = record.get(key)
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise SystemExit(f"producer {key} is invalid")
    if values != sorted(set(values)):
        raise SystemExit(f"producer {key} must be sorted and unique")
if len(record["normalized_directive_ids"]) != record["directive_count"]:
    raise SystemExit("normalized directive count does not match directive_count")
if len(record["directive_version_ids"]) != record["directive_count"] or len(record["directive_version_ids"]) > 32:
    raise SystemExit("directive version IDs must match directive_count and remain bounded")
warnings = record.get("warnings")
if (
    not isinstance(warnings, list)
    or len(warnings) > 100
    or not _int(record.get("warning_count"))
    or record["warning_count"] != len(warnings)
):
    raise SystemExit("producer warnings are invalid or unbounded")
if any(not isinstance(item, dict) or set(item) != {"code", "severity"} or
       not all(isinstance(item[key], str) and item[key] for key in ("code", "severity"))
       or item["severity"] not in {"warning", "error"}
       for item in warnings):
    raise SystemExit("producer warning record is invalid")
if warnings != sorted(warnings, key=lambda item: (item["code"], item["severity"])):
    raise SystemExit("producer warnings must be sorted and unique")
if len({(item["code"], item["severity"]) for item in warnings}) != len(warnings):
    raise SystemExit("producer warnings must be sorted and unique")
if _has_float(record):
    raise SystemExit("producer record must be float-free")

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

if schema == "directive.validate.v2":
    if not _int(record.get("mandate_count")) or record["mandate_count"] < 0:
        raise SystemExit("validation mandate_count is invalid")
    if not _int(record.get("mandate_user_count")) or record["mandate_user_count"] < 0:
        raise SystemExit("validation mandate_user_count is invalid")
    if not isinstance(record.get("failures"), list):
        raise SystemExit("validation failures must be an array")
    if not _hex64(record.get("validation_digest")) or digest({k: v for k, v in record.items() if k != "validation_digest"}) != record["validation_digest"]:
        raise SystemExit("validation_digest does not match the visible producer record")
else:
    cross_store = record.get("cross_store")
    expected_cross_store = {
        "catalog": {"directive_count", "version_count", "current_count", "identity_digest"},
        "content": {"item_count", "section_count", "part_count", "identity_digest"},
        "artifacts": {"object_count", "required_count", "identity_digest"},
        "source_state": {"record_count", "identity_digest"},
        "search": {"document_count", "current_document_count", "directive_count", "version_count",
                   "vector_dimensions", "vector_profile", "vectorizer", "semantic_configuration",
                   "direct_hybrid_query", "identity_digest"},
        "mandates": {"snapshot_id", "checksum", "assignment_count", "user_count", "identity_digest"},
    }
    if not isinstance(cross_store, dict) or set(cross_store) != set(expected_cross_store):
        raise SystemExit("verify cross_store keys are incomplete or unexpected")
    for name, keys in expected_cross_store.items():
        item = cross_store[name]
        if not isinstance(item, dict) or set(item) != keys:
            raise SystemExit(f"verify cross_store.{name} shape is invalid")
        for key, value in item.items():
            if key.endswith("digest") or key == "checksum":
                if not _hex64(value):
                    raise SystemExit(f"verify cross_store.{name}.{key} is invalid")
            elif key in {"snapshot_id", "vector_profile", "vectorizer", "semantic_configuration", "direct_hybrid_query"}:
                if not isinstance(value, str) or not value:
                    raise SystemExit(f"verify cross_store.{name}.{key} is invalid")
            elif not _int(value) or value < 0:
                raise SystemExit(f"verify cross_store.{name}.{key} is invalid")
    projection_keys = (
        "record_schema", "environment", "processing_version", "processing_hash",
        "search_index", "source_count", "source_inventory_digest", "directive_count",
        "normalized_directive_ids", "directive_version_ids", "validation_digest",
        "cross_store",
    )
    projection = {key: record[key] for key in projection_keys}
    if not _hex64(record.get("validation_digest")):
        raise SystemExit("verify validation_digest is invalid")
    if not _hex64(record.get("state_digest")) or digest(projection) != record["state_digest"]:
        raise SystemExit("state_digest does not match the stable producer projection")
    if not _hex64(record.get("verify_digest")) or digest({k: v for k, v in record.items() if k != "verify_digest"}) != record["verify_digest"]:
        raise SystemExit("verify_digest does not match the visible producer record")

serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
if len(serialized.encode("utf-8")) > 65536:
    raise SystemExit("producer record exceeds 64 KiB")
pathlib.Path(output_path).write_text(serialized + "\n", encoding="utf-8")
PY
}
