#!/usr/bin/env bash
# Build one independently versioned Hosted MAF image in ACR.
# Usage: build_hosted_agent_image.sh --agent support|directive --tag <new-tag>
#        [--configure-azd|--validate-tag-only]
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
AGENT_KIND="support"
IMAGE_TAG=""
CONFIGURE_AZD=false
VALIDATE_TAG_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent|--tag)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "ERROR: $1 requires a value" >&2
        exit 2
      fi
      if [[ "$1" == "--agent" ]]; then
        AGENT_KIND="$2"
      else
        IMAGE_TAG="$2"
      fi
      shift 2
      ;;
    --configure-azd) CONFIGURE_AZD=true; shift;;
    --validate-tag-only) VALIDATE_TAG_ONLY=true; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

if [[ -z "$IMAGE_TAG" ]]; then
  echo "ERROR: --tag is required; use a new immutable tag for every build" >&2
  exit 2
fi
if [[ "$CONFIGURE_AZD" == true && "$VALIDATE_TAG_ONLY" == true ]]; then
  echo "ERROR: --configure-azd and --validate-tag-only are mutually exclusive" >&2
  exit 2
fi

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

ensure_image_tag_available() {
  local repositories
  local tags
  local repository
  local existing_tag
  local repository_exists=false

  if ! repositories="$(
    az acr repository list \
      --name "$ACR_NAME" \
      --only-show-errors \
      --output tsv
  )"; then
    echo "ERROR: failed to inspect repositories in ACR $ACR_NAME" >&2
    return 1
  fi

  while IFS= read -r repository; do
    if [[ "$repository" == "$AGENT_NAME" ]]; then
      repository_exists=true
      break
    fi
  done <<<"$repositories"

  if [[ "$repository_exists" == false ]]; then
    return 0
  fi

  if ! tags="$(
    az acr repository show-tags \
      --name "$ACR_NAME" \
      --repository "$AGENT_NAME" \
      --only-show-errors \
      --output tsv
  )"; then
    echo "ERROR: failed to inspect tags for $AGENT_NAME in ACR $ACR_NAME" >&2
    return 1
  fi

  while IFS= read -r existing_tag; do
    if [[ "$existing_tag" == "$IMAGE_TAG" ]]; then
      echo "ERROR: $AGENT_NAME:$IMAGE_TAG already exists; use a new immutable tag" >&2
      return 1
    fi
  done <<<"$tags"
}

pin_manifest_image() {
  local manifest="$1"
  local image_ref="$2"
  local rendered
  rendered="$(mktemp)"

  if ! awk -v image_ref="$image_ref" '
    /^[[:space:]]*image:[[:space:]]*/ {
      count += 1
      match($0, /^[[:space:]]*/)
      print substr($0, RSTART, RLENGTH) "image: " image_ref
      next
    }
    { print }
    END {
      if (count != 1) {
        exit 1
      }
    }
  ' "$manifest" >"$rendered"; then
    rm -f "$rendered"
    echo "ERROR: expected exactly one image entry in $manifest" >&2
    return 1
  fi

  cp "$rendered" "$manifest"
  rm -f "$rendered"
}

ACR_NAME="$(tf acr_name)"
ACR_LOGIN="$(tf acr_login_server)"

case "$AGENT_KIND" in
  support)
    AGENT_NAME="$(tf foundry_hosted_agent_name)"
    DOCKERFILE="$REPO_ROOT/agents/customer-support-maf/src/customer-support-maf/Dockerfile"
    AZD_PROJECT_DIR="$REPO_ROOT/agents/customer-support-maf"
    IMAGE_ENV_VAR="HOSTED_AGENT_IMAGE"
    ;;
  directive)
    AGENT_NAME="${DIRECTIVE_HOSTED_AGENT_NAME:-$(tf directive_foundry_agent_name)}"
    DIRECTIVE_RELEASE_ID="$(
      tf directive_agent_release_id
    )"
    DOCKERFILE="$REPO_ROOT/agents/directive-rag-maf/src/directive-rag-maf/Dockerfile"
    AZD_PROJECT_DIR="$REPO_ROOT/agents/directive-rag-maf"
    IMAGE_ENV_VAR="DIRECTIVE_HOSTED_AGENT_IMAGE"
    ;;
  *)
    echo "ERROR: --agent must be support or directive" >&2
    exit 2
    ;;
esac

IMAGE_REF="${ACR_LOGIN}/${AGENT_NAME}:${IMAGE_TAG}"
ensure_image_tag_available
if [[ "$VALIDATE_TAG_ONLY" == true ]]; then
  echo "==> Available immutable image tag: ${IMAGE_REF}"
  exit 0
fi

if [[ "$CONFIGURE_AZD" == true ]]; then
  command -v azd >/dev/null || {
    echo "ERROR: azd is required with --configure-azd" >&2
    exit 1
  }
  (
    cd "$AZD_PROJECT_DIR"
    azd env get-value AZURE_ENV_NAME >/dev/null
  )
fi

echo "==> Building ${AGENT_KIND} Hosted MAF image: ${IMAGE_REF}"
az acr build \
  -r "$ACR_NAME" \
  -t "${AGENT_NAME}:${IMAGE_TAG}" \
  -f "$DOCKERFILE" \
  "$REPO_ROOT"

if [[ "$CONFIGURE_AZD" == true ]]; then
  pin_manifest_image "$AZD_PROJECT_DIR/azure.yaml" "$IMAGE_REF"
  (
    cd "$AZD_PROJECT_DIR"
    azd env set AZD_AGENT_SKIP_ACR true
    azd env set "$IMAGE_ENV_VAR" "$IMAGE_REF"
    if [[ "$AGENT_KIND" == "directive" ]]; then
      azd env set DIRECTIVE_AGENT_RELEASE_ID "$DIRECTIVE_RELEASE_ID"
    fi
  )
  echo "==> Configured azd and its manifest to deploy the prebuilt ${IMAGE_REF}"
fi
