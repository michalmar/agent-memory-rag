#!/usr/bin/env bash
# Safely grant and later remove the Azure roles needed by a single Global
# Administrator who also performs the complete deployment.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365-global-admin}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

COMMAND=""
INPUTS_FILE=""
STATE_FILE="./global-admin-access-state.json"

usage() {
  cat <<'USAGE'
Usage:
  global_admin_access.sh context --inputs <file>
  global_admin_access.sh grant   --inputs <file> [--state <file>]
  global_admin_access.sh status  --inputs <file> [--state <file>]
  global_admin_access.sh cleanup --inputs <file> [--state <file>]

The input file must define TARGET_TENANT_ID and TARGET_SUBSCRIPTION_ID.
DEPLOYER_OBJECT_ID is optional; when set, it must match the signed-in user.

Before "grant", the signed-in Global Administrator must temporarily enable
"Access management for Azure resources" and refresh the Azure CLI login.
The script creates direct Contributor and User Access Administrator assignments
at the target subscription, recording only assignments it created.

"cleanup" removes Contributor first and User Access Administrator last. It
never removes role assignments that existed before "grant".
USAGE
}

if [[ $# -gt 0 ]]; then
  COMMAND="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inputs)
      [[ $# -ge 2 ]] || { echo "ERROR: --inputs requires a file" >&2; exit 2; }
      INPUTS_FILE="$2"
      shift 2
      ;;
    --state)
      [[ $# -ge 2 ]] || { echo "ERROR: --state requires a file" >&2; exit 2; }
      STATE_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$COMMAND" in
  context|grant|status|cleanup) ;;
  -h|--help|"")
    usage
    exit 0
    ;;
  *)
    echo "ERROR: unknown command: $COMMAND" >&2
    usage >&2
    exit 2
    ;;
esac

[[ -n "$INPUTS_FILE" && -f "$INPUTS_FILE" ]] || {
  echo "ERROR: --inputs must reference an existing file" >&2
  exit 2
}

command -v az >/dev/null || { echo "ERROR: Azure CLI is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "ERROR: jq is required" >&2; exit 1; }
if [[ "$COMMAND" == grant ]]; then
  command -v uuidgen >/dev/null || {
    echo "ERROR: uuidgen is required for deterministic role assignment IDs" >&2
    exit 1
  }
fi

set -a
# shellcheck disable=SC1090
source "$INPUTS_FILE"
set +a

TARGET_TENANT_ID="${TARGET_TENANT_ID:-}"
TARGET_SUBSCRIPTION_ID="${TARGET_SUBSCRIPTION_ID:-}"
DEPLOYER_OBJECT_ID="${DEPLOYER_OBJECT_ID:-}"

guid_pattern='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
[[ "$TARGET_TENANT_ID" =~ $guid_pattern ]] || {
  echo "ERROR: TARGET_TENANT_ID must be a GUID" >&2
  exit 2
}
[[ "$TARGET_SUBSCRIPTION_ID" =~ $guid_pattern ]] || {
  echo "ERROR: TARGET_SUBSCRIPTION_ID must be a GUID" >&2
  exit 2
}
if [[ -n "$DEPLOYER_OBJECT_ID" && ! "$DEPLOYER_OBJECT_ID" =~ $guid_pattern ]]; then
  echo "ERROR: DEPLOYER_OBJECT_ID must be empty or a GUID" >&2
  exit 2
fi

subscription_scope="/subscriptions/${TARGET_SUBSCRIPTION_ID}"

lowercase() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

assert_context() {
  local actual_tenant actual_subscription signed_in_id
  actual_tenant="$(az account show --query tenantId --output tsv)"
  actual_subscription="$(az account show --query id --output tsv)"
  signed_in_id="$(az ad signed-in-user show --query id --output tsv)"

  [[ "$(lowercase "$actual_tenant")" == "$(lowercase "$TARGET_TENANT_ID")" ]] || {
    echo "ERROR: signed in to tenant ${actual_tenant}, expected ${TARGET_TENANT_ID}" >&2
    exit 1
  }
  [[ "$(lowercase "$actual_subscription")" == "$(lowercase "$TARGET_SUBSCRIPTION_ID")" ]] || {
    echo "ERROR: selected subscription ${actual_subscription}, expected ${TARGET_SUBSCRIPTION_ID}" >&2
    exit 1
  }
  if [[ -n "$DEPLOYER_OBJECT_ID" ]] \
    && [[ "$(lowercase "$signed_in_id")" != "$(lowercase "$DEPLOYER_OBJECT_ID")" ]]; then
    echo "ERROR: signed-in object ${signed_in_id} does not match DEPLOYER_OBJECT_ID" >&2
    exit 1
  fi

  DEPLOYER_OBJECT_ID="$signed_in_id"
  export DEPLOYER_OBJECT_ID
}

direct_assignment_id() {
  local role_name="$1"
  local assignments assignment_id
  if ! assignments="$(
    az role assignment list \
      --assignee-object-id "$DEPLOYER_OBJECT_ID" \
      --scope "$subscription_scope" \
      --all \
      --output json
  )"; then
    echo "ERROR: failed to query existing ${role_name} assignments" >&2
    return 1
  fi
  if ! assignment_id="$(
    jq -r \
      --arg role "$role_name" \
      --arg scope "$(lowercase "$subscription_scope")" \
      '[
        .[]
        | select(.roleDefinitionName == $role)
        | select((.scope | ascii_downcase) == $scope)
      ][0].id // empty' <<<"$assignments"
  )"; then
    echo "ERROR: failed to parse existing ${role_name} assignments" >&2
    return 1
  fi
  printf '%s\n' "$assignment_id"
}

update_state_status() {
  local status="$1"
  local state_tmp="${STATE_FILE}.tmp"
  if ! jq --arg status "$status" '.status = $status' \
    "$STATE_FILE" >"$state_tmp"; then
    rm -f "$state_tmp"
    echo "ERROR: failed to update access state status" >&2
    return 1
  fi
  mv "$state_tmp" "$STATE_FILE"
}

record_role_state() {
  local role_key="$1"
  local assignment_id="$2"
  local created_by_script="$3"
  local creation_pending="$4"
  local state_tmp="${STATE_FILE}.tmp"
  if ! jq \
    --arg role_key "$role_key" \
    --arg assignment_id "$assignment_id" \
    --argjson created_by_script "$created_by_script" \
    --argjson creation_pending "$creation_pending" '
      .roles[$role_key] = {
        assignment_id: $assignment_id,
        created_by_script: $created_by_script,
        creation_pending: $creation_pending
      }
    ' "$STATE_FILE" >"$state_tmp"; then
    rm -f "$state_tmp"
    echo "ERROR: failed to record ${role_key} assignment state" >&2
    return 1
  fi
  mv "$state_tmp" "$STATE_FILE"
}

initialize_grant_state() {
  local state_directory state_tmp
  state_directory="$(dirname "$STATE_FILE")"
  [[ -d "$state_directory" && -w "$state_directory" ]] || {
    echo "ERROR: state directory must exist and be writable: ${state_directory}" >&2
    return 1
  }

  umask 077
  state_tmp="${STATE_FILE}.tmp"
  rm -f "$state_tmp"
  if ! jq -n \
    --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg tenant_id "$TARGET_TENANT_ID" \
    --arg subscription_id "$TARGET_SUBSCRIPTION_ID" \
    --arg principal_id "$DEPLOYER_OBJECT_ID" \
    --arg scope "$subscription_scope" '
      {
        status: "granting",
        created_at: $created_at,
        tenant_id: $tenant_id,
        subscription_id: $subscription_id,
        principal_id: $principal_id,
        scope: $scope,
        roles: {}
      }
    ' >"$state_tmp"; then
    rm -f "$state_tmp"
    echo "ERROR: failed to create access state" >&2
    return 1
  fi
  mv "$state_tmp" "$STATE_FILE"
}

ensure_direct_assignment() {
  local role_name="$1"
  local role_key="$2"
  local existing_id assignment_name expected_id actual_id

  if ! existing_id="$(direct_assignment_id "$role_name")"; then
    return 1
  fi
  if [[ -n "$existing_id" ]]; then
    record_role_state "$role_key" "$existing_id" false false || return 1
    echo "Existing direct ${role_name} assignment: ${existing_id}"
    return
  fi

  assignment_name="$(uuidgen | tr '[:upper:]' '[:lower:]')"
  expected_id="${subscription_scope}/providers/Microsoft.Authorization/roleAssignments/${assignment_name}"
  record_role_state "$role_key" "$expected_id" true true || return 1

  if ! actual_id="$(
    az role assignment create \
      --name "$assignment_name" \
      --assignee-object-id "$DEPLOYER_OBJECT_ID" \
      --assignee-principal-type User \
      --role "$role_name" \
      --scope "$subscription_scope" \
      --query id \
      --output tsv
  )"; then
    echo "ERROR: failed to create ${role_name}; ownership state was retained" >&2
    return 1
  fi
  [[ -n "$actual_id" ]] || {
    echo "ERROR: Azure did not return the ${role_name} assignment ID" >&2
    return 1
  }
  [[ "$(lowercase "$actual_id")" == "$(lowercase "$expected_id")" ]] || {
    echo "ERROR: Azure returned an unexpected ${role_name} assignment ID" >&2
    return 1
  }

  record_role_state "$role_key" "$actual_id" true false || return 1
  echo "Created direct ${role_name} assignment: ${actual_id}"
}

assignment_status() {
  local assignment_id="$1"
  local role_name="$2"
  local assignments is_present
  if ! assignments="$(
    az role assignment list \
      --assignee-object-id "$DEPLOYER_OBJECT_ID" \
      --scope "$subscription_scope" \
      --all \
      --output json
  )"; then
    echo "ERROR: failed to query ${role_name} during cleanup" >&2
    return 1
  fi
  if ! is_present="$(
    jq -r \
      --arg id "$(lowercase "$assignment_id")" \
      --arg role "$role_name" \
      --arg scope "$(lowercase "$subscription_scope")" '
        any(
          .[];
          (.id | ascii_downcase) == $id
          and .roleDefinitionName == $role
          and (.scope | ascii_downcase) == $scope
        )
      ' <<<"$assignments"
  )"; then
    echo "ERROR: failed to parse ${role_name} assignments during cleanup" >&2
    return 1
  fi
  case "$is_present" in
    true) echo present ;;
    false) echo absent ;;
    *)
      echo "ERROR: invalid ${role_name} assignment query result" >&2
      return 1
      ;;
  esac
}

wait_for_assignment_absence() {
  local assignment_id="$1"
  local role_name="$2"
  local attempt status
  for ((attempt = 1; attempt <= 12; attempt++)); do
    if ! status="$(assignment_status "$assignment_id" "$role_name")"; then
      return 1
    fi
    if [[ "$status" == absent ]]; then
      return
    fi
    sleep 5
  done
  echo "ERROR: ${role_name} is still present after deletion" >&2
  return 1
}

show_context() {
  az account show \
    --query '{tenantId:tenantId,subscriptionId:id,subscription:name,state:state,user:user.name}' \
    --output table
  printf 'Signed-in object ID: %s\n' "$DEPLOYER_OBJECT_ID"
  printf 'Deployment scope:    %s\n' "$subscription_scope"
}

grant_access() {
  local existing_status
  if [[ -e "$STATE_FILE" ]]; then
    existing_status="$(jq -r '.status // "unknown"' "$STATE_FILE" 2>/dev/null || echo invalid)"
    echo "ERROR: state file already exists with status ${existing_status}: ${STATE_FILE}" >&2
    if [[ "$existing_status" == cleaned ]]; then
      echo "Move the cleaned state to secure evidence before starting a new grant." >&2
    else
      echo "Run status or cleanup; do not overwrite the role ownership record." >&2
    fi
    exit 1
  fi

  initialize_grant_state
  if ! ensure_direct_assignment "Contributor" "contributor"; then
    update_state_status "grant_failed" || true
    echo "ERROR: Contributor setup failed; keep root elevation active and run cleanup using ${STATE_FILE}" >&2
    return 1
  fi
  if ! ensure_direct_assignment \
    "User Access Administrator" \
    "user_access_administrator"; then
    update_state_status "grant_failed" || true
    echo "ERROR: User Access Administrator setup failed; keep root elevation active and run cleanup using ${STATE_FILE}" >&2
    return 1
  fi
  update_state_status "active"

  echo "Access state recorded in ${STATE_FILE}."
  echo "Set 'Access management for Azure resources' back to No now, then refresh login."
}

show_status() {
  show_context
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "No access state file: ${STATE_FILE}"
    return
  fi
  jq . "$STATE_FILE"
  az role assignment list \
    --assignee-object-id "$DEPLOYER_OBJECT_ID" \
    --scope "$subscription_scope" \
    --all \
    --query "[?roleDefinitionName=='Contributor' || roleDefinitionName=='User Access Administrator'].{role:roleDefinitionName,scope:scope,id:id}" \
    --output table
}

cleanup_role() {
  local role_key="$1"
  local role_name="$2"
  local verify_after_delete="$3"
  local assignment_id created_by_script status

  if ! assignment_id="$(
    jq -r --arg role_key "$role_key" \
      '.roles[$role_key].assignment_id // empty' \
      "$STATE_FILE"
  )"; then
    echo "ERROR: failed to read ${role_name} assignment ID from state" >&2
    return 1
  fi
  if ! created_by_script="$(
    jq -r --arg role_key "$role_key" \
      '.roles[$role_key].created_by_script // false' \
      "$STATE_FILE"
  )"; then
    echo "ERROR: failed to read ${role_name} ownership from state" >&2
    return 1
  fi

  case "$created_by_script" in
    false)
      echo "Leaving pre-existing ${role_name} assignment unchanged."
      return
      ;;
    true) ;;
    *)
      echo "ERROR: invalid ${role_name} ownership state" >&2
      return 1
      ;;
  esac
  [[ -n "$assignment_id" ]] || {
    echo "ERROR: script-created ${role_name} has no recorded assignment ID" >&2
    return 1
  }

  if ! status="$(assignment_status "$assignment_id" "$role_name")"; then
    return 1
  fi
  if [[ "$status" == absent ]]; then
    echo "Script-created ${role_name} assignment is already absent."
    return
  fi

  echo "Removing script-created ${role_name} assignment."
  if ! az role assignment delete --ids "$assignment_id" --output none; then
    echo "ERROR: failed to remove ${role_name}" >&2
    return 1
  fi

  if [[ "$verify_after_delete" == true ]]; then
    wait_for_assignment_absence "$assignment_id" "$role_name"
  else
    echo "${role_name} deletion was accepted by Azure; no post-delete query is attempted after removing the final authorization role."
  fi
}

cleanup_access() {
  local state_tenant state_subscription state_principal
  local state_tmp

  [[ -f "$STATE_FILE" ]] || {
    echo "ERROR: cleanup requires the original state file: ${STATE_FILE}" >&2
    exit 1
  }

  state_tenant="$(jq -r .tenant_id "$STATE_FILE")"
  state_subscription="$(jq -r .subscription_id "$STATE_FILE")"
  state_principal="$(jq -r .principal_id "$STATE_FILE")"
  [[ "$(lowercase "$state_tenant")" == "$(lowercase "$TARGET_TENANT_ID")" ]] || {
    echo "ERROR: state tenant does not match inputs" >&2
    exit 1
  }
  [[ "$(lowercase "$state_subscription")" == "$(lowercase "$TARGET_SUBSCRIPTION_ID")" ]] || {
    echo "ERROR: state subscription does not match inputs" >&2
    exit 1
  }
  [[ "$(lowercase "$state_principal")" == "$(lowercase "$DEPLOYER_OBJECT_ID")" ]] || {
    echo "ERROR: state principal does not match the signed-in user" >&2
    exit 1
  }

  cleanup_role "contributor" "Contributor" true
  cleanup_role \
    "user_access_administrator" \
    "User Access Administrator" \
    false

  state_tmp="${STATE_FILE}.tmp"
  jq \
    --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '.status = "cleaned" | .cleanup_completed_at = $completed_at' \
    "$STATE_FILE" >"$state_tmp"
  mv "$state_tmp" "$STATE_FILE"
  echo "Temporary subscription access cleanup completed."
}

assert_context

case "$COMMAND" in
  context) show_context ;;
  grant) grant_access ;;
  status) show_status ;;
  cleanup) cleanup_access ;;
esac
