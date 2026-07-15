#!/usr/bin/env bash
# Build application, PostgreSQL bootstrap, and Hosted MAF images in ACR.
#
# The public ACR endpoint is required by the non-injected Hosted Agent runtime.
# Authentication remains Entra/RBAC-only; admin and anonymous pull are disabled.
# ACA continues to resolve the private endpoint through VNet-linked private DNS.
#
# Prereqs: az CLI logged in; run from repo root or anywhere (paths are resolved).
# Reads resource names from `terraform output` in ../infra.
set -euo pipefail

export AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-$HOME/.azure-365}"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

tf() { terraform -chdir="$INFRA_DIR" output -raw "$1"; }

RG="$(tf resource_group)"
ACR_NAME="$(tf acr_name)"
ACR_LOGIN="$(tf acr_login_server)"
BACKEND_APP="$(tf backend_app_name)"
FRONTEND_APP="$(tf frontend_app_name)"
PG_SETUP_JOB="$(tf postgres_setup_job_name)"
HOSTED_AGENT_NAME="$(tf foundry_hosted_agent_name)"
AGENT_RELEASE_ID="$(tf agent_release_id)"

TAG="${1:-$(date +%Y%m%d%H%M%S)}"
BACKEND_IMG="$ACR_LOGIN/backend:$TAG"
FRONTEND_IMG="$ACR_LOGIN/frontend:$TAG"
PG_SETUP_IMG="$ACR_LOGIN/pg-bootstrap:$TAG"
HOSTED_AGENT_IMG="$ACR_LOGIN/$HOSTED_AGENT_NAME:$AGENT_RELEASE_ID"

echo "==> Registry : $ACR_LOGIN"
echo "==> Tag      : $TAG"
echo "==> Backend  : $BACKEND_APP"
echo "==> Frontend : $FRONTEND_APP"
echo "==> PG job   : $PG_SETUP_JOB"
echo "==> Hosted   : $HOSTED_AGENT_IMG"

echo "==> Building backend image (server-side ACR task)"
az acr build -r "$ACR_NAME" -t "backend:$TAG" -f "$REPO_ROOT/backend/Dockerfile" "$REPO_ROOT"

echo "==> Building frontend image (server-side ACR task)"
az acr build -r "$ACR_NAME" -t "frontend:$TAG" -f "$REPO_ROOT/frontend/Dockerfile" "$REPO_ROOT/frontend"

echo "==> Building PostgreSQL bootstrap image (server-side ACR task)"
az acr build -r "$ACR_NAME" -t "pg-bootstrap:$TAG" -f "$REPO_ROOT/setup/postgres/Dockerfile" "$REPO_ROOT/setup/postgres"

echo "==> Building Hosted MAF image (server-side ACR task)"
az acr build -r "$ACR_NAME" -t "$HOSTED_AGENT_NAME:$AGENT_RELEASE_ID" -f "$REPO_ROOT/agents/customer-support-maf/src/customer-support-maf/Dockerfile" "$REPO_ROOT"

echo "==> Rolling backend Container App -> $BACKEND_IMG"
az containerapp update -n "$BACKEND_APP" -g "$RG" --image "$BACKEND_IMG" -o none

echo "==> Rolling frontend Container App -> $FRONTEND_IMG"
az containerapp update -n "$FRONTEND_APP" -g "$RG" --image "$FRONTEND_IMG" -o none

echo "==> Updating PostgreSQL bootstrap job -> $PG_SETUP_IMG"
az containerapp job update -n "$PG_SETUP_JOB" -g "$RG" --image "$PG_SETUP_IMG" -o none

echo "==> Done. Images deployed with tag $TAG"
