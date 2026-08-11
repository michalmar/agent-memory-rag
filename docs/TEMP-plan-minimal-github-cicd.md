# Minimal GitHub application deployment

> **Status:** Planned, not implemented.
>
> **Decision date:** 2026-08-11

## Goal

Automatically rebuild and deploy the backend and frontend Azure Container Apps
when application code is pushed to `main`.

This is intentionally a minimal continuous-deployment workflow, not a complete
CI/CD platform.

## One workflow

Add one workflow:

```text
.github/workflows/deploy-app.yml
```

It runs on:

- a push to `main` that changes application code;
- an optional manual `workflow_dispatch` for rerunning a deployment.

The push path filter includes:

```text
backend/**
frontend/**
agent_contracts/**
directive_contracts/**
.github/workflows/deploy-app.yml
```

Changes limited to `infra/**`, `agents/**`, `maf_hosting/**`, `setup/**`,
`docs/**`, or other repository files do not trigger deployment.

## Deployment sequence

For every matching push, the workflow:

1. Checks out the exact `main` commit.
2. Signs in to Azure through GitHub OIDC.
3. Creates one traceable image tag from the commit SHA and workflow attempt.
4. Builds and pushes `backend:<tag>` through ACR Tasks using the repository-root
   Docker context.
5. Builds and pushes `frontend:<tag>` through ACR Tasks using the frontend
   Docker context.
6. Updates the backend Container App to the new backend image.
7. Updates the frontend Container App to the new frontend image.
8. Verifies the public frontend URL and backend readiness endpoint.

Deployments run sequentially with one concurrency group so two pushes cannot
update the Container Apps at the same time.

Both images are rebuilt on every matching push. Detecting and building only the
changed component is deliberately omitted to keep the first workflow simple.

## Minimal GitHub configuration

Repository secrets:

```text
AZURE_CLIENT_ID
AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID
```

Repository variables:

```text
AZURE_RESOURCE_GROUP
ACR_NAME
ACR_LOGIN_SERVER
BACKEND_CONTAINER_APP
FRONTEND_CONTAINER_APP
```

No GitHub environment, required reviewer, protected branch, CODEOWNERS, security
workflow, or Terraform configuration is required for this version.

## Minimal Azure configuration

Create one user-assigned managed identity for GitHub Actions and:

1. Add a federated credential for:

   ```text
   repo:michalmar/agent-memory-rag:ref:refs/heads/main
   ```

2. Grant the identity `Contributor` on the existing
   `rg-agent-memory-rag` resource group.
3. Store its client ID and the existing tenant/subscription IDs in the GitHub
   repository settings listed above.

The workflow uses short-lived OIDC tokens. It does not need an Azure client
secret, ACR password, Storage key, or publish profile.

## Repository implementation note

Do not call `scripts/deploy_images.sh` unchanged. That script:

- requires a Hosted Agent release tag;
- builds the support Hosted Agent;
- optionally builds the directive Hosted Agent;
- reads deployment names from local Terraform state.

The minimal workflow should run the backend/frontend ACR build and Container App
update commands directly, or call a new app-only helper that receives the
resource names through GitHub variables.

## Explicitly out of scope

- Terraform format, validation, plan, apply, or drift detection.
- Infrastructure deployment of any kind.
- Pull-request checks or unit-test jobs.
- CodeQL, dependency, container, IaC, or secret scanning.
- Branch protection and deployment approvals.
- Hosted Agent builds or Foundry deployment.
- Foundry IQ or Prompt Agent publication.
- Directive ingestion deployment.
- SBOM generation, signing, attestations, or automated rollback.
- Staging and production environment promotion.

Hosted Agent, Foundry, ingestion, infrastructure, and shared-contract releases
remain manual. A change to contracts shared with Hosted Agents may therefore
require a separate manual Hosted Agent release.

## Acceptance criteria

- A matching application-code push to `main` starts the workflow automatically.
- An infrastructure-only push does not start it.
- Backend and frontend ACR builds complete with the same release tag.
- Both Container Apps reference that release tag after deployment.
- The frontend root returns HTTP 200.
- `/api/health/ready` returns HTTP 200 through the frontend proxy.
- A failed build or deployment marks the workflow failed.

