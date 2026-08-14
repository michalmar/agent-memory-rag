# Global Administrator cross-tenant deployment plan

This is the ordered execution plan for a **single target-tenant Global
Administrator** who performs the complete deployment without repository access.
The handoff archive contains the infrastructure, application source, release
scripts, this plan, and an input template.

The source document is
[`CROSS-TENANT-AZURE-DEPLOYMENT.md`](CROSS-TENANT-AZURE-DEPLOYMENT.md).
This plan changes its multi-actor model to one person and one deployment
identity. It does not change the architecture.

## 1. Security model

Microsoft Entra Global Administrator and Azure RBAC are independent:

- Global Administrator permits the Entra app, enterprise app, app-role, consent,
  and optional license operations.
- It does **not** permit Azure resource deployment.
- The same user temporarily enables **Access management for Azure resources**.
  Microsoft then grants that user **User Access Administrator** at root scope
  (`/`).
- While root elevation is active, the user creates direct
  **Contributor** and **User Access Administrator** assignments for themselves
  at the target subscription.
- Root elevation is disabled immediately after those subscription assignments
  are verified. It is not left enabled for the deployment.
- Terraform and every release run as that same user. This is required because
  the current IaC grants deployment-time data roles to
  `data.azurerm_client_config.current.object_id`.
- At the end, remove the script-created subscription **Contributor** assignment
  first and **User Access Administrator** last.

Do not substitute **Role Based Access Control Administrator** for User Access
Administrator in this repository. The first apply creates a custom role
definition and requires `Microsoft.Authorization/roleDefinitions/write`.

### Residual access decision

Removing Contributor and User Access Administrator does not remove the narrower
data-plane roles that Terraform deliberately assigns to its current principal:

- Cognitive Services OpenAI User;
- Search Index Data Contributor;
- Search Service Contributor;
- Azure AI Project Manager;
- Storage Blob Data Contributor for directive artifacts;
- Cognitive Services User for Document Intelligence;
- Cosmos DB Built-in Data Contributor.

The deployment owner must approve retaining those IaC-managed roles for the
Global Administrator. Do not delete them manually because that creates
Terraform drift. If the Global Administrator must have no post-deployment data
access, refactor the IaC to use an explicit non-admin deployer principal before
starting this plan.

## 2. Package contents and integrity

The handoff archive contains:

| Path | Purpose |
| --- | --- |
| `START-HERE.md` | This execution plan |
| `global-admin-inputs.env.example` | Input and decision template |
| `infra/` | Terraform configuration; no state, target variables, or saved plan |
| `scripts/` | Entra, authorization, provider, build, release, and ingestion helpers |
| `backend/`, `frontend/`, `agents/`, `setup/` | Application and deployment source |
| `agent_contracts/`, `directive_contracts/`, `maf_hosting/` | Shared build dependencies |
| `SOURCE-MANIFEST.txt` | Expected files |
| `SHA256SUMS` | Per-file integrity checks |
| `PACKAGE-SOURCE.txt` | Source commit and build timestamp |

The package builder intentionally excludes Git metadata, `.azure/`, Terraform
state, `terraform.tfvars`, saved plans, caches, test files, and the historical
source-environment deployment plan. It replaces both Hosted Agent endpoint and
image pins with non-routable placeholders.

Verify the archive checksum before extraction, then verify every extracted file:

```bash
shasum -a 256 -c agent-memory-rag-global-admin-*.zip.sha256
unzip agent-memory-rag-global-admin-*.zip
cd agent-memory-rag-global-admin-*/
shasum -a 256 -c SHA256SUMS
```

On Linux, use `sha256sum -c` instead of `shasum -a 256 -c`.

Stop if either check fails.

## 3. Required inputs

Copy the template and complete it before starting:

```bash
cp global-admin-inputs.env.example global-admin-inputs.env
chmod 600 global-admin-inputs.env
```

The file contains no password, token, client secret, or Terraform state.

### 3.1 Inputs required before any change

| Input | Requirement or source |
| --- | --- |
| `TARGET_TENANT_ID` | Target Microsoft Entra tenant GUID |
| `TARGET_SUBSCRIPTION_ID` | Target Azure subscription GUID in that tenant |
| `DEPLOYER_OBJECT_ID` | Object ID of the Global Administrator user; may initially be blank and then recorded from `az ad signed-in-user show` |
| `LOCATION` | Region approved for Foundry, Hosted Agents, Container Apps, and all selected models |
| `SEARCH_LOCATION` | Region with approved Azure AI Search capacity |
| `RESOURCE_GROUP` | New or approved target resource group name |
| `NAME_PREFIX` | Lowercase alphanumeric resource prefix |
| `ENVIRONMENT_NAME` | Short target environment identifier |
| `APP_NAME` | Unique target-tenant Entra application name |
| `TAG_ENVIRONMENT`, `TAG_OWNER` | Required target tags |
| `VNET_ADDRESS_SPACE` | Non-overlapping CIDR; default design is `10.42.0.0/16` |
| `DIRECTIVE_MODEL_MODE` | `fresh` for a new model deployment or `adopt` for the exact existing target deployment |
| `DIRECTIVE_MODEL_IMPORT_ID` | Full existing deployment ARM resource ID in `adopt` mode; empty in `fresh` mode |
| Model names, versions, capacities | Approved combinations and quota for chat, embedding, and directive models |
| `SEARCH_SKU` | Approved Search SKU |
| `DIRECTIVE_STORAGE_REPLICATION_TYPE` | Approved redundancy: `LRS`, `ZRS`, `GRS`, `GZRS`, `RAGRS`, or `RAGZRS` |
| `AGENT365_ENABLED` | `true` only when Agent 365 telemetry, service principal, and licensing are in scope |
| `DEPLOYMENT_RUN_ID` | Unique 3-80 character audit identifier using letters, digits, `.`, `_`, or `-` |
| `SECURE_EVIDENCE_DIR` | Unique absolute directory for this deployment run in approved encrypted storage; its parent must already exist |
| Residual access approval | Explicit acceptance of the Terraform-managed deployer roles listed in section 1 |

### 3.2 Inputs required later

| Gate | Input |
| --- | --- |
| After Entra bootstrap | `ENTRA_CLIENT_ID`, `ENTRA_API_SCOPE` |
| Before source upload | Approved target users/groups for `DirectiveSource.Manage`; group assignment requires Entra ID P1 or P2 |
| Before Agent 365 role assignment | Licensed user UPN and confirmed Agent 365 service principal, only when enabled |
| Before Terraform | Unique Prompt Agent and Directive Agent release IDs |
| Before application release | Unique app, support-agent, directive-agent, and ingestion image/release tags |
| Before Hosted Agent release | Unique support and directive `azd` environment names |
| After Hosted Agent release | Support instance, directive instance, and shared project Agent Identity principal IDs |
| Before ingestion | Approved target PDF source set |

### 3.3 Hard preflight decisions

Do not start Terraform until all are true:

- The subscription is in the target tenant.
- Target policy permits the documented public Entra/RBAC-only Foundry, Search,
  and ACR paths and the private endpoint topology for the other services.
- All model versions and deployment types are available with capacities 30, 30,
  and 250, or approved replacements have passed application revalidation.
- Hosted Agents are supported in `LOCATION`.
- Search has capacity in `SEARCH_LOCATION`.
- The VNet CIDR does not overlap a connected network.
- `fresh` or `adopt` has been selected for the directive model.
- An isolated Terraform state location has been approved.
- The Global Administrator's residual IaC-managed data roles are accepted.

## 4. Exact execution order

| Phase | Action | Exit gate |
| --- | --- | --- |
| 0 | Verify package and complete inputs | Checksums pass; no placeholder decision remains |
| 1 | Bootstrap the Entra SPA/API app | Tenant ID, client ID, and API scope recorded |
| 2 | Temporarily elevate at root and establish subscription roles | Direct Contributor and User Access Administrator exist; root toggle returned to No |
| 3 | Install tools, authenticate `azd`, register providers, verify quota/policy | All providers registered and all portability gates approved |
| 4 | Select fresh/adopt mode and create target `terraform.tfvars` | No source value or rollout flag is present |
| 5 | Initialize, validate, plan, review, and apply Terraform | Saved plan approved and infrastructure apply succeeds |
| 6 | Replace localhost redirect with production URL and assign source operators | Production sign-in and `DirectiveSource.Manage` assignment work |
| 7 | Release Search, Foundry IQ, and Prompt Agent assets | Release script succeeds while agents remain disabled |
| 8 | Create target `azd` environments and pin the target Foundry endpoint | Both environments contain only target values |
| 9 | Build immutable images and roll frontend/backend | Target revisions are healthy |
| 10 | Upload approved PDFs and run directive ingestion | Publication and verification succeed |
| 11 | Deploy both Hosted Agents and assign application roles | Instance and project identities have `AgentTools.Invoke`; optional Agent 365 role succeeds or is explicitly skipped |
| 12 | Apply backend identity allowlists | Both agent identity sets are present before exposure |
| 13 | Enable Prompt, support Hosted, and Directive agents in stages | Each stage passes before the next |
| 14 | Run acceptance and drift validation | Runtime acceptance passes and Terraform reports no drift |
| 15 | Remove temporary/manual access and deactivate elevation | Root toggle is No; script-created Contributor then User Access Administrator are removed |

## 5. Phase 0 - open a controlled shell

Use Azure Cloud Shell (Bash) or a controlled Linux/macOS workstation. Required
tools:

- Terraform 1.6 or later;
- Azure CLI 2.80 or later;
- Azure Developer CLI (`azd`);
- Azure CLI `containerapp` extension;
- `azure.ai.agents` azd extension;
- Bash, `jq`, `uuidgen`, Python 3.11 or later, Node.js 20 or later.

From the extracted package root:

```bash
set -a
source ./global-admin-inputs.env
set +a

export AZURE_CONFIG_DIR
export COPILOT_HOME

./scripts/validate_global_admin_inputs.sh \
  bootstrap \
  ./global-admin-inputs.env

umask 077
if [[ ! -d "$SECURE_EVIDENCE_DIR" ]]; then
  mkdir "$SECURE_EVIDENCE_DIR"
  (
    set -o noclobber
    printf '%s\n' "$DEPLOYMENT_RUN_ID" \
      >"$SECURE_EVIDENCE_DIR/.agent-memory-rag-deployment-id"
  )
fi
test -d "$SECURE_EVIDENCE_DIR"
test -w "$SECURE_EVIDENCE_DIR"
test "$(
  cat "$SECURE_EVIDENCE_DIR/.agent-memory-rag-deployment-id"
)" = "$DEPLOYMENT_RUN_ID"

terraform version
az version
azd version
python3 --version
node --version
jq --version
```

Install or update the extensions:

```bash
az extension add --name containerapp --upgrade
azd extension install azure.ai.agents
```

## 6. Phase 1 - Entra bootstrap

Activate Global Administrator through PIM if the role is eligible. Sign in to
the target directory; Azure subscription access is not required yet:

```bash
az login \
  --tenant "$TARGET_TENANT_ID" \
  --allow-no-subscriptions

test "$(
  az account show --query tenantId --output tsv
)" = "$TARGET_TENANT_ID"

DEPLOYER_OBJECT_ID="$(
  az ad signed-in-user show --query id --output tsv
)"
printf 'DEPLOYER_OBJECT_ID=%s\n' "$DEPLOYER_OBJECT_ID"
```

Record `DEPLOYER_OBJECT_ID` in `global-admin-inputs.env`.

Create the single-tenant SPA/API app with temporary localhost redirects:

```bash
./scripts/create_entra_app.sh \
  --name "$APP_NAME" \
  --frontend-url http://localhost:5175
```

If the uniquely named app already exists, stop, verify it, and rerun with its
explicit client ID:

```bash
./scripts/create_entra_app.sh \
  --name "$APP_NAME" \
  --app-id "<verified-application-client-id>" \
  --frontend-url http://localhost:5175
```

Record:

```text
ENTRA_CLIENT_ID=<application client ID>
ENTRA_API_SCOPE=api://<application client ID>/access_as_user
```

Update `global-admin-inputs.env`, then validate the next gate:

```bash
set -a
source ./global-admin-inputs.env
set +a

./scripts/validate_global_admin_inputs.sh \
  terraform \
  ./global-admin-inputs.env
```

The helper creates no client secret. It creates:

- delegated scope `access_as_user`;
- application role `AgentTools.Invoke`;
- user role `DirectiveSource.Manage`;
- v2 access tokens;
- SPA redirect URIs;
- Azure CLI preauthorization for test-token acquisition;
- the enterprise application service principal.

Grant tenant-wide consent only when target tenant consent policy requires it.

## 7. Phase 2 - establish temporary Azure access

In the Azure portal, as the same user:

1. Open **Microsoft Entra ID > Manage > Properties**.
2. Set **Access management for Azure resources** to **Yes** and save.
3. Sign out and sign back in so the root-scope assignment is in the token.

The toggle grants this user User Access Administrator at root scope (`/`).
It is not a general Global Administrator entitlement.

Refresh the CLI session and select the exact subscription:

```bash
az logout
az login --tenant "$TARGET_TENANT_ID"
az account set --subscription "$TARGET_SUBSCRIPTION_ID"

./scripts/global_admin_access.sh context \
  --inputs ./global-admin-inputs.env
```

Create and record the direct subscription assignments:

```bash
./scripts/global_admin_access.sh grant \
  --inputs ./global-admin-inputs.env \
  --state ./global-admin-access-state.json
```

Keep `global-admin-access-state.json`. It records whether each assignment was
created by the helper; cleanup never removes a pre-existing assignment.
If the grant command fails, keep root elevation active and run the cleanup
command with that state file. Then archive the cleaned failure record before
attempting the grant again:

```bash
./scripts/global_admin_access.sh cleanup \
  --inputs ./global-admin-inputs.env \
  --state ./global-admin-access-state.json

./scripts/archive_deployment_evidence.sh \
  --inputs ./global-admin-inputs.env \
  --name "global-admin-access-state.failed-$(date -u +%Y%m%dT%H%M%SZ).json" \
  ./global-admin-access-state.json
```

Immediately return to **Microsoft Entra ID > Manage > Properties**, set
**Access management for Azure resources** to **No**, save, and refresh login:

```bash
az logout
az login --tenant "$TARGET_TENANT_ID"
az account set --subscription "$TARGET_SUBSCRIPTION_ID"

./scripts/global_admin_access.sh status \
  --inputs ./global-admin-inputs.env \
  --state ./global-admin-access-state.json
```

Stop unless the target tenant/subscription and direct subscription roles are
correct and the root toggle is back to No.

Authenticate `azd` separately:

```bash
azd auth login --tenant-id "$TARGET_TENANT_ID"
azd auth status
```

## 8. Phase 3 - providers, quota, policy, and topology

Register required providers:

```bash
./scripts/register_providers.sh --include-quota
```

Review subscription and inherited policy:

```bash
az policy assignment list \
  --scope "/subscriptions/$TARGET_SUBSCRIPTION_ID" \
  --disable-scope-strict-match \
  --output table
```

Confirm the hard preflight decisions in section 3.3. Do not silently change a
model name, version, deployment type, capacity, Search region, or network CIDR.

## 9. Phase 4 - target configuration

Select exactly one directive-model path without modifying source files:

- `fresh` leaves `DIRECTIVE_MODEL_IMPORT_ID` empty and lets Terraform create
  the deployment.
- `adopt` records the exact existing deployment ARM resource ID in
  `DIRECTIVE_MODEL_IMPORT_ID`. Import it after Terraform initialization and
  before the first plan.

Create the target variables:

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
chmod 600 infra/terraform.tfvars
```

Set at least:

```hcl
subscription_id     = "<TARGET_SUBSCRIPTION_ID>"
location            = "<LOCATION>"
search_location     = "<SEARCH_LOCATION>"
resource_group_name = "<RESOURCE_GROUP>"
name_prefix         = "<NAME_PREFIX>"

entra_tenant_id = "<TARGET_TENANT_ID>"
entra_client_id = "<ENTRA_CLIENT_ID>"

tags = {
  project = "agent-memory-rag"
  env     = "<TAG_ENVIRONMENT>"
  owner   = "<TAG_OWNER>"
}

vnet_address_space = "<VNET_ADDRESS_SPACE>"

chat_model_name         = "<CHAT_MODEL_NAME>"
chat_model_version      = "<CHAT_MODEL_VERSION>"
chat_model_sku          = "<CHAT_MODEL_SKU>"
chat_model_capacity     = <CHAT_MODEL_CAPACITY>
embedding_model_name    = "<EMBEDDING_MODEL_NAME>"
embedding_model_version = "<EMBEDDING_MODEL_VERSION>"
embedding_model_sku      = "<EMBEDDING_MODEL_SKU>"
embedding_model_capacity = <EMBEDDING_MODEL_CAPACITY>
directive_model_name     = "<DIRECTIVE_MODEL_NAME>"
directive_model_version  = "<DIRECTIVE_MODEL_VERSION>"
directive_model_sku      = "<DIRECTIVE_MODEL_SKU>"
directive_model_capacity = <DIRECTIVE_MODEL_CAPACITY>
search_sku                = "<SEARCH_SKU>"
directive_storage_replication_type = "<DIRECTIVE_STORAGE_REPLICATION_TYPE>"

agent_release_id           = "<unique-prompt-agent-release>"
directive_agent_release_id = "<unique-directive-agent-release>"

foundry_prompt_enabled  = false
foundry_hosted_enabled  = false
directive_agent_enabled = false
directive_agent_visible = false

hosted_agent_principal_ids           = []
support_hosted_agent_principal_ids   = []
directive_hosted_agent_principal_ids = []
```

Use the approved numeric capacities rather than blindly copying the example.
Keep all four exposure flags false.

Confirm the package has no prior state:

```bash
test ! -e infra/terraform.tfstate
test ! -e infra/.terraform
test ! -e infra/tfplan
```

## 10. Phase 5 - Terraform

Initialize and validate:

```bash
terraform -chdir=infra init -reconfigure
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra validate
```

In `adopt` mode, import the recorded deployment before planning:

```bash
if [[ "$DIRECTIVE_MODEL_MODE" == adopt ]]; then
  ./scripts/import_directive_model.sh "$DIRECTIVE_MODEL_IMPORT_ID"
fi
```

Create a saved plan and reviewable text:

```bash
PLAN_FILE="target-${TARGET_SUBSCRIPTION_ID}.tfplan"

terraform -chdir=infra plan \
  -input=false \
  -out="$PLAN_FILE"

terraform -chdir=infra show \
  -no-color \
  "$PLAN_FILE" >"infra/${PLAN_FILE}.txt"
```

Before apply, confirm:

- every resource ID is in the target subscription;
- every Entra ID is from the target tenant;
- no source endpoint, resource name, image, or principal appears;
- no unrelated resource is destroyed or replaced;
- the signed-in Global Administrator is the principal on every `deployer_*`
  resource;
- the plan creates the managed-identity assignments and custom Foundry
  consumer role;
- model and Search regions match the approved capacity decision;
- the selected fresh/adopt behavior is exactly as approved.

Apply only the reviewed saved plan:

```bash
terraform -chdir=infra apply "$PLAN_FILE"
```

Saved Terraform plans and their rendered text can contain prior state and
sensitive values. Move them out of the extracted package immediately after
apply:

```bash
./scripts/archive_deployment_evidence.sh \
  --inputs ./global-admin-inputs.env \
  "infra/$PLAN_FILE" \
  "infra/${PLAN_FILE}.txt"
```

Protect `infra/terraform.tfstate` and every
`infra/terraform.tfstate.backup` as deployment state. Do not email them or place
them in the final general-purpose handoff. Keep them protected for the remaining
applies, then move every `infra/terraform.tfstate*` file to approved encrypted
or remote state custody before privilege cleanup.

Allow time for Azure RBAC and managed-identity propagation.

## 11. Phase 6 - production Entra configuration

Get the frontend name:

```bash
FRONTEND_FQDN="$(
  terraform -chdir=infra output -raw frontend_fqdn
)"
```

Replace the bootstrap redirect with the production origin:

```bash
./scripts/create_entra_app.sh \
  --name "$APP_NAME" \
  --app-id "$ENTRA_CLIENT_ID" \
  --frontend-url "https://${FRONTEND_FQDN}"
```

Add `--localhost` only when local sign-in remains approved.

In the Microsoft Entra admin center:

1. Open **Enterprise applications** and select `APP_NAME`.
2. Open **Users and groups**.
3. Assign the approved target user or group to **Manage directive sources**
   (`DirectiveSource.Manage`).
4. For this single-admin execution, assign the Global Administrator only if
   that user performs the source upload.

Group assignment requires Microsoft Entra ID P1 or P2 and does not expand nested
groups.

## 12. Phase 7 - Search, Foundry IQ, and Prompt assets

Terraform assigned the current user the required data roles. After propagation:

```bash
./scripts/release_foundry_assets.sh all
```

The script installs the local `directive_contracts` and `agent_contracts`
packages into its setup environment and checks both imports before release work.

Keep `foundry_prompt_enabled = false` until the release is verified.

Before continuing to image and agent releases, complete all release identifiers
and directive-source assignees in the input file:

```bash
set -a
source ./global-admin-inputs.env
set +a

./scripts/validate_global_admin_inputs.sh \
  release \
  ./global-admin-inputs.env
```

## 13. Phase 8 - target azd environments

Resolve Terraform outputs from the package root:

```bash
REPO_ROOT="$(pwd -P)"
tf() { terraform -chdir="$REPO_ROOT/infra" output -raw "$1"; }

AI_ACCOUNT_NAME="$(basename "$(tf foundry_agents_account_id)")"
AI_PROJECT_NAME="$(basename "$(tf foundry_agents_project_id)")"
PROJECT_ENDPOINT="$(tf foundry_agents_project_endpoint)"
APP_TOOLS_CONNECTION_ID="$(tf foundry_application_tools_connection_name)"
CHAT_DEPLOYMENT="$(tf chat_deployment)"
IQ_CONNECTION_ID="$(tf foundry_iq_connection_name)"
IQ_MCP_ENDPOINT="$(tf foundry_iq_mcp_endpoint)"
APP_TOOL_GATEWAY_URL="$(tf agent_tool_gateway_url)"
APP_TOOL_GATEWAY_SCOPE="$(tf agent_tool_gateway_scope)"
DIRECTIVE_MODEL_DEPLOYMENT="$(tf directive_model_deployment)"
DIRECTIVE_AGENT_RELEASE_ID="$(tf directive_agent_release_id)"
PROJECT_AGENT_PRINCIPAL_ID="$(tf foundry_agents_project_principal_id)"
```

Record `PROJECT_AGENT_PRINCIPAL_ID` in `global-admin-inputs.env`. The Hosted
Agent manifests read their project endpoint and immutable image reference from
each azd environment; they contain no committed target endpoint or image tag.

Create the support environment:

```bash
(
  cd "$REPO_ROOT/agents/customer-support-maf"
  azd env new "$SUPPORT_AZD_ENVIRONMENT"
  azd env set AZURE_SUBSCRIPTION_ID "$TARGET_SUBSCRIPTION_ID"
  azd env set AZURE_TENANT_ID "$TARGET_TENANT_ID"
  azd env set ENTRA_TENANT_ID "$TARGET_TENANT_ID"
  azd env set AZURE_RESOURCE_GROUP "$RESOURCE_GROUP"
  azd env set AZURE_LOCATION "$LOCATION"
  azd env set AZURE_AI_ACCOUNT_NAME "$AI_ACCOUNT_NAME"
  azd env set AZURE_AI_PROJECT_NAME "$AI_PROJECT_NAME"
  azd env set FOUNDRY_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
  azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "$CHAT_DEPLOYMENT"
  azd env set IQ_CONNECTION_ID "$IQ_CONNECTION_ID"
  azd env set IQ_MCP_ENDPOINT "$IQ_MCP_ENDPOINT"
  azd env set APP_TOOL_GATEWAY_URL "$APP_TOOL_GATEWAY_URL"
  azd env set APP_TOOL_GATEWAY_SCOPE "$APP_TOOL_GATEWAY_SCOPE"
  azd env set APP_TOOLS_CONNECTION_ID "$APP_TOOLS_CONNECTION_ID"
)
```

Create the directive environment:

```bash
(
  cd "$REPO_ROOT/agents/directive-rag-maf"
  azd env new "$DIRECTIVE_AZD_ENVIRONMENT"
  azd env set AZURE_SUBSCRIPTION_ID "$TARGET_SUBSCRIPTION_ID"
  azd env set AZURE_TENANT_ID "$TARGET_TENANT_ID"
  azd env set ENTRA_TENANT_ID "$TARGET_TENANT_ID"
  azd env set AZURE_RESOURCE_GROUP "$RESOURCE_GROUP"
  azd env set AZURE_LOCATION "$LOCATION"
  azd env set AZURE_AI_ACCOUNT_NAME "$AI_ACCOUNT_NAME"
  azd env set AZURE_AI_PROJECT_NAME "$AI_PROJECT_NAME"
  azd env set FOUNDRY_PROJECT_ENDPOINT "$PROJECT_ENDPOINT"
  azd env set DIRECTIVE_MODEL_DEPLOYMENT "$DIRECTIVE_MODEL_DEPLOYMENT"
  azd env set DIRECTIVE_MAX_ITERATIONS 12
  azd env set DIRECTIVE_AGENT_RELEASE_ID "$DIRECTIVE_AGENT_RELEASE_ID"
  azd env set APP_TOOL_GATEWAY_URL "$APP_TOOL_GATEWAY_URL"
  azd env set APP_TOOL_GATEWAY_SCOPE "$APP_TOOL_GATEWAY_SCOPE"
)
```

Inspect both environments and stop if any value belongs to another tenant,
subscription, registry, or Foundry project.

## 14. Phase 9 - build and roll images

Use new immutable tags:

```bash
./scripts/deploy_images.sh "$APP_IMAGE_TAG" \
  --support-agent-tag "$SUPPORT_IMAGE_TAG" \
  --with-directive \
  --directive-agent-tag "$DIRECTIVE_IMAGE_TAG"
```

The script builds all images through ACR Tasks, updates only the backend and
frontend Container Apps, and replaces the placeholder Hosted Agent images with
target ACR images.

If ACR authorization is denied, stop and inspect the registry permission mode.
Assign only the missing role to this same user and record the role assignment ID
for cleanup:

- `Container Registry Tasks Contributor` for ACR Tasks;
- `AcrPush` for classic RBAC Registry Permissions; or
- `Container Registry Repository Writer` and
  `Container Registry Repository Catalog Lister` for RBAC Registry + ABAC.

Do not add an ACR admin credential or anonymous pull.

Confirm frontend and backend readiness before continuing.

## 15. Phase 10 - source upload and directive ingestion

Sign in to the production frontend as the user assigned
`DirectiveSource.Manage`. Upload only approved PDFs through **Sources**. Upload
does not start ingestion.

Run the managed-identity preflight, publication, and verification:

```bash
cp setup/directives/mandatory/mand.csv.example \
  setup/directives/mandatory/mand.csv
# Replace every sample identity with target-tenant assignments.
./scripts/deploy_directive_ingestion.sh "$INGESTION_RELEASE_ID"
```

`mand.csv` is ignored by Git and must never be included in a source snapshot.
An empty assignment snapshot requires the explicit
`ALLOW_EMPTY_DIRECTIVE_MANDATES=true` override.

Do not enable the Directive Assistant unless this command reports successful
publication and verification.

## 16. Phase 11 - Hosted Agents and app roles

Deploy support:

```bash
(
  cd agents/customer-support-maf
  azd ai agent doctor
  azd deploy --no-prompt
  azd ai agent show --output json
)
```

Deploy directive:

```bash
(
  cd agents/directive-rag-maf
  azd ai agent doctor
  azd deploy --no-prompt
  azd ai agent show --output json
)
```

Record each `instance_identity.principal_id`. Wait for both service principals
to propagate in Entra.

Update the two published instance principal IDs in the input file, confirm the
previously recorded project principal ID, reload the shell, and validate:

```bash
set -a
source ./global-admin-inputs.env
set +a

./scripts/validate_global_admin_inputs.sh \
  agent-roles \
  ./global-admin-inputs.env
```

When Agent 365 is enabled, first verify its service principal and assign the
required Microsoft 365 E7 or Microsoft Agent 365 license to at least one target
tenant user.

Build the optional flag:

```bash
AGENT365_ARGS=()
if [[ "$AGENT365_ENABLED" != "true" ]]; then
  AGENT365_ARGS+=(--skip-agent365)
fi
```

Assign support roles:

```bash
./scripts/assign_hosted_agent_access.sh \
  --principal-id "$SUPPORT_AGENT_PRINCIPAL_ID" \
  --api-app-id "$ENTRA_CLIENT_ID" \
  --azd-project-dir agents/customer-support-maf \
  --agent-type support \
  "${AGENT365_ARGS[@]}"
```

Assign directive roles:

```bash
./scripts/assign_hosted_agent_access.sh \
  --principal-id "$DIRECTIVE_AGENT_PRINCIPAL_ID" \
  --api-app-id "$ENTRA_CLIENT_ID" \
  --azd-project-dir agents/directive-rag-maf \
  --agent-type directive \
  --no-app-tools-connection \
  "${AGENT365_ARGS[@]}"
```

The helper assigns `AgentTools.Invoke` to each published instance and the shared
project Agent Identity. It assigns `Agent365.Observability.OtelWrite` only when
Agent 365 was not explicitly skipped.

Record the published and shared project principal IDs printed by the helper.

## 17. Phase 12 - backend identity allowlists

Add target identities to `infra/terraform.tfvars`:

```hcl
support_hosted_agent_principal_ids = [
  "<support-agent-principal-id>",
  "<project-agent-principal-id>",
]

directive_hosted_agent_principal_ids = [
  "<directive-agent-principal-id>",
  "<project-agent-principal-id>",
]
```

Apply before exposing either Hosted Agent:

```bash
terraform -chdir=infra plan \
  -input=false \
  -out=agent-allowlists.tfplan
terraform -chdir=infra show \
  -no-color \
  agent-allowlists.tfplan >infra/agent-allowlists.tfplan.txt
terraform -chdir=infra apply agent-allowlists.tfplan

./scripts/archive_deployment_evidence.sh \
  --inputs ./global-admin-inputs.env \
  infra/agent-allowlists.tfplan \
  infra/agent-allowlists.tfplan.txt
```

## 18. Phase 13 - staged enablement

Use a separate reviewed plan for each gate.

1. Set only `foundry_prompt_enabled = true`; plan, apply, and test Prompt Agent.
2. Set `foundry_hosted_enabled = true`; plan, apply, and test the support Hosted
   Agent including its app-only MCP call.
3. Set `directive_agent_enabled = true` while
   `directive_agent_visible = false`; plan, apply, and test directive runtime.
4. Set `directive_agent_visible = true`; plan, apply, and test the user-visible
   Directive Assistant.

Never enable the next component after a failed gate.
For every staged saved plan, render it with `terraform show -no-color`, apply
only that reviewed plan, then archive both the binary plan and rendered text
with `scripts/archive_deployment_evidence.sh` before starting the next gate.

## 19. Phase 14 - acceptance

### Infrastructure and drift

```bash
terraform -chdir=infra plan -input=false -detailed-exitcode
```

Exit `0` means no drift. Exit `2` means unreviewed changes exist. Any destroy or
replacement requires separate approval.

### Public application

```bash
FRONTEND_URL="https://$(
  terraform -chdir=infra output -raw frontend_fqdn
)"

curl --fail --silent --show-error "$FRONTEND_URL/"
curl --fail --silent --show-error "$FRONTEND_URL/config.js"
curl --fail --silent --show-error "$FRONTEND_URL/api/health/live"
curl --fail --silent --show-error "$FRONTEND_URL/api/health/ready"
```

`config.js` must contain only target tenant/client values.

### Entra

```bash
TOKEN="$(
  az account get-access-token \
    --scope "$ENTRA_API_SCOPE" \
    --query accessToken \
    --output tsv
)"

curl --fail \
  -H "Authorization: Bearer $TOKEN" \
  "$FRONTEND_URL/api/me"

unset TOKEN
```

The assigned user must receive `can_manage_directive_sources: true`; an
unassigned user must not.

### Runtime

Verify:

- all readiness dependencies are `ok`;
- each enabled agent appears in `/api/agents`;
- Prompt and support Hosted requests complete;
- Hosted Agent MCP calls succeed with app-only `AgentTools.Invoke`;
- directive retrieval returns grounded references;
- unauthorized source and document requests return 401 or 403;
- active Container Apps revisions use the intended immutable tags;
- both `azd ai agent show` commands report active target-tenant versions.

Retain plan text, apply result, release IDs, revision names, agent principal IDs,
and acceptance evidence.

## 20. Phase 15 - privilege cleanup

Complete all Terraform role-assignment changes first. Remove any temporary ACR
roles created manually, using their recorded assignment IDs.

If the Global Administrator no longer needs source management, remove that
user's `DirectiveSource.Manage` enterprise-app assignment.

Confirm **Access management for Azure resources** is already **No** for the same
Global Administrator.

Reload the final input values, move every local state artifact to secure
custody, and confirm no state or saved plan remains in the package:

```bash
set -a
source ./global-admin-inputs.env
set +a

while IFS= read -r -d '' state_file; do
  ./scripts/archive_deployment_evidence.sh \
    --inputs ./global-admin-inputs.env \
    "$state_file"
done < <(
  find infra -type f \
    -name 'terraform.tfstate*' \
    -print0
)

rm -rf -- infra/.terraform

if find infra \
  -type f \
  \( -name '*.tfplan' -o -name '*.tfplan.txt' -o -name 'terraform.tfstate*' \) \
  -print | grep -q .; then
  echo "ERROR: move sensitive Terraform state and plans to secure custody first" >&2
  exit 1
fi
test ! -d infra/.terraform
```

Remove only the two subscription roles created by the package helper:

```bash
./scripts/global_admin_access.sh cleanup \
  --inputs ./global-admin-inputs.env \
  --state ./global-admin-access-state.json
```

The helper:

1. verifies tenant, subscription, and signed-in object ID against the state;
2. removes script-created Contributor first;
3. stops on failure before touching User Access Administrator;
4. removes script-created User Access Administrator last;
5. leaves every pre-existing assignment unchanged.

Do not lose the state file before cleanup. If cleanup cannot prove assignment
ownership, stop and review manually.

Archive the completed access record:

```bash
./scripts/archive_deployment_evidence.sh \
  --inputs ./global-admin-inputs.env \
  --name "global-admin-access-state.final-$(date -u +%Y%m%dT%H%M%SZ).json" \
  ./global-admin-access-state.json
```

Finally:

1. Verify the root elevation toggle is **No**.
2. Deactivate Global Administrator in PIM.
3. Confirm Terraform state and target configuration are in approved operational
   custody.
4. Retain the access-state JSON, package checksums, and deployment evidence for
   audit.

## 21. Stop conditions

Stop immediately when:

- a checksum fails;
- tenant, subscription, or signed-in object ID differs from the input;
- root elevation cannot be returned to No after subscription roles are created;
- required provider, quota, regional capacity, or policy approval is missing;
- Terraform references another tenant/subscription or proposes an unexpected
  destroy/replacement;
- the selected directive-model import behavior is wrong;
- a Hosted Agent manifest still contains a placeholder or source endpoint/image;
- Agent 365 is requested but its service principal/role/license is unavailable;
- the access-state file is missing or does not match cleanup context.

Do not convert a failed gate into a success-shaped workaround.

## 22. Official references

- [Elevate Global Administrator access to Azure resources](https://learn.microsoft.com/azure/role-based-access-control/elevate-access-global-admin)
- [Steps to assign an Azure role](https://learn.microsoft.com/azure/role-based-access-control/role-assignments-steps)
- [Azure custom roles](https://learn.microsoft.com/azure/role-based-access-control/custom-roles)
- [Assign users and groups to an enterprise application](https://learn.microsoft.com/entra/identity/enterprise-apps/assign-user-or-group-access-portal)
- [Grant an app role to a service principal](https://learn.microsoft.com/graph/api/serviceprincipal-post-approleassignments)
- [Register Azure resource providers](https://learn.microsoft.com/azure/azure-resource-manager/management/resource-providers-and-types)
- [Deploy a Foundry Hosted Agent](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent)
- [Hosted Agent permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions)
- [Manage Azure OpenAI quota](https://learn.microsoft.com/azure/foundry/openai/how-to/quota)
- [Azure Container Registry roles](https://learn.microsoft.com/azure/container-registry/container-registry-rbac-built-in-roles-overview)
