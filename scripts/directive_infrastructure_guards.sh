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
      (if $expected_argument == "verify-deep"
       then ["verify", "--deep-source-audit"]
       else [$expected_argument]
       end) as $expected_args |
      (.command | if type == "array" then . else [.] end) == ["directive-ingest"] and
      (.args | if type == "array" then . else [.] end) == $expected_args
    ' <<<"$container_json" >/dev/null
}

directive_execution_approval_kind() {
  case "$1" in
    run-daily)
      printf '%s\n' publication
      ;;
    verify | verify-deep)
      printf '%s\n' verification
      ;;
    *)
      printf '%s\n' none
      ;;
  esac
}

directive_assert_execution_override_json() {
  local container_json="$1"
  local expected_argument="$2"
  local expected_image="$3"
  local expected_cpu="$4"
  local expected_memory="$5"
  python3 - "$expected_argument" "$expected_image" "$expected_cpu" \
    "$expected_memory" "$container_json" <<'PY'
import json
import sys

expected_argument, expected_image, expected_cpu, expected_memory, raw = sys.argv[1:]
container = json.loads(raw)
expected_args = (
    ["verify", "--deep-source-audit"]
    if expected_argument == "verify-deep"
    else [expected_argument]
)
if not isinstance(container, dict):
    raise SystemExit("execution container is not an object")
if container.get("name") != "directive-ingestion":
    raise SystemExit("execution container name is not pinned")
if container.get("image") != expected_image:
    raise SystemExit("execution image is not pinned")
if container.get("command") != ["directive-ingest"] or container.get("args") != expected_args:
    raise SystemExit("execution command is not pinned")
resources = container.get("resources")
if not isinstance(resources, dict):
    raise SystemExit("execution resources are missing")
try:
    cpu_matches = float(resources.get("cpu")) == float(expected_cpu)
except (TypeError, ValueError):
    cpu_matches = False
if not cpu_matches or resources.get("memory") != expected_memory:
    raise SystemExit("execution resources are not pinned")
PY
}

directive_render_execution_env_vars() {
  local raw_env_json="$1"
  local expected_argument="$2"
  local expected_processing_version="$3"
  local expected_search_index="$4"
  local expected_validation_digest="${5:-}"
  local expected_environment_digest="${6:-}"
  local expected_source_digest="${7:-}"
  local expected_validation_evidence_digest="${8:-}"
  python3 - "$raw_env_json" "$expected_argument" \
    "$expected_processing_version" "$expected_search_index" \
    "$expected_validation_digest" "$expected_environment_digest" \
    "$expected_source_digest" "$expected_validation_evidence_digest" <<'PY'
import json
import sys

raw, mode, processing_version, search_index, validation_digest, \
    environment_digest, source_digest, validation_evidence_digest = sys.argv[1:]
try:
    env = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"job environment is invalid JSON: {exc}")
if not isinstance(env, list) or not env:
    raise SystemExit("job environment is missing")
approval_names = {
    "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
    "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
    "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
    "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST",
}
values = {}
for item in env:
    if not isinstance(item, dict):
        raise SystemExit("job environment contains a non-object entry")
    name = item.get("name")
    value = item.get("value")
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(value, str)
        or any(character in value for character in "\t\r\n")
        or name in values
        or "secretRef" in item
    ):
        raise SystemExit("job environment contains an unsafe or duplicate value")
    values[name] = value
if (
    values.get("DIRECTIVE_PROCESSING_VERSION") != processing_version
    or values.get("DIRECTIVE_SEARCH_INDEX") != search_index
):
    raise SystemExit("job environment is not the expected target configuration")
for name in approval_names:
    values.pop(name, None)
if mode == "run-daily":
    expected_approvals = {
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST": validation_digest,
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST": environment_digest,
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST": source_digest,
        "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST": validation_evidence_digest,
    }
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in expected_approvals.values()
    ):
        raise SystemExit("execution approval digest is invalid")
    values.update(expected_approvals)
elif mode in {"verify", "verify-deep"}:
    expected_approvals = {
        "DIRECTIVE_APPROVED_VALIDATION_DIGEST": validation_digest,
        "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST": environment_digest,
        "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST": source_digest,
    }
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in expected_approvals.values()
    ):
        raise SystemExit("execution approval digest is invalid")
    if validation_evidence_digest:
        raise SystemExit("verify execution must not receive a validation evidence digest override")
    values.update(expected_approvals)
elif any((validation_digest, environment_digest, source_digest)):
    raise SystemExit("unapproved execution received approval values")
elif validation_evidence_digest:
    raise SystemExit("unapproved execution received a validation evidence digest")
for name, value in sorted(values.items()):
    print(f"{name}\t{value}")
PY
}

directive_assert_job_start_override_args() {
  local expected_argument="$1"
  local expected_image="$2"
  local expected_cpu="$3"
  local expected_memory="$4"
  shift 4
  python3 - "$expected_argument" "$expected_image" "$expected_cpu" \
    "$expected_memory" "$@" <<'PY'
import sys

expected_argument, expected_image, expected_cpu, expected_memory, *args = sys.argv[1:]
expected_args = (
    ["verify", "--deep-source-audit"]
    if expected_argument == "verify-deep"
    else [expected_argument]
)
required = {
    "--container-name": "directive-ingestion",
    "--image": expected_image,
    "--cpu": expected_cpu,
    "--memory": expected_memory,
    "--command": "directive-ingest",
}
for flag, expected in required.items():
    try:
        position = args.index(flag)
    except ValueError:
        raise SystemExit(f"job start is missing {flag}")
    if position + 1 >= len(args) or args[position + 1] != expected:
        raise SystemExit(f"job start has an invalid {flag}")
try:
    args_start = args.index("--args") + 1
    args_end = args.index("--env-vars", args_start)
except ValueError:
    raise SystemExit("job start has an invalid --args section")
if args[args_start:args_end] != expected_args:
    raise SystemExit("job start has invalid execution arguments")
if args.count("--env-vars") != 1:
    raise SystemExit("job start must carry exactly one complete environment override")
env_start = args.index("--env-vars") + 1
try:
    env_end = args.index("--query", env_start)
except ValueError:
    env_end = len(args)
env_vars = args[env_start:env_end]
if not env_vars or any("=" not in item for item in env_vars):
    raise SystemExit("job start environment override is incomplete")
names = [item.split("=", 1)[0] for item in env_vars]
if len(names) != len(set(names)):
    raise SystemExit("job start environment override has duplicate names")
PY
}

directive_build_job_start_override_args() {
  local expected_argument="$1"
  local job_name="$2"
  local resource_group="$3"
  local container_name="$4"
  local image="$5"
  local cpu="$6"
  local memory="$7"
  shift 7
  [[ "$container_name" == directive-ingestion ]] || return 1
  [[ "$expected_argument" != verify-deep ]] || return 1
  [[ "$#" -gt 0 ]] || return 1
  local execution_args=("$expected_argument")
  DIRECTIVE_JOB_START_ARGS=(
    --name "$job_name"
    --resource-group "$resource_group"
    --container-name "$container_name"
    --image "$image"
    --cpu "$cpu"
    --memory "$memory"
    --command directive-ingest
    --args "${execution_args[@]}"
    --env-vars
    "$@"
    --query name
    --output tsv
  )
  directive_assert_job_start_override_args \
    "$expected_argument" "$image" "$cpu" "$memory" \
    "${DIRECTIVE_JOB_START_ARGS[@]}"
}

directive_render_job_execution_override_json() {
  local expected_argument="$1"
  local container_name="$2"
  local image="$3"
  local cpu="$4"
  local memory="$5"
  shift 5
  python3 - "$expected_argument" "$container_name" "$image" "$cpu" \
    "$memory" "$@" <<'PY'
import json
import re
import sys

mode, container_name, image, raw_cpu, memory, *raw_env = sys.argv[1:]
if mode != "verify-deep":
    raise SystemExit("execution-template override is reserved for deep verification")
if container_name != "directive-ingestion":
    raise SystemExit("execution container name is not pinned")
if not image or not memory:
    raise SystemExit("execution image or memory is missing")
try:
    cpu = float(raw_cpu)
except ValueError as exc:
    raise SystemExit("execution CPU is invalid") from exc
if cpu <= 0:
    raise SystemExit("execution CPU must be positive")
if not raw_env:
    raise SystemExit("execution environment override is missing")

env = []
names = set()
for entry in raw_env:
    name, separator, value = entry.partition("=")
    if (
        not separator
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        or name in names
        or any(character in value for character in "\r\n")
    ):
        raise SystemExit("execution environment override is malformed")
    names.add(name)
    env.append({"name": name, "value": value})

payload = {
    "containers": [
        {
            "name": container_name,
            "image": image,
            "command": ["directive-ingest"],
            "args": ["verify", "--deep-source-audit"],
            "env": env,
            "resources": {"cpu": cpu, "memory": memory},
        }
    ]
}
print(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
PY
}

directive_build_job_start_yaml_args() {
  local job_name="$1"
  local resource_group="$2"
  local override_file="$3"
  [[ -n "$job_name" && -n "$resource_group" && -f "$override_file" ]] || return 1
  DIRECTIVE_JOB_START_ARGS=(
    --name "$job_name"
    --resource-group "$resource_group"
    --yaml "$override_file"
    --query name
    --output tsv
  )
  [[ "${DIRECTIVE_JOB_START_ARGS[*]}" == \
    "--name $job_name --resource-group $resource_group --yaml $override_file --query name --output tsv" ]]
}

directive_assert_approved_execution_json() {
  local container_json="$1"
  local expected_argument="$2"
  local expected_image="$3"
  local expected_environment_digest="$4"
  local expected_source_digest="$5"
  local expected_validation_digest="$6"
  local expected_processing_version="$7"
  local expected_search_index="$8"
  local expected_validation_evidence_digest="${9:-}"
  directive_assert_execution_override_json \
    "$container_json" "$expected_argument" "$expected_image" 1 2Gi || return
  python3 - "$expected_image" "$expected_environment_digest" \
    "$expected_source_digest" "$expected_validation_digest" \
    "$expected_processing_version" "$expected_search_index" \
    "$expected_argument" "$expected_validation_evidence_digest" \
    "$container_json" <<'PY'
import json
import sys

expected_image, expected_environment_digest, expected_source_digest, \
    expected_validation_digest, expected_processing_version, \
    expected_search_index, expected_argument, \
    expected_validation_evidence_digest = sys.argv[1:9]
container = json.loads(sys.argv[9])
expected_args = (
    ["verify", "--deep-source-audit"]
    if expected_argument == "verify-deep"
    else [expected_argument]
)
if not isinstance(container, dict):
    raise SystemExit("execution container is not an object")
command = container.get("command")
args = container.get("args")
if command != ["directive-ingest"] or args != expected_args:
    raise SystemExit("approved execution command is not pinned")
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
if expected_argument == "run-daily":
    expected["DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST"] = (
        expected_validation_evidence_digest
    )
elif env.get("DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST") not in {None, ""}:
    raise SystemExit(
        "verification execution must not carry a validation evidence digest override"
    )
if any(env.get(key) != value for key, value in expected.items()):
    raise SystemExit("publication execution approval/configuration overrides are not exact")
PY
}

directive_assert_unapproved_execution_json() {
  local container_json="$1"
  local expected_argument="$2"
  python3 - "$expected_argument" "$container_json" <<'PY'
import json
import sys

expected_argument, raw = sys.argv[1:]
container = json.loads(raw)
if not isinstance(container, dict):
    raise SystemExit("execution container is not an object")
if container.get("command") != ["directive-ingest"] or container.get("args") != [expected_argument]:
    raise SystemExit("nonpublication execution command is not pinned")
names = {
    item.get("name")
    for item in container.get("env", [])
    if isinstance(item, dict)
}
if names & {
    "DIRECTIVE_APPROVED_VALIDATION_DIGEST",
    "DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST",
    "DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST",
  "DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST",
}:
  raise SystemExit("approval overrides are not permitted for this execution")
PY
}

directive_select_new_approved_execution_names() {
  local before_json="$1"
  local current_json="$2"
  local expected_argument="$3"
  local expected_image="$4"
  local expected_environment_digest="$5"
  local expected_source_digest="$6"
  local expected_validation_digest="$7"
  local expected_processing_version="$8"
  local expected_search_index="$9"
  local expected_validation_evidence_digest="${10:-}"
  local before_file current_file status
  before_file="$(mktemp)"
  current_file="$(mktemp)"
  printf '%s' "$before_json" >"$before_file"
  printf '%s' "$current_json" >"$current_file"
  if python3 - "$before_file" "$current_file" "$expected_argument" \
    "$expected_image" "$expected_environment_digest" "$expected_source_digest" \
    "$expected_validation_digest" "$expected_processing_version" \
    "$expected_search_index" "$expected_validation_evidence_digest" <<'PY'
import json
import pathlib
import sys

before = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
current = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
expected_argument, expected_image, expected_environment_digest, \
    expected_source_digest, expected_validation_digest, \
    expected_processing_version, expected_search_index, \
    expected_validation_evidence_digest = sys.argv[3:]
before_names = {
    item.get("name") for item in before if isinstance(item, dict)
}
matches = []
for item in current:
    if not isinstance(item, dict) or item.get("name") in before_names:
        continue
    template = item.get("properties", {}).get("template", {})
    for container in template.get("containers", []):
        if not isinstance(container, dict) or container.get("name") != "directive-ingestion":
            continue
        env = {
            value.get("name"): value.get("value")
            for value in container.get("env", [])
            if isinstance(value, dict)
        }
        if (
            container.get("image") == expected_image
            and container.get("command") == ["directive-ingest"]
            and container.get("args") == [expected_argument]
            and container.get("name") == "directive-ingestion"
            and isinstance(container.get("resources"), dict)
            and float(container["resources"].get("cpu", 0)) == 1.0
            and container["resources"].get("memory") == "2Gi"
            and env.get("DIRECTIVE_APPROVED_ENVIRONMENT_DIGEST") == expected_environment_digest
            and env.get("DIRECTIVE_APPROVED_SOURCE_INVENTORY_DIGEST") == expected_source_digest
            and env.get("DIRECTIVE_APPROVED_VALIDATION_DIGEST") == expected_validation_digest
            and (
                (
                    expected_argument == "run-daily"
                    and env.get("DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST")
                    == expected_validation_evidence_digest
                )
                or (
                    expected_argument != "run-daily"
                    and env.get("DIRECTIVE_APPROVED_VALIDATION_EVIDENCE_DIGEST")
                    in {None, ""}
                )
            )
            and env.get("DIRECTIVE_PROCESSING_VERSION") == expected_processing_version
            and env.get("DIRECTIVE_SEARCH_INDEX") == expected_search_index
        ):
            name = item.get("name")
            if isinstance(name, str) and name:
                matches.append(name)
for name in sorted(set(matches)):
    print(name)
if len(set(matches)) != 1:
    raise SystemExit(2)
PY
  then
    status=0
  else
    status=$?
  fi
  rm -f "$before_file" "$current_file"
  return "$status"
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
    all(
      $targets[];
      (.change.actions == ["create"]) or
      (
        .change.actions == ["delete", "create"] and
        .action_reason == "replace_by_request"
      )
    )
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
    "record_schema", "success", "run_id", "environment", "environment_digest",
    "processing_version", "processing_hash", "search_index",
    "source_count", "directive_count", "normalized_directive_ids",
    "directive_version_ids", "mandate_count", "mandate_user_count",
    "mandate_checksum", "warnings", "warning_count", "failures",
    "source_inventory_digest",
    "validation_evidence_digest",
    "validation_digest",
}
verify_keys = {
    "record_schema", "success", "run_id", "environment", "environment_digest",
    "processing_version", "processing_hash", "search_index",
    "source_inventory_digest", "source_count", "directive_count",
    "normalized_directive_ids", "directive_version_ids", "warnings",
    "warning_count", "mandate_checksum", "cross_store", "state_digest",
    "validation_digest",
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

def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()

if not isinstance(record, dict) or record.get("record_schema") != schema:
    raise SystemExit("unexpected producer record schema")
expected_keys = validate_keys if schema == "directive.validate.v3" else verify_keys
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
if (
    not _hex64(record.get("environment_digest"))
    or digest(environment) != record["environment_digest"]
    or record["environment_digest"] != digest(expected_environment)
):
    raise SystemExit("producer environment_digest does not match the canonical environment")
if record.get("processing_version") != processing_version:
    raise SystemExit("producer processing version does not match")
if not isinstance(record.get("processing_hash"), str) or not _hex64(record["processing_hash"]):
    raise SystemExit("producer processing hash is invalid")
if source_digest and record.get("source_inventory_digest") != source_digest:
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

if schema == "directive.validate.v3":
    if not _int(record.get("mandate_count")) or record["mandate_count"] < 0:
        raise SystemExit("validation mandate_count is invalid")
    if not _int(record.get("mandate_user_count")) or record["mandate_user_count"] < 0:
        raise SystemExit("validation mandate_user_count is invalid")
    if not isinstance(record.get("failures"), list):
        raise SystemExit("validation failures must be an array")
    if not _hex64(record.get("mandate_checksum")):
        raise SystemExit("validation mandate_checksum is invalid")
    if not _hex64(record.get("validation_evidence_digest")):
        raise SystemExit("validation validation_evidence_digest is invalid")
    if not _hex64(record.get("validation_digest")) or digest({
        key: record[key]
        for key in (
            "record_schema",
            "success",
            "environment",
            "environment_digest",
            "processing_version",
            "processing_hash",
            "search_index",
            "source_count",
            "directive_count",
            "normalized_directive_ids",
            "directive_version_ids",
            "mandate_count",
            "mandate_user_count",
            "mandate_checksum",
            "warnings",
            "warning_count",
            "failures",
            "source_inventory_digest",
            "validation_evidence_digest",
        )
    }) != record["validation_digest"]:
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
    if (
        not _hex64(record.get("mandate_checksum"))
        or record["mandate_checksum"] != cross_store["mandates"]["checksum"]
    ):
        raise SystemExit("verify mandate_checksum does not match cross_store.mandates.checksum")
    projection_keys = (
        "record_schema", "environment", "environment_digest",
        "processing_version", "processing_hash",
        "search_index", "source_count", "source_inventory_digest", "directive_count",
        "normalized_directive_ids", "directive_version_ids", "mandate_checksum",
        "validation_digest",
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
