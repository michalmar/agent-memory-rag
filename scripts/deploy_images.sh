#!/usr/bin/env bash
# Build + push backend/frontend images to the private ACR and roll the Container Apps.
#
# ACR is Premium with public network access DISABLED. `az acr build` runs the build
# server-side inside ACR, but the client still needs to reach the registry data plane
# to upload the build context — so we toggle public access on for the build window,
# then turn it back off. ACA continues to *pull* over the private endpoint via MI.
#
# Prereqs: az CLI logged in; run from repo root or anywhere (paths are resolved).
# Reads resource names from `terraform output` in ../infra.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

RG="$(tf resource_group)"
ACR_NAME="$(tf acr_name)"
ACR_LOGIN="$(tf acr_login_server)"
BACKEND_APP="$(tf backend_app_name)"
FRONTEND_APP="$(tf frontend_app_name)"

TAG="${1:-$(date +%Y%m%d%H%M%S)}"
BACKEND_IMG="$ACR_LOGIN/backend:$TAG"
FRONTEND_IMG="$ACR_LOGIN/frontend:$TAG"

echo "==> Registry : $ACR_LOGIN"
echo "==> Tag      : $TAG"
echo "==> Backend  : $BACKEND_APP"
echo "==> Frontend : $FRONTEND_APP"

cleanup() {
  echo "==> Locking ACR back down (deny + private only)"
  az acr update -n "$ACR_NAME" -g "$RG" --default-action Deny --public-network-enabled false -o none || true
}
trap cleanup EXIT

echo "==> Temporarily enabling ACR public network access for build upload"
az acr update -n "$ACR_NAME" -g "$RG" --public-network-enabled true --default-action Allow -o none
# Give the network rule a moment to propagate (ACR build agents use dynamic IPs).
sleep 40

echo "==> Building backend image (server-side ACR task)"
az acr build -r "$ACR_NAME" -t "backend:$TAG" -f "$REPO_ROOT/backend/Dockerfile" "$REPO_ROOT/backend"

echo "==> Building frontend image (server-side ACR task)"
az acr build -r "$ACR_NAME" -t "frontend:$TAG" -f "$REPO_ROOT/frontend/Dockerfile" "$REPO_ROOT/frontend"

# ACR public access no longer needed; cleanup trap will disable it after the updates.
echo "==> Rolling backend Container App -> $BACKEND_IMG"
az containerapp update -n "$BACKEND_APP" -g "$RG" --image "$BACKEND_IMG" -o none

echo "==> Rolling frontend Container App -> $FRONTEND_IMG"
az containerapp update -n "$FRONTEND_APP" -g "$RG" --image "$FRONTEND_IMG" -o none

echo "==> Done. Images deployed with tag $TAG"
