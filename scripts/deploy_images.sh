#!/usr/bin/env bash
# Build application and Hosted MAF images in ACR.
#
# The public ACR endpoint is required by the non-injected Hosted Agent runtime.
# Authentication remains Entra/RBAC-only; admin and anonymous pull are disabled.
# ACA continues to resolve the private endpoint through VNet-linked private DNS.
#
# Prereqs: az and azd CLIs logged in; run from anywhere (paths are resolved).
# Reads resource names from `terraform output` in ../infra.
# Usage: deploy_images.sh [app-tag] --support-agent-tag <new-tag>
#        [--with-directive --directive-agent-tag <new-tag>]
# The directive Hosted image is opt-in; default runs remain support-only.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
TAG=""
WITH_DIRECTIVE=false
SUPPORT_AGENT_TAG=""
DIRECTIVE_AGENT_TAG=""
BUILD_CONTEXT=""

cleanup() {
  if [[ -n "$BUILD_CONTEXT" && -d "$BUILD_CONTEXT" ]]; then
    rm -rf "$BUILD_CONTEXT"
  fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-directive)
      WITH_DIRECTIVE=true
      shift
      ;;
    --support-agent-tag|--directive-agent-tag)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "ERROR: $1 requires a value" >&2
        exit 2
      fi
      if [[ "$1" == "--support-agent-tag" ]]; then
        SUPPORT_AGENT_TAG="$2"
      else
        DIRECTIVE_AGENT_TAG="$2"
      fi
      shift 2
      ;;
    --*)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
    *)
      if [[ -n "$TAG" ]]; then
        echo "ERROR: only one image tag may be provided" >&2
        exit 2
      fi
      TAG="$1"
      shift
      ;;
  esac
done

if [[ -z "$SUPPORT_AGENT_TAG" ]]; then
  echo "ERROR: --support-agent-tag is required" >&2
  exit 2
fi
if [[ "$WITH_DIRECTIVE" == true && -z "$DIRECTIVE_AGENT_TAG" ]]; then
  echo "ERROR: --directive-agent-tag is required with --with-directive" >&2
  exit 2
fi
if [[ "$WITH_DIRECTIVE" == false && -n "$DIRECTIVE_AGENT_TAG" ]]; then
  echo "ERROR: --directive-agent-tag requires --with-directive" >&2
  exit 2
fi

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

RG="$(tf resource_group)"
ACR_NAME="$(tf acr_name)"
ACR_LOGIN="$(tf acr_login_server)"
BACKEND_APP="$(tf backend_app_name)"
FRONTEND_APP="$(tf frontend_app_name)"
HOSTED_AGENT_NAME="$(tf foundry_hosted_agent_name)"

TAG="${TAG:-$(date +%Y%m%d%H%M%S)}"
BACKEND_IMG="$ACR_LOGIN/backend:$TAG"
FRONTEND_IMG="$ACR_LOGIN/frontend:$TAG"
HOSTED_AGENT_IMG="$ACR_LOGIN/$HOSTED_AGENT_NAME:$SUPPORT_AGENT_TAG"
DIRECTIVE_AGENT_NAME=""
DIRECTIVE_AGENT_IMG=""
if [[ "$WITH_DIRECTIVE" == true ]]; then
  DIRECTIVE_AGENT_NAME="$(tf directive_foundry_agent_name)"
  DIRECTIVE_AGENT_IMG="$ACR_LOGIN/$DIRECTIVE_AGENT_NAME:$DIRECTIVE_AGENT_TAG"
fi

echo "==> Registry : $ACR_LOGIN"
echo "==> Tag      : $TAG"
echo "==> Backend  : $BACKEND_APP"
echo "==> Frontend : $FRONTEND_APP"
echo "==> Hosted   : $HOSTED_AGENT_IMG"
if [[ "$WITH_DIRECTIVE" == true ]]; then
  echo "==> Directive: $DIRECTIVE_AGENT_IMG"
fi

echo "==> Verifying Hosted image tags are new"
"$SCRIPT_DIR/build_hosted_agent_image.sh" \
  --agent support \
  --tag "$SUPPORT_AGENT_TAG" \
  --validate-tag-only
if [[ "$WITH_DIRECTIVE" == true ]]; then
  "$SCRIPT_DIR/build_hosted_agent_image.sh" \
    --agent directive \
    --tag "$DIRECTIVE_AGENT_TAG" \
    --validate-tag-only
fi

BUILD_CONTEXT="$(mktemp -d "${TMPDIR:-/tmp}/agent-memory-images.XXXXXX")"
tar \
  --exclude='*/.env' \
  --exclude='*/.env.*' \
  --exclude='*/.venv' \
  --exclude='*/.venv/*' \
  --exclude='*/.pytest_cache' \
  --exclude='*/.pytest_cache/*' \
  --exclude='*/__pycache__' \
  --exclude='*/__pycache__/*' \
  --exclude='*/node_modules' \
  --exclude='*/node_modules/*' \
  --exclude='*/dist' \
  --exclude='*/dist/*' \
  --exclude='*/.azure' \
  --exclude='*/.azure/*' \
  -C "$REPO_ROOT" \
  -cf - \
  agent_contracts \
  directive_contracts \
  maf_hosting \
  backend \
  agents/customer-support-maf/src/customer-support-maf \
  agents/directive-rag-maf/src/directive-rag-maf |
  tar -C "$BUILD_CONTEXT" -xf -

echo "==> Building backend image (server-side ACR task)"
az acr build \
  -r "$ACR_NAME" \
  -t "backend:$TAG" \
  -f "$BUILD_CONTEXT/backend/Dockerfile" \
  "$BUILD_CONTEXT"

echo "==> Building frontend image (server-side ACR task)"
az acr build -r "$ACR_NAME" -t "frontend:$TAG" -f "$REPO_ROOT/frontend/Dockerfile" "$REPO_ROOT/frontend"

echo "==> Building Hosted MAF image (server-side ACR task)"
REPO_BUILD_CONTEXT="$BUILD_CONTEXT" "$SCRIPT_DIR/build_hosted_agent_image.sh" \
  --agent support \
  --tag "$SUPPORT_AGENT_TAG" \
  --configure-azd

if [[ "$WITH_DIRECTIVE" == true ]]; then
  echo "==> Building directive Hosted MAF image (server-side ACR task)"
  REPO_BUILD_CONTEXT="$BUILD_CONTEXT" "$SCRIPT_DIR/build_hosted_agent_image.sh" \
    --agent directive \
    --tag "$DIRECTIVE_AGENT_TAG" \
    --configure-azd
fi

echo "==> Rolling backend Container App -> $BACKEND_IMG"
az containerapp update -n "$BACKEND_APP" -g "$RG" --image "$BACKEND_IMG" -o none

echo "==> Rolling frontend Container App -> $FRONTEND_IMG"
az containerapp update -n "$FRONTEND_APP" -g "$RG" --image "$FRONTEND_IMG" -o none

echo "==> Done. Application images deployed with tag $TAG; Hosted images built."
