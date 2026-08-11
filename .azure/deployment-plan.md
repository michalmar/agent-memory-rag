# Azure Deployment Plan - Azure Blob Directive Source

> **Status:** Deployed
>
> **Current release status:** Deployed

Generated: 2026-07-25T20:33:12Z

## Current release: selective GitHub application deployment

Approved: 2026-08-11

This CI/CD-only release adds one GitHub Actions workflow that builds and deploys
the backend and frontend independently. Backend code, shared contracts, and the
root Docker context select the backend; frontend code selects the frontend.
Manual runs can target either component or both. Azure infrastructure and
application source remain unchanged.

- [x] Detect backend-only, frontend-only, and combined push ranges.
- [x] Treat cross-component renames as delete plus add.
- [x] Serialize backend and frontend deployments independently.
- [x] Check out current `main` only after acquiring the component lock.
- [x] Build with the existing ACR Tasks Docker contexts.
- [x] Wait for the expected ready revision and verify public health.
- [x] Configure and verify GitHub OIDC secrets, variables, federation, and RBAC.
- [x] Validate workflow syntax, embedded shell, selectors, and deployment roles.
- [x] Merge the workflow to `main`.
- [x] Run backend-only and frontend-only deployments.
- [x] Confirm each deployment leaves the untouched Container App image unchanged.
- [x] Record workflow runs, revisions, image references, and live acceptance.

### Validation proof

Validated at `2026-08-11T09:36:00Z`.

| Check | Result |
| --- | --- |
| Workflow | `actionlint` passed |
| Embedded shell | All 7 workflow shell blocks passed `bash -n` |
| Push selection | Historical backend-only, frontend-only, and combined commit ranges passed |
| Manual selection | `backend`, `frontend`, and `all` inputs produced the expected matrices |
| Change boundaries | Cross-component rename and root `.dockerignore` targeting passed |
| GitHub configuration | Three OIDC secrets and five Azure resource variables are configured |
| Azure target | `ME-MngEnvMCAP372348-mimarusa-1` / `rg-agent-memory-rag` in `eastus2` is provisioned |
| Deployment identity | `id-agmem-github-5df652` trusts only `repo:michalmar/agent-memory-rag:ref:refs/heads/main` and has resource-group `Contributor` |
| Image pull | Backend and frontend managed identities retain ACR-scoped `AcrPull` |
| Change scope | No Terraform or application-source change; CI/CD workflow and documentation only |

**Validated by:** `azure-validate`

### Deployment proof

Deployed at `2026-08-11T09:46:00Z`.

| Item | Result |
| --- | --- |
| Pull request | [#2](https://github.com/michalmar/agent-memory-rag/pull/2) merged as `66e684374890817d294637db7031b7e2d81b989c` |
| Backend-only run | [31478710782](https://github.com/michalmar/agent-memory-rag/actions/runs/31478710782) succeeded; only `deploy (backend)` ran |
| Frontend-only run | [31478990188](https://github.com/michalmar/agent-memory-rag/actions/runs/31478990188) succeeded; only `deploy (frontend)` ran |
| Backend isolation | Backend changed from `202607252050-blobsource`; frontend remained `session-agents-20260731125529` |
| Frontend isolation | Frontend changed from `session-agents-20260731125529`; backend retained the image from run `31478710782` |
| Backend image | `agmem5df652acr.azurecr.io/backend:66e684374890817d294637db7031b7e2d81b989c-31478710782-1` (`sha256:6b658e856fa70488bd13c5e95250765e3bfe2699010e8b1187357888cbbec295`) |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:66e684374890817d294637db7031b7e2d81b989c-31478990188-1` (`sha256:cabdb3633a6b29801c1f9378bfbab6ca9408cc8a007a03d91e8c338cf3c0ec26`) |
| Revisions | Backend `ca-agmem-backend--0000060` and frontend `ca-agmem-frontend--0000028` are healthy, running, and latest-ready |
| Live acceptance | Frontend returned HTTP 200; `/api/health/ready` returned HTTP 200 with status `ready` and every dependency `ok` |
| Live RBAC | Deployment identity retains resource-group `Contributor`; both Container App identities retain ACR-scoped `AcrPull` |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

Resource group:
`https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

**Deployed by:** `azure-deploy`

## Current release: session agent indicators

Approved: 2026-07-31

This frontend-only release adds a distinct agent badge to every session in the
left thread panel. The badge uses the persisted agent label and matching icon,
with a clear fallback for legacy sessions that do not have runtime metadata.
Azure infrastructure, backend, subscription, and region remain unchanged.

- [x] Add typed agent indicator presentation metadata.
- [x] Render and style the agent badge in every session row.
- [x] Cover persisted, inferred, and legacy agent metadata.
- [x] Run the targeted frontend tests and production build.
- [x] Validate the image-only release against the existing Terraform state.
- [x] Build and deploy a uniquely tagged frontend image.
- [x] Verify the active Azure revision and public endpoint.

### Validation proof

Validated at `2026-07-31T12:54:30Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3 and Azure CLI authentication available |
| Frontend | 2 targeted tests passed; TypeScript and Vite production build passed |
| Terraform | Initialization, recursive format check, syntax validation, 97-resource state access, and no-change plan passed |
| Saved plan | No infrastructure changes; SHA-256 `ffcc10abb13323f73bc5f44323ff82cc6840ecbbb39d96d4d2f26b1f0c8bf32f` |
| Template variables | No unresolved `{{ .Env.* }}` expressions |
| Static RBAC | Frontend managed identity retains the Terraform-managed `AcrPull` assignment on the exact ACR scope |

**Saved plan:**
`/tmp/session-agent-indicator.tfplan`

**Validated by:** `azure-validate`

### Deployment proof

Deployed at `2026-07-31T12:56:30Z`.

| Item | Result |
| --- | --- |
| Terraform apply | No infrastructure changes; 0 added, 0 changed, 0 destroyed |
| ACR Task | Run `ch3f` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:session-agents-20260731125529` |
| Active revision | `ca-agmem-frontend--0000027`, healthy, running, one replica, 100% traffic |
| Public endpoint | HTTP 200; deployed bundle contains the agent badge and legacy fallback |
| Backend readiness | HTTP 200 |
| Live role verification | Frontend identity retains `AcrPull` on the exact ACR scope |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

**Deployed by:** `azure-deploy`

## Previous release: semantic health badge colors

Approved: 2026-07-31

This frontend-only release gives each health value an explicit semantic color:
green for `Healthy`, amber for `Degraded`, red for `Unhealthy`, and neutral
gray for `Checking`. It reuses the existing light/dark theme token system and
rolls only the frontend Container App image. Azure infrastructure, backend,
subscription, and regions remain unchanged.

- [x] Update theme tokens and badge state styles.
- [x] Run frontend tests and production build.
- [x] Validate the image-only release.
- [x] Build and deploy a uniquely tagged frontend image.
- [x] Verify the healthy green badge bundle and active Azure revision.

### Validation proof

Validated at `2026-07-31T11:56:30Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3, Azure CLI 2.80.0, and enabled default subscription `7bc68c68-f434-49ad-ab3e-b883ec39da86` |
| Frontend | 35 tests passed; TypeScript and Vite production build passed |
| Terraform | Initialization, recursive format check, syntax validation, 97-resource state access, and no-change plan passed |
| Saved plan | No infrastructure changes; SHA-256 `22ec22d5adc8545ae8e16e9232573de0e274200a11c6259e286c662cfa72b778` |
| Azure Policy | Six effective policy assignments retrieved without blocking the image-only release |
| Template variables | No unresolved `{{ .Env.* }}` expressions |
| Static RBAC | Frontend managed identity retains `AcrPull` on the exact ACR scope |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/87b04967-9dab-4fb2-bd6d-c20e7982c81f/files/badge-colors-validation.tfplan`

**Validated by:** `azure-validate`

### Deployment proof

Deployed at `2026-07-31T09:59:30Z`.

| Item | Result |
| --- | --- |
| Terraform apply | No infrastructure changes; 0 added, 0 changed, 0 destroyed |
| ACR Task | Run `ch3e` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:health-colors-20260731095739` |
| Image digest | `sha256:6ff8e576c39eb6494432e1f423cd80eeec0c22151cfda9af0bbe9ced441c6a72` |
| Active revision | `ca-agmem-frontend--0000026`, healthy, running, one replica, 100% traffic |
| Public endpoint | HTTP 200; deployed bundle contains success, warning, and danger badge tokens |
| Backend readiness | HTTP 200 |
| Live role verification | Frontend principal retains `AcrPull` on the exact ACR scope |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

**Deployed by:** `azure-deploy`

## Current release: application health badge and semantic billing

Approved: 2026-07-31

This release adds a continuously refreshed health badge to the authenticated
application header. The frontend reads the existing public
`/health/ready` contract without requiring a user token and maps required
dependency failures to `Unhealthy`, optional dependency failures to
`Degraded`, and a fully ready response to `Healthy`. Network failures and
timeouts are reported as `Unhealthy`; polling is bounded and cancelled when
the component disconnects.

Terraform updates the existing Azure AI Search service in place from free
semantic-query quota to billed standard semantic search. The subscription,
tenant, resource group, application region, Search region, identities,
networking, databases, and indexes remain unchanged.

### Current release execution plan

- [x] Diagnose the production failure and confirm Cosmos DB data-plane health.
- [x] Confirm the user-approved subscription and regions.
- [x] Load Azure, Terraform, validation, and deployment guidance.
- [x] Implement and test the frontend health contract and header badge.
- [x] Update and format Terraform semantic billing configuration.
- [x] Set this plan to `Ready for Validation`.
- [x] Invoke `azure-validate` and record fresh validation proof.
- [x] Apply the reviewed Terraform plan.
- [x] Build and deploy a uniquely tagged frontend image.
- [x] Verify Search semantic billing, backend readiness, the active frontend
      revision, the public endpoint, and the deployed health badge.

### Current release validation proof

Validated at `2026-07-31T09:49:15Z`.

| Check | Result |
| --- | --- |
| Toolchain and authentication | Terraform 1.13.3, Azure CLI 2.80.0, and enabled default subscription `7bc68c68-f434-49ad-ab3e-b883ec39da86` |
| Terraform | Initialization, recursive format check, syntax validation, and 97-resource state access passed |
| Saved plan | Exactly one in-place update: `azurerm_search_service.main.semantic_search_sku` from `free` to `standard` |
| Saved plan integrity | SHA-256 `13246d31bb34a719b1b5034aada0bb04b29ab22e531d9e958f0c96799500d600` |
| Azure Policy | Subscription and inherited Defender, Security Baseline, MCAP deploy/deny/audit assignments reviewed; no conflict with the in-place Search update |
| Template variables | No unresolved `{{ .Env.* }}` expressions |
| Frontend | 35 tests passed; TypeScript and Vite production build passed; local preview returned HTTP 200 and contained the health badge implementation |
| Review and integrity | Five-axis code review reported no required findings; `git diff --check` and deployment script syntax passed |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/87b04967-9dab-4fb2-bd6d-c20e7982c81f/files/health-semantic-validation.tfplan`

**Validated by:** `azure-validate`

### Current release deployment proof

Deployed at `2026-07-31T09:52:39Z`.

| Item | Result |
| --- | --- |
| Terraform apply | `azurerm_search_service.main` updated in place; 0 added, 1 changed, 0 destroyed |
| Azure AI Search | Basic service remains running in West Europe; semantic search is `standard`; semantic hybrid query returned HTTP 200 |
| ACR Task | Run `ch3d` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:health-badge-202607310951` |
| Image digest | `sha256:8a52602a3121f22ccb7bd63d3dc64d8f222e3d5a786d9aef3c99b5382e4b6438` |
| Active revision | `ca-agmem-frontend--0000025`, healthy, running, one replica, 100% traffic |
| Backend readiness | HTTP 200, `ready`, no failed or degraded dependencies |
| Public endpoint | HTTP 200; deployed `assets/index-CqXPirKP.js` contains the health badge and dependency-failure labels |
| Live role verification | Frontend principal retains `AcrPull` on the exact ACR scope |
| Post-deployment drift | Terraform reports no changes |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

Resource group:
`https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

**Deployed by:** `azure-deploy`

## 1. Project overview

**Goal:** Deploy the completed cutover from image-packaged directive PDFs to a
private Azure Blob Storage `directive-source` container.

**Path:** Modify the existing Azure application.

The ingestion job will read source PDFs from Blob Storage by managed identity.
The backend will expose role-protected, metadata-only source management, and
the frontend will expose the corresponding right-rail UI and streaming upload
path. No data migration, compatibility mode, or session preservation is
required.

## 2. Requirements and Azure context

| Attribute | Confirmed value |
| --- | --- |
| Classification | Proof of concept |
| Scale | Small, under 1,000 users |
| Budget | Performance-focused |
| Compliance | No additional requirements beyond existing Azure policies |
| Subscription | `ME-MngEnvMCAP372348-mimarusa-1` (`7bc68c68-f434-49ad-ab3e-b883ec39da86`) |
| Tenant | `a7b1484c-f66a-496a-b1cf-35631a50396c` |
| Resource group | `rg-agent-memory-rag` |
| Location | East US 2 (`eastus2`) |
| Deployment mode | Modify the existing Terraform-managed environment |
| Authentication | Existing managed identities and Entra ID |

The user confirmed the subscription, location, requirements, and retention of
the existing Terraform-managed Azure Container Apps architecture.

### Policy constraints

The active subscription and inherited management-group assignments include
Microsoft Defender initiatives, Azure Security Baseline, MCAPSGov deploy,
deny, and audit initiatives, and the classic-resource creation deny policy.
The classic-resource deny rule applies only to legacy `Microsoft.Classic*`
types. This release uses current ARM resource types, private Blob access,
managed identities, OAuth-only data access, and existing required tags. The
saved Terraform plan remains the final policy compatibility check.

## 3. Components detected

| Component | Type | Technology | Path |
| --- | --- | --- | --- |
| Frontend | SPA and reverse proxy | TypeScript, Lit, Vite, Nginx | `frontend/` |
| Backend | API service | Python, FastAPI | `backend/` |
| Directive ingestion | Manual worker job | Python CLI, Azure SDKs | `setup/directive_ingest/` |
| Shared contracts | Python package | Pydantic/dataclasses | `directive_contracts/` |
| Infrastructure | Infrastructure as code | Terraform, AzureRM, AzAPI | `infra/` |
| Identity setup | Entra application automation | Azure CLI, Microsoft Graph | `scripts/create_entra_app.sh` |

No GitHub Copilot SDK markers or specialized deployment route were detected.

## 4. Recipe selection

**Selected:** Pure Terraform plus the repository's existing Azure CLI rollout
scripts.

The environment is already managed with local Terraform state, Azure Container
Registry Tasks, Azure Container Apps, and a managed-identity ingestion job.
The user explicitly chose to keep this architecture rather than introduce azd
or a second state/orchestration layer.

## 5. Architecture

**Stack:** Containers on Azure Container Apps.

| Component | Azure service | Deployment action |
| --- | --- | --- |
| Frontend | Azure Container Apps | Build and roll a new image |
| Backend | Azure Container Apps | Add source settings and roll a new image |
| Directive ingestion | Azure Container Apps Job | Add Blob-source settings and roll a new image |
| Images | Azure Container Registry Premium | Build with ACR Tasks |
| Directive sources | Existing private StorageV2 account | Add private `directive-source` container |
| Directive artifacts | Existing private Blob container | Retain unchanged |
| Catalog/content/mandates | Existing Cosmos DB containers | Retain published data |
| Retrieval | Existing Azure AI Search index | Retain indexed data |
| Parsing and summaries | Existing Document Intelligence and Foundry deployments | Retain unchanged |
| Observability | Existing Application Insights and Log Analytics | Retain unchanged |

The existing Blob private endpoint and private DNS cover both containers.
The backend receives `Storage Blob Data Contributor` only on
`directive-source`; ingestion receives `Storage Blob Data Reader` there and
retains contributor access only on `directive-artifacts`. The frontend and
Hosted Agent identities receive no source-container data role.

## 6. Provisioning limit checklist

| Resource type | Number to deploy | Total after deployment | Limit/quota | Evidence |
| --- | ---: | ---: | --- | --- |
| `Microsoft.Storage/storageAccounts` | 0 | 1 | No quota consumed by this release | `az quota usage list` reports one account in `eastus2` |
| `Microsoft.Storage/storageAccounts/blobServices/containers` | 1 | 2 | No separate container-count quota is published; one container can use the storage account capacity | ARM lists one current container; Microsoft Blob scalability targets |
| `Microsoft.Authorization/roleAssignments` | 2 | 149 | 4,000 per subscription | 147 current assignments plus two planned container-scoped assignments |
| `Microsoft.App/containerApps` | 0 new | Existing count unchanged | No new app capacity | Backend and frontend are updated in place |
| `Microsoft.App/jobs` | 0 new | Existing count unchanged | No new job capacity | Ingestion job is updated in place |

`Microsoft.Quota` was registered for the required check. Its storage usage
endpoint reports one existing storage account; the quota limit endpoint was
still propagating registration, but this release creates no storage account.
The source corpus is additionally capped by the application at 512 MiB and each
management upload at 50 MiB.

**Status:** All planned resources are within applicable limits.

## 7. Execution checklist

### Phase 1: Planning

- [x] Analyze workspace in MODIFY mode.
- [x] Gather classification, scale, budget, compliance, and architecture requirements.
- [x] Confirm subscription and location with the user.
- [x] Inspect subscription policy assignments.
- [x] Register `Microsoft.Quota` with user approval and validate capacity.
- [x] Scan the codebase and specialized technology markers.
- [x] Select the existing pure-Terraform recipe.
- [x] Plan the architecture and rollout.
- [x] User approved this plan.

### Phase 2: Preparation

- [x] Load Blob Storage, identity, Terraform, and Container Apps guidance.
- [x] Confirm the completed implementation matches the approved plan.
- [x] Perform local functional verification.
- [x] Set status to `Ready for Validation`.

### Phase 3: Validation

- [x] Invoke `azure-validate`.
- [x] All validation checks pass:
  - [x] 1. Terraform installation.
  - [x] 2. Azure CLI installation.
  - [x] 3. Authentication and confirmed subscription.
  - [x] 4. Terraform initialization.
  - [x] 5. Terraform recursive format check.
  - [x] 6. Terraform syntax validation.
  - [x] 7. Saved Terraform plan preview.
  - [x] 8. Terraform state-backend access.
  - [x] 9. Azure Policy validation.
  - [x] 10. Template-variable resolution check.
  - [x] 11. Backend, ingestion, frontend, and shared-contract tests.
  - [x] 12. Backend/ingestion compilation and frontend production build.
  - [x] 13. Static least-privilege role verification.
- [x] Record validation proof and set status to `Validated`.

### Phase 4: Deployment

- [x] Invoke `azure-deploy`.
- [x] Review the saved Terraform plan.
- [x] Stop for renewed approval if any resource is destroyed or replaced.
- [x] Obtain explicit confirmation for the two ARM RBAC changes.
- [x] Apply only the reviewed saved Terraform plan.
- [x] Update the existing Entra app, using explicit app ID
      `dbf8eef6-d745-4b73-b3ac-edc93a15c339`, to add
      `DirectiveSource.Manage`.
- [x] Obtain explicit confirmation before assigning that app role to the
      signed-in deployment operator.
- [x] Build backend and frontend images with ACR Tasks and roll
      `ca-agmem-backend` and `ca-agmem-frontend`.
- [x] Verify healthy revisions, frontend HTTP 200, readiness, and role
      protection.
- [x] Upload the seven repository fixture PDFs through the authenticated,
      create-only management API.
- [x] Verify upload did not start the ingestion job.
- [x] Build and deploy the ingestion image with
      `scripts/deploy_directive_ingestion.sh`.
- [x] Complete Blob-mode preflight, publication, verification, and restore the
      job to `run-daily`.
- [x] Verify source metadata, retained published data, retrieval, live RBAC,
      and a clean post-deployment Terraform plan.
- [x] Record fully qualified endpoint URLs and set status to `Deployed`.

## 8. Deployment and security boundary

The expected Terraform plan is three additions, two in-place configuration
updates, and no destruction:

- add `azapi_resource.directive_source_container`;
- add backend `Storage Blob Data Contributor` on that container;
- add ingestion `Storage Blob Data Reader` on that container;
- update backend environment settings in place;
- update ingestion job environment settings in place.

No Search index, Cosmos container, published directive, immutable artifact,
session, model deployment, resource group, storage account, Container App, or
Container Apps Job may be deleted or replaced. A plan containing any destroy or
replacement action requires renewed user approval.

The release intentionally makes security changes: two container-scoped ARM
role assignments, one Entra application role, and one operator app-role
assignment. Each security mutation requires explicit confirmation immediately
before execution.

Source bootstrap creates seven blobs with existing filenames and never
overwrites. Source deletion is not part of deployment. Existing published data
is retained, and the unchanged processing hashes should avoid model and Search
writes during reconciliation.

## 9. Acceptance criteria

- [x] Terraform creates the private `directive-source` container without
      destroying or replacing any resource.
- [x] Live backend and ingestion identities have only the planned
      container-scoped source roles.
- [x] The existing Entra app exposes `DirectiveSource.Manage`, and only the
      approved operator receives it.
- [x] The frontend and backend revisions are healthy.
- [x] The approved operator can list and upload metadata-only source PDFs.
- [x] Seven source PDFs exist in Blob Storage and no upload starts ingestion.
- [x] Blob-mode ingestion preflight, publication, and read-only verification
      succeed.
- [x] The job is restored to `run-daily`.
- [x] Existing published directives, content, mandates, Search chunks, and
      immutable artifacts remain valid.
- [x] Backend readiness and an end-to-end directive retrieval request succeed.
- [x] Post-deployment Terraform plan reports no changes.

## 7. Validation proof

Validated at `2026-07-25T20:39:45Z`.

### Functional verification

- **Backend:** 162 tests and 12 subtests passed; Python compilation passed;
  local `/health/live` and authenticated `/me` returned successful responses.
- **Ingestion:** 49 tests passed; Python compilation passed.
- **Frontend:** 27 tests passed; the production build succeeded; local preview
  returned HTTP 200.
- **Container images:** Docker 27.4.0 is installed, but its daemon is not
  running. Image builds and startup health remain deployment checks through ACR
  Tasks and Azure Container Apps.

| Check | Command run | Result | Timestamp |
| --- | --- | --- | --- |
| Toolchain and authentication | `terraform version`; `az version`; `az account show` | Terraform 1.13.3, Azure CLI 2.80.0, enabled confirmed subscription and tenant | 2026-07-25T20:39:45Z |
| Terraform initialization | `terraform -chdir=infra init -input=false -no-color` | Existing local state initialized; AzureRM 4.80.0 and AzAPI 2.10.0 loaded | 2026-07-25T20:39:45Z |
| Terraform formatting | `terraform -chdir=infra fmt -check -recursive` | Passed | 2026-07-25T20:39:45Z |
| Terraform syntax | `terraform -chdir=infra validate -no-color` | Passed | 2026-07-25T20:39:45Z |
| State access | `terraform -chdir=infra state list` | 94 managed resources readable | 2026-07-25T20:39:45Z |
| Saved plan | `terraform -chdir=infra plan -input=false -no-color -out=<session>/files/blob-source.tfplan` | 3 creates, 2 in-place updates, 0 destroys | 2026-07-25T20:39:45Z |
| Plan action review | `terraform show -json` plus `jq` action inspection | Only source container, two source RBAC assignments, backend update, and ingestion-job update | 2026-07-25T20:39:45Z |
| Saved plan integrity | `shasum -a 256 <session>/files/blob-source.tfplan` | `777184437db510001dbc3e8aee377a1d77bd95c7a49351eb0c7759a4ea03013d` | 2026-07-25T20:39:45Z |
| Azure Policy | `policy_assignment_list` and governing deny-rule inspection | Active Defender/governance policies reviewed; planned current ARM types and settings have no identified conflict | 2026-07-25T20:39:45Z |
| Template variables | Repository search for `{{ .Env.* }}` in Terraform inputs | No unresolved Go-style templates | 2026-07-25T20:39:45Z |
| Backend | `backend/.venv/bin/python -m pytest backend/tests -q`; `compileall` | 162 tests and 12 subtests passed; compilation passed | 2026-07-25T20:39:45Z |
| Ingestion | `setup/directive_ingest/.venv/bin/python -m pytest setup/directive_ingest/tests -q`; `compileall` | 49 tests passed; compilation passed | 2026-07-25T20:39:45Z |
| Frontend | `npm test -- --reporter=dot`; `npm run build` | 27 tests passed; TypeScript and Vite production build passed | 2026-07-25T20:39:45Z |
| Script and patch integrity | `bash -n` on rollout scripts; `git diff --check` | Passed | 2026-07-25T20:39:45Z |

**Saved plan:** `/Users/mimarusa/.copilot/session-state/8321e24b-94a6-4dad-8321-415b9b2a2bcc/files/blob-source.tfplan`

**Validated by:** `azure-validate`

### Static role verification

- **Backend identity:** Existing ACR pull, Search reader, directive Cosmos reader,
  artifact Blob reader, and model roles remain. The plan adds only
  `Storage Blob Data Contributor` on the exact `directive-source` container,
  matching metadata list, create-only upload, and exact delete operations.
- **Ingestion identity:** Existing ACR pull, artifact Blob contributor, Search,
  Cosmos, Document Intelligence, and model roles remain. The plan adds only
  `Storage Blob Data Reader` on the exact `directive-source` container,
  matching list and download operations.
- **Frontend and Hosted Agent identities:** No source-container data role.
- **Credentials:** Azure-hosted backend and ingestion paths use their explicit
  user-assigned `ManagedIdentityCredential`; no storage keys or SAS tokens are
  introduced.
- **Status:** Verified; no role issue found.

## 11. Deployment artifacts

| Artifact | Purpose | Status |
| --- | --- | --- |
| `.azure/deployment-plan.md` | Current deployment source of truth | Deployed |
| `infra/*.tf` | Container, RBAC, and runtime configuration | Applied |
| `backend/Dockerfile` | Backend image | Deployed |
| `frontend/Dockerfile` | Frontend image | Deployed |
| `setup/directive_ingest/Dockerfile` | Blob-mode ingestion image | Deployed |
| `scripts/create_entra_app.sh` | Safe existing-app role update | Executed |
| `scripts/deploy_directive_ingestion.sh` | Ingestion rollout and verification | Executed |

## 12. Deployment verification

Deployed to `ME-MngEnvMCAP372348-mimarusa-1`
(`7bc68c68-f434-49ad-ab3e-b883ec39da86`) in `eastus2` on
2026-07-25 UTC.

### Infrastructure and authorization

- Terraform applied `3 added, 2 changed, 0 destroyed`; the final plan reported
  `No changes. Your infrastructure matches the configuration.`
- Created private container `directive-source` in `agmem5df652docs`.
- Backend principal `8987ff56-8834-45a2-8e18-2f1a776bba37` has
  `Storage Blob Data Contributor` and ingestion principal
  `f98f333c-4a20-4092-938a-3e405c702c4b` has
  `Storage Blob Data Reader`, both scoped exactly to `directive-source`.
- The frontend principal has no source-container assignment.
- Enabled user app role `DirectiveSource.Manage`
  (`993505a1-1ba5-4103-a540-69885b82e1af`) and verified the single approved
  operator assignment. A refreshed token carried the role and `/api/me`
  returned `can_manage_directive_sources: true`.

### Images and revisions

| Workload | Tag | Digest |
| --- | --- | --- |
| Backend | `202607252050-blobsource` | `sha256:bfac89d613033ecae41978696ad11bbe9d32a96926837ac3aaefeaa42eef8ec9` |
| Frontend | `202607252050-blobsource` | `sha256:3b19c05b7e27203482529068a502d37d9850c83e556f4212edcad11d3995c9f5` |
| Ingestion | `202607252055-blobsource` | `sha256:99c1d0009f2890bfd42c8adcfbd447d87849111ac21843ff415400a39c0e490a` |

Backend revision `ca-agmem-backend--0000059` and frontend revision
`ca-agmem-frontend--0000023` are healthy, provisioned, and receive 100% of
traffic. The ingestion job is provisioned successfully on the new image with
command `directive-ingest` and argument `run-daily`.

### Source and ingestion checks

- Uploaded 7 PDFs totaling 803,856 bytes through the authenticated API.
- Source listing returns all 7 items. A duplicate upload returns `409` without
  changing the count, and uploads did not start ingestion.
- Managed-identity preflight passed for source Blob read, artifact Blob write,
  Cosmos, Search, Document Intelligence, embeddings, and summary-model access.
- Blob reconciliation reported `source_count=7`, `skipped_count=7`,
  `changed_count=0`, and `chunk_count=0`.
- Verification passed for 7 published/source versions, 91 content sections,
  91 published chunks, 4 current directives, 14 required artifacts,
  5 mandate assignments, the 3072-dimensional vector configuration, and a
  direct hybrid query.

### Live acceptance

- Frontend:
  `https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`
- Readiness returned `ready` with every dependency `ok`, including
  `directive_sources`.
- An authenticated Directive Assistant request returned HTTP 200, completed
  with `RUN_FINISHED`, exercised `search_directives`, and emitted grounded
  directive references.
- Resource group:
  `https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

## 13. Completion

Current phase: deployed.

## 14. Frontend citation section navigation release

Release date: 2026-07-26.

### Scope

This is an immutable frontend-only release. Inline directive citations and
detailed Sources rows open the existing exact-version document drawer at the
validated Markdown section, display section/page context, and preserve the cited
page when the PDF tab is opened. Grouped Documents actions continue to open the
document at the top. Backend APIs, agent contracts, ingestion, data, RBAC, and
Terraform infrastructure are unchanged.

### Validation checklist

- [x] Invoke `azure-validate`.
- [x] All validation checks pass:
  - [x] 1. Terraform installation.
  - [x] 2. Azure CLI installation.
  - [x] 3. Authentication and confirmed subscription.
  - [x] 4. Terraform initialization.
  - [x] 5. Terraform recursive format check.
  - [x] 6. Terraform syntax validation.
  - [x] 7. Terraform plan preview.
  - [x] 8. Terraform state-backend access.
  - [x] 9. Azure Policy validation.
  - [x] 10. Template-variable resolution check.
  - [x] 11. Frontend unit tests and production build.
  - [x] 12. Browser citation-navigation regression flow.
  - [x] 13. Static least-privilege role verification.
  - [x] 14. Diff and deployment-script integrity.
- [x] Record current validation proof and set this release to `Validated`.

### Validation proof

Validated at `2026-07-26T07:09:55Z`.

| Check | Command or review | Result |
| --- | --- | --- |
| Toolchain | `terraform version`; `az version` | Terraform 1.13.3 and Azure CLI 2.80.0 |
| Authentication | `az account show --subscription 7bc68c68-f434-49ad-ab3e-b883ec39da86` | Enabled subscription `ME-MngEnvMCAP372348-mimarusa-1`, tenant `a7b1484c-f66a-496a-b1cf-35631a50396c` |
| Terraform | `init`; `fmt -check -recursive`; `validate`; `state list`; saved `plan -detailed-exitcode` | Passed; 97 resources readable; exit 0; no changes and 0 changed resources |
| Saved plan integrity | `shasum -a 256` | `2f88dfd983e74c73829615be2211dccb72e5e0c12088a0a29bf285cea9969f62` |
| Azure Policy | `policy_assignment_list` at subscription scope | Current Defender, Azure Security Baseline, and MCAP governance assignments reviewed; this frontend image-only release adds or changes no Azure resource |
| Template variables | Search for unresolved `{{ .Env.* }}` in Terraform inputs | No matches |
| Frontend | `npm test`; `npm run build` | 32 tests passed; TypeScript and Vite production build passed |
| Browser flow | Playwright Chromium regression script | Inline and Sources navigation, exact heading focus, same-document reuse/top reset, cited PDF page, external-source preservation, relative PDF intent, and focus restoration passed |
| Independent review | Five-axis review and re-review | Required scroll-reset finding fixed; no remaining high-confidence issues |
| Static roles | IaC/diff review | No infrastructure diff or new data operation; the frontend retains only its existing ACR-scoped `AcrPull` assignment |
| Integrity | `git diff --check`; `bash -n scripts/deploy_images.sh` | Passed |
| Local image runtime | `docker info` | Docker daemon unavailable; ACR Task build and Container Apps health are deployment gates |

**Saved plan:**
`/Users/mimarusa/.copilot/session-state/cb9fd353-0eaf-48c5-b9a8-aa60bef10375/files/citation-navigation-validation.tfplan`

**Validated by:** `azure-validate`

### Deployment checklist

- [x] Invoke `azure-deploy`.
- [x] Build a uniquely tagged frontend image with an ACR Task.
- [x] Roll only `ca-agmem-frontend` to the immutable image.
- [x] Verify the new revision is healthy and receives 100% of traffic.
- [x] Verify the production root, configuration, deployed bundle, and directive
      citation interaction.
- [x] Record the image digest, revision, rollback target, endpoint, and portal URL.

### Deployment proof

Deployed at `2026-07-26T07:20:02Z` to
`ME-MngEnvMCAP372348-mimarusa-1`
(`7bc68c68-f434-49ad-ab3e-b883ec39da86`) in `eastus2`.

| Item | Result |
| --- | --- |
| ACR Task | Run `ch3c` succeeded |
| Frontend image | `agmem5df652acr.azurecr.io/frontend:citation-sections-20260726071405` |
| Image digest | `sha256:4e2af92d8f67dd3b9648824b26e59950cdb333d41344732e77f1985966ebc525` |
| Active revision | `ca-agmem-frontend--0000024`, healthy, provisioned, one replica, 100% traffic |
| Rollback revision | `ca-agmem-frontend--0000023`, image `frontend:202607252050-blobsource` |
| Runtime dependency audit | `npm audit --omit=dev`: 0 vulnerabilities |
| Public endpoint | HTTP 200; deployed `assets/index-Bd2enmIj.js` contains the citation-location implementation |
| Backend readiness | `ready`; all required dependencies `ok` |
| Authorization boundary | Anonymous exact-version directive document request returns HTTP 401 |
| Deployed browser flow | Passed against the production bundle with isolated in-browser test data and API stubs |
| Live role verification | Frontend principal `2d5ffcf5-89b7-4689-816c-ccc5c62c98bf` has `AcrPull` on the exact ACR scope |
| Post-deployment drift | Terraform exit 0; no changes and 0 changed resources |

Frontend:
`https://ca-agmem-frontend.salmonmeadow-d85c9acb.eastus2.azurecontainerapps.io/`

Resource group:
`https://portal.azure.com/#resource/subscriptions/7bc68c68-f434-49ad-ab3e-b883ec39da86/resourceGroups/rg-agent-memory-rag/overview`

**Deployed by:** `azure-deploy`
