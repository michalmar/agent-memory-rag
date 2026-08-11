# Minimal GitHub application deployment

> **Status:** Implemented.
>
> **Decision date:** 2026-08-11
>
> **Implementation date:** 2026-08-11

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
- an optional manual `workflow_dispatch` targeting `backend`, `frontend`, or
  `all`.

The push path filter includes:

```text
backend/**
frontend/**
agent_contracts/**
directive_contracts/**
.dockerignore
```

Changes limited to `infra/**`, `agents/**`, `maf_hosting/**`, `setup/**`,
`docs/**`, or other repository files do not trigger deployment.

## Deployment sequence

For every matching push, the workflow:

1. Detects the affected application components from the pushed commit range:
   - `backend/**`, `agent_contracts/**`, `directive_contracts/**`, and the root
     `.dockerignore` select the backend;
   - `frontend/**` selects the frontend.
   Renames are evaluated as a delete plus an add so both source and destination
   components are selected.
2. Starts each selected component as an independent matrix job with its own
   concurrency group.
3. After acquiring that component's concurrency slot, checks out the current
   `main` head and signs in to Azure through GitHub OIDC.
4. Creates one traceable image tag from that source commit SHA, workflow run ID,
   and workflow attempt.
5. Builds and pushes each selected image through ACR Tasks using its existing
   Docker context.
6. Updates each selected Container App and waits for its new revision to become
   ready with the expected image.
7. Verifies the public frontend URL and backend readiness endpoint.

Backend deployments and frontend deployments are serialized independently.
GitHub can coalesce an older pending run only when a newer run deploys the same
component. Because source checkout occurs after the component lock is acquired,
an older workflow run cannot roll a component back to an older `main` commit.
If a force-push makes the previous commit unavailable, both components deploy
conservatively.

An untouched component is neither rebuilt nor updated. A manual run makes the
target explicit and supports `backend`, `frontend`, or `all`.

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

The implemented deployment identity is `id-agmem-github-5df652` in
`rg-agent-memory-rag`. Its federated credential trusts only the `main` branch,
and the required GitHub Actions secrets and repository variables are configured.

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
- A backend, shared-contract, or root `.dockerignore`-only change rebuilds and
  deploys only the backend.
- A frontend-only change rebuilds and deploys only the frontend.
- A change spanning both components builds both images in independent component
  jobs.
- An untouched Container App keeps its existing image reference.
- The frontend root returns HTTP 200.
- `/api/health/ready` returns HTTP 200 through the frontend proxy.
- A failed build or deployment marks the workflow failed.
