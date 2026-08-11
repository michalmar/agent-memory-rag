# Cross-tenant Azure deployment

This runbook deploys a new, isolated instance of this solution into another
Microsoft Entra tenant and Azure subscription.

For a self-contained handoff where one target-tenant Global Administrator runs
the complete deployment without repository access, use
[`GLOBAL-ADMIN-CROSS-TENANT-PLAN.md`](GLOBAL-ADMIN-CROSS-TENANT-PLAN.md) and the
archive produced by `scripts/build_global_admin_package.sh`.

The application operator is assumed to have **Contributor** on the target
subscription. Contributor is enough for most Azure resources and application
updates, but it is not enough for the complete deployment.

> **Important:** Microsoft Entra roles and Azure RBAC roles are independent.
> A Global Administrator is not automatically an Azure subscription Owner, and
> an Azure Contributor cannot manage the tenant's app registrations.

## 1. Required actors

Use separate identities or Privileged Identity Management activations for these
lanes.

| Lane | Required role | Why it is needed |
| --- | --- | --- |
| Application operator | **Contributor** on the target subscription, plus temporary **User Access Administrator** during authorization-bearing Terraform applies | Creates and updates Azure resources and runs releases; the temporary role lets this same identity create the IaC-managed role assignments and custom role |
| Azure authorization administrator | **User Access Administrator** or **Owner** on the target subscription | Grants and removes the operator's temporary elevation and handles any exceptional Azure RBAC assignments |
| Microsoft Entra application administrator | **Cloud Application Administrator**, **Application Administrator**, or **Global Administrator** | Creates the SPA/API app, service principal, app roles, redirect URIs, and Hosted Agent app-role assignments |
| License administrator, optional | **License Administrator** or **Global Administrator** | Assigns the Microsoft 365 E7 or Microsoft Agent 365 license required for Agent 365 telemetry |

`Role Based Access Control Administrator` is not sufficient for the initial
Terraform apply by itself. It can write role assignments, but this repository
also creates `azurerm_role_definition.backend_foundry_agent_consumer`, which
requires `Microsoft.Authorization/roleDefinitions/write`. User Access
Administrator and Owner include that permission.

The recommended deployment model is:

1. The Entra administrator creates the app registration and returns its client
   ID.
2. The Azure authorization administrator temporarily adds User Access
   Administrator to the Contributor operator.
3. The **operator runs Terraform under the operator's own identity**.
4. The Entra administrator completes directory app-role assignments.
5. The operator deploys the applications and data.
6. The temporary User Access Administrator assignment is removed.

Running Terraform as a different administrator is not equivalent. Several
resources use `data.azurerm_client_config.current.object_id`; therefore, the
identity that runs Terraform receives the Search, Foundry, model, Blob,
Document Intelligence, and Cosmos deployment-time data roles.

If policy does not allow temporary User Access Administrator, refactor the IaC
to accept an explicit deployer object ID and split Azure authorization resources
from the base infrastructure before attempting this runbook.

## 2. What is deployed

Terraform in `infra/` creates:

- a resource group, virtual network, delegated Container Apps subnet, private
  endpoint subnet, and private DNS zones;
- Azure Container Apps for the public frontend and internal FastAPI backend;
- an Azure Container Apps Job for directive ingestion;
- Azure Container Registry Premium;
- a Microsoft Foundry account, project, model deployments, project
  connections, and monitoring connection;
- Azure AI Search;
- private, Entra-only Cosmos DB and Blob Storage resources;
- private Document Intelligence and Azure Monitor paths;
- application, frontend, and ingestion user-assigned managed identities;
- Azure RBAC, Cosmos data-plane RBAC, and the custom Foundry consumer role.

Application release scripts then deploy:

- the frontend and backend images;
- the directive ingestion image and publication job;
- Search indexes, Foundry IQ, and the native Prompt Agent;
- the support and directive Microsoft Agent Framework Hosted Agents.

## 3. Cross-tenant portability gates

Resolve every item in this section before the first target-tenant plan.

### 3.1 Use isolated Terraform state

The repository currently uses local Terraform state. Never run a target-tenant
plan from a checkout containing state for another tenant.

Use a fresh clone or an organization-approved remote backend with a unique
state key. Do not reuse any of these files from the source environment:

- `infra/terraform.tfstate*`
- `infra/.terraform/`
- `infra/terraform.tfvars`
- `infra/tfplan` or any other saved plan

The checked-in `infra/tfplan` is not a deployment input for the new tenant.
Always create and review a new plan.

`.azure/deployment-plan.md` is a historical source-environment record and
contains source subscription and image values. Do not treat it as target
configuration or replay its commands without replacing and revalidating every
environment-specific value.

### 3.2 Decide how the directive model is created

`infra/directive_data.tf` contains an unconditional import block for
`azurerm_cognitive_deployment.directive`. It assumes the directive model
deployment already exists in the original subscription.

Choose the applicable path before planning:

- **Fresh deployment:** remove only that import block in the target deployment
  branch and keep the `azurerm_cognitive_deployment.directive` resource so
  Terraform creates the deployment. This is the expected path for an empty
  target subscription.
- **Adoption:** retain the import block only when the exact target Foundry
  account and directive deployment already exist at the generated import ID.

The model defaults are target-sensitive:

- `gpt-4o-mini`, version `2024-07-18`, capacity 30;
- `text-embedding-3-large`, version `1`, capacity 30;
- `gpt-5.6-sol`, version `2026-07-09`, Global Standard capacity 250.

Verify model availability and quota in the target subscription and region. Do
not silently substitute a model or version; the directive behavior must be
revalidated if it changes.

### 3.3 Replace target-specific Hosted Agent manifest values

Both Hosted Agent manifests contain the source environment's concrete Foundry
endpoint and image:

- `agents/customer-support-maf/azure.yaml`
- `agents/directive-rag-maf/azure.yaml`

Before `azd deploy`:

1. Change `services.ai-project.endpoint` in both files to the target value from
   `terraform -chdir=infra output -raw foundry_agents_project_endpoint`.
2. Run `scripts/build_hosted_agent_image.sh --configure-azd`, directly or
   through `scripts/deploy_images.sh`; it replaces the manifest image with the
   target ACR image.
3. Use a distinct `azd` environment for each target tenant.

Do not commit target-specific endpoint or image pins to a shared branch unless
that branch intentionally represents one tenant.

### 3.4 Confirm architecture-policy compatibility

This design intentionally uses:

- public, Entra-only Foundry and Azure AI Search endpoints;
- a public, Entra/RBAC-only ACR endpoint for non-VNet-injected Hosted Agents;
- private endpoints for Cosmos DB, directive Blob Storage, Document
  Intelligence, ACR access from Container Apps, and Azure Monitor;
- Application Insights public ingestion for the Foundry platform path.

Confirm that target policies allow these exceptions. A policy that requires a
private endpoint for every AI/Search/ACR path is incompatible with the current
Hosted Agent and Foundry IQ topology.

## 4. Record target values

Run operator commands from the repository root unless a command says otherwise.

```bash
export AZURE_CONFIG_DIR="$HOME/.azure-365"
export COPILOT_HOME="$HOME/.copilot"

export TARGET_TENANT_ID="<target-tenant-guid>"
export TARGET_SUBSCRIPTION_ID="<target-subscription-guid>"
export LOCATION="<application-and-foundry-region>"
export SEARCH_LOCATION="<search-region>"
export RESOURCE_GROUP="rg-agent-memory-rag"
export NAME_PREFIX="agmem"
export ENVIRONMENT_NAME="<short-target-environment-name>"
export APP_NAME="agent-memory-rag-${ENVIRONMENT_NAME}"
```

The operator signs in and proves that both tenant and subscription are correct:

```bash
az login --tenant "$TARGET_TENANT_ID"
az account set --subscription "$TARGET_SUBSCRIPTION_ID"

az account show \
  --query '{tenantId:tenantId,subscriptionId:id,name:name,state:state}' \
  --output table

test "$(az account show --query tenantId --output tsv)" = "$TARGET_TENANT_ID"
test "$(az account show --query id --output tsv)" = "$TARGET_SUBSCRIPTION_ID"
```

Contributor must be assigned at subscription scope for the default path. A
resource-group-only assignment is not enough to create the resource group,
register providers, or create the subscription-scoped custom role.

## 5. Entra bootstrap - elevated directory lane

**Actor:** Cloud Application Administrator, Application Administrator, or
Global Administrator.

The first run needs a temporary localhost redirect because the Container Apps
frontend FQDN does not exist yet.

```bash
ENTRA_ADMIN_AZURE_CONFIG_DIR="$HOME/.azure-365-entra-admin"

AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" az login \
  --tenant "<target-tenant-guid>" \
  --allow-no-subscriptions

test "$(
  AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" \
    az account show --query tenantId --output tsv
)" = "<target-tenant-guid>"

AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" \
  ./scripts/create_entra_app.sh \
  --name "<environment-specific-app-name>" \
  --frontend-url http://localhost:5175
```

This bootstrap uses Microsoft Graph only. The Entra administrator does not need
Azure subscription access for this step. Its separate Azure CLI profile keeps
the operator's subscription session intact on a shared workstation.

The script creates a single-tenant SPA/API application and service principal,
then:

- exposes delegated scope `access_as_user`;
- defines application role `AgentTools.Invoke`;
- defines user role `DirectiveSource.Manage`;
- configures v2 access tokens;
- configures SPA redirect URIs;
- preauthorizes the Azure CLI for test-token acquisition.

It does not create a client secret. Record these outputs and return them to the
operator:

```text
ENTRA_TENANT_ID=<target tenant ID>
ENTRA_CLIENT_ID=<application client ID>
ENTRA_API_SCOPE=api://<application client ID>/access_as_user
```

Tenant-wide admin consent is not normally required because this app requests
only its own user-consentable delegated scope. If the target tenant disables
user consent or requires user assignment, an Entra administrator must grant
tenant-wide consent after reviewing the app.

## 6. Azure authorization bootstrap - elevated Azure lane

**Actor:** subscription Owner or User Access Administrator.

Get the operator's object ID from the operator:

```bash
az ad signed-in-user show --query id --output tsv
```

Grant the operator temporary User Access Administrator at subscription scope,
in addition to the existing Contributor assignment:

```bash
AZURE_AUTH_ADMIN_CONFIG_DIR="$HOME/.azure-365-azure-admin"

AZURE_CONFIG_DIR="$AZURE_AUTH_ADMIN_CONFIG_DIR" \
  az login --tenant "<target-tenant-guid>"
AZURE_CONFIG_DIR="$AZURE_AUTH_ADMIN_CONFIG_DIR" \
  az account set --subscription "<target-subscription-guid>"

AZURE_CONFIG_DIR="$AZURE_AUTH_ADMIN_CONFIG_DIR" \
  az role assignment create \
  --assignee-object-id "<operator-object-id>" \
  --assignee-principal-type User \
  --role "User Access Administrator" \
  --scope "/subscriptions/<target-subscription-guid>"
```

If the only available privileged identity is a Global Administrator with no
Azure resource access, that administrator must first use **Microsoft Entra ID >
Properties > Access management for Azure resources**. Enabling it grants the
signed-in Global Administrator User Access Administrator at root scope. Use it
only to make the required Azure role assignment, then turn it off immediately.
If the same identity must remove the operator's temporary assignment later,
reactivate root access only for that cleanup.

## 7. Contributor preflight

### 7.1 Install tools

Required tooling:

- Terraform 1.6 or later;
- Azure CLI 2.80 or later;
- the Azure CLI `containerapp` extension;
- Azure Developer CLI (`azd`);
- the `azure.ai.agents` azd extension required by the manifests;
- Bash, `jq`, `uuidgen`, Python 3.11, and Node.js 20 or later.

Install or update the Hosted Agent extension:

```bash
az extension add --name containerapp --upgrade
azd extension install azure.ai.agents
```

`azd` maintains its own authentication state. Authenticate it explicitly as the
application operator:

```bash
azd auth login --tenant-id "$TARGET_TENANT_ID"
azd auth status
```

### 7.2 Register resource providers

The AzureRM provider is configured with
`resource_provider_registrations = "none"`, so registration is explicit.
Contributor can register providers when it is assigned at subscription scope.

```bash
providers=(
  Microsoft.App
  Microsoft.Authorization
  Microsoft.CognitiveServices
  Microsoft.ContainerRegistry
  Microsoft.DocumentDB
  Microsoft.Insights
  Microsoft.ManagedIdentity
  Microsoft.Network
  Microsoft.OperationalInsights
  Microsoft.Search
  Microsoft.Storage
)

for provider in "${providers[@]}"; do
  az provider register --namespace "$provider" --wait
done
```

In the same shell, verify:

```bash
for provider in "${providers[@]}"; do
  az provider show \
    --namespace "$provider" \
    --query '{namespace:namespace,state:registrationState}' \
    --output table
done
```

Register `Microsoft.Quota` only when it is needed for programmatic quota
inspection.

### 7.3 Verify capacity and policy

Before Terraform:

- confirm all three model/version/deployment-type combinations are available;
- confirm TPM quota for capacities 30, 30, and 250;
- confirm Azure AI Search capacity in `SEARCH_LOCATION`;
- confirm Hosted Agents are available in the selected Foundry region;
- inspect subscription and inherited management-group policies;
- confirm the VNet CIDR `10.42.0.0/16` does not overlap connected networks.

Azure OpenAI quota is per subscription, region, model, and deployment type.
The minimal built-in role to view it is Cognitive Services Usages Reader at
subscription scope; subscription Reader also includes visibility.

## 8. Configure and deploy infrastructure

### 8.1 Create target-specific variables

Copy the example:

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

Set target values. Keep all agent exposure flags false for the initial
deployment:

```hcl
subscription_id    = "<target-subscription-guid>"
location           = "<application-and-foundry-region>"
search_location    = "<search-region>"
resource_group_name = "rg-agent-memory-rag"
name_prefix         = "agmem"

entra_tenant_id = "<target-tenant-guid>"
entra_client_id = "<target-app-client-guid>"

tags = {
  project = "agent-memory-rag"
  env     = "<environment>"
  owner   = "<target-owner>"
}

foundry_prompt_enabled = false
foundry_hosted_enabled = false
directive_agent_enabled = false
directive_agent_visible = false

hosted_agent_principal_ids           = []
support_hosted_agent_principal_ids   = []
directive_hosted_agent_principal_ids = []
```

Also override region-specific model names, versions, capacities, Search SKU,
storage replication, and VNet CIDR when the target design requires it.

### 8.2 Initialize, validate, and plan

Use only the new target state:

```bash
terraform -chdir=infra init -reconfigure
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra validate

PLAN_FILE="target-${TARGET_SUBSCRIPTION_ID}.tfplan"
terraform -chdir=infra plan \
  -input=false \
  -out="$PLAN_FILE"

terraform -chdir=infra show \
  -no-color \
  "$PLAN_FILE" > "infra/${PLAN_FILE}.txt"
```

Before apply, verify:

- every resource ID contains the target subscription;
- the plan contains no source-tenant IDs, endpoints, or resource names;
- no unrelated resource is destroyed or replaced;
- the target operator is the principal for every `deployer_*` resource;
- the plan includes the managed-identity assignments and custom Foundry
  consumer role;
- model and Search regions match the approved capacity decision.

Apply only the reviewed saved plan:

```bash
PLAN_FILE="target-${TARGET_SUBSCRIPTION_ID}.tfplan"
terraform -chdir=infra apply "$PLAN_FILE"
```

Allow time for Azure RBAC and Microsoft Entra managed-identity propagation.

## 9. Finalize the Entra application

### 9.1 Add the production redirect URI

**Actor:** Entra application administrator.

The operator supplies:

```bash
terraform -chdir=infra output -raw frontend_fqdn
```

The Entra administrator updates the explicitly selected app:

```bash
ENTRA_ADMIN_AZURE_CONFIG_DIR="$HOME/.azure-365-entra-admin"

AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" az login \
  --tenant "<target-tenant-guid>" \
  --allow-no-subscriptions

AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" \
  ./scripts/create_entra_app.sh \
  --name "<environment-specific-app-name>" \
  --app-id "<application-client-id>" \
  --frontend-url "https://<target-frontend-fqdn>" \
  --localhost
```

Omit `--localhost` when local sign-in is not permitted.

### 9.2 Assign the directive-source operator role

**Actor:** Cloud Application Administrator, Application Administrator, User
Administrator, service-principal owner, or Global Administrator.

In the Microsoft Entra admin center:

1. Open **Enterprise applications**.
2. Select the environment-specific application.
3. Open **Users and groups**.
4. Assign approved users or groups to **Manage directive sources**
   (`DirectiveSource.Manage`).

Group-based assignment requires Microsoft Entra ID P1 or P2. Use direct user
assignment if that license is unavailable.

## 10. Deploy application and data workloads

### 10.1 Release Search, Foundry IQ, and the Prompt Agent

Terraform assigns the operator the required Foundry, Search, model, and Cosmos
data roles. After propagation:

```bash
./scripts/release_foundry_assets.sh all
```

Keep `foundry_prompt_enabled = false` until release verification succeeds.

### 10.2 Initialize target-specific azd environments

Create and populate both target environments from the repository root. Resolve
every Terraform output before entering either agent directory:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
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

(
  cd "$REPO_ROOT/agents/customer-support-maf"
  azd env new "<support-target-environment>"
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

(
  cd "$REPO_ROOT/agents/directive-rag-maf"
  azd env new "<directive-target-environment>"
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

Update both `azure.yaml` project endpoints to `PROJECT_ENDPOINT` before the
next step.

### 10.3 Build and roll application images

Use new immutable tags:

```bash
APP_TAG="<unique-application-release>"
SUPPORT_TAG="<unique-support-agent-release>"
DIRECTIVE_TAG="<unique-directive-agent-release>"

./scripts/deploy_images.sh "$APP_TAG" \
  --support-agent-tag "$SUPPORT_TAG" \
  --with-directive \
  --directive-agent-tag "$DIRECTIVE_TAG"
```

The script:

- builds backend, frontend, support-agent, and directive-agent images with ACR
  Tasks;
- updates only the backend and frontend Container Apps;
- pins both Hosted Agent manifests and azd environments to the target ACR
  images.

If ACR authorization is denied, ask the Azure authorization administrator for
the narrow roles required by the registry's permission mode:

- `Container Registry Tasks Contributor` for `az acr build`;
- `AcrPush` for classic **RBAC Registry Permissions**, or **Container Registry
  Repository Writer** plus **Container Registry Repository Catalog Lister** for
  **RBAC Registry + ABAC Repository Permissions**.

### 10.4 Upload and ingest directive sources

After the frontend is healthy, sign in as a user assigned
`DirectiveSource.Manage` and upload the approved PDFs through the **Sources**
rail. Upload does not start ingestion.

Run the managed-identity preflight, ingestion, and verification:

```bash
./scripts/deploy_directive_ingestion.sh "<unique-ingestion-release>"
```

Do not enable the Directive Assistant until this script reports successful
publication and verification.

## 11. Deploy Hosted Agents

### 11.1 Deploy both agent versions

The operator needs Foundry Project Manager at project scope. Terraform assigns
the role to the identity that performed the Terraform apply.

```bash
(
  cd agents/customer-support-maf
  azd ai agent doctor
  azd deploy --no-prompt
  azd ai agent show --output json
)

(
  cd agents/directive-rag-maf
  azd ai agent doctor
  azd deploy --no-prompt
  azd ai agent show --output json
)
```

Record each agent's `instance_identity.principal_id`. If the azd output does not
include it, retrieve the Hosted Agent with the Foundry REST API and query
`instance_identity.principal_id`.

### 11.2 Assign Entra application roles to agent identities

**Actor:** Cloud Application Administrator, Application Administrator,
Privileged Role Administrator, Agent ID Administrator where applicable, or
Global Administrator.

The person running the repository helper also needs at least Azure Reader on
the target Foundry resource group because the script reads the project Agent
Identity from ARM. They must sign in to the target tenant and select the target
subscription before running the helper. Run it from the checkout containing the
azd environments created in section 10.2; on another workstation, recreate
those environment values first.

```bash
ENTRA_ADMIN_AZURE_CONFIG_DIR="$HOME/.azure-365-entra-admin"

AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" \
  az login --tenant "$TARGET_TENANT_ID"
AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" \
  az account set --subscription "$TARGET_SUBSCRIPTION_ID"
```

Support agent:

```bash
ENTRA_ADMIN_AZURE_CONFIG_DIR="$HOME/.azure-365-entra-admin"

AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" \
  ./scripts/assign_hosted_agent_access.sh \
  --principal-id "<support-agent-principal-id>" \
  --api-app-id "<application-client-id>" \
  --azd-project-dir agents/customer-support-maf \
  --agent-type support
```

Directive agent:

```bash
ENTRA_ADMIN_AZURE_CONFIG_DIR="$HOME/.azure-365-entra-admin"

AZURE_CONFIG_DIR="$ENTRA_ADMIN_AZURE_CONFIG_DIR" \
  ./scripts/assign_hosted_agent_access.sh \
  --principal-id "<directive-agent-principal-id>" \
  --api-app-id "<application-client-id>" \
  --azd-project-dir agents/directive-rag-maf \
  --agent-type directive \
  --no-app-tools-connection
```

The helper assigns:

- `AgentTools.Invoke` to the published Hosted Agent identity;
- `AgentTools.Invoke` to the shared project Agent Identity;
- `Agent365.Observability.OtelWrite` to each published Hosted Agent identity;
- target tenant, project endpoint, and connection values in the active azd
  environment.

The Agent 365 service principal and role must exist in the target tenant. For
Agent 365 ingestion, a License Administrator or Global Administrator must also
assign Microsoft 365 E7 or Microsoft Agent 365 to at least one tenant user.
Application Insights remains independent, but the repository helper currently
treats the Agent 365 role as required. If Agent 365 is out of scope or its
service principal is absent, do not run the helper unchanged: it stops before
the shared project Agent Identity receives `AgentTools.Invoke`. Instead, have
the Entra administrator use the Microsoft Graph app-role assignment flow linked
in section 16 to assign only `AgentTools.Invoke` to both identities. Retrieve
the shared identity from the Foundry project ARM property
`properties.agentIdentity.agentIdentityId`; section 10.2 already configures the
required azd tenant, endpoint, and connection values.

Record both principal IDs printed for each agent: the published instance
identity and the shared project Agent Identity.

### 11.3 Update backend allowlists

Add the IDs to `infra/terraform.tfvars`:

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

Plan and apply the Container App configuration update before enabling either
Hosted Agent:

```bash
terraform -chdir=infra plan -input=false -out=agent-allowlists.tfplan
terraform -chdir=infra apply agent-allowlists.tfplan
```

## 12. Enable agents in stages

Enable only components that passed their release checks:

```hcl
foundry_prompt_enabled   = true
foundry_hosted_enabled   = true
directive_agent_enabled  = true
directive_agent_visible  = true
```

Plan, review, and apply:

```bash
terraform -chdir=infra plan -input=false -out=enable-agents.tfplan
terraform -chdir=infra apply enable-agents.tfplan
```

For a lower-risk rollout, enable the Prompt Agent first, then the support Hosted
Agent, then the Directive Assistant.

## 13. Validation

### Infrastructure and drift

```bash
terraform -chdir=infra plan -input=false -detailed-exitcode
```

Exit code `0` means no drift. Exit code `2` means changes exist and must be
reviewed. Any destroy or replacement action requires separate approval.

### Public application

```bash
FRONTEND_URL="https://$(terraform -chdir=infra output -raw frontend_fqdn)"

curl --fail --silent --show-error "$FRONTEND_URL/"
curl --fail --silent --show-error "$FRONTEND_URL/config.js"
curl --fail --silent --show-error "$FRONTEND_URL/api/health/live"
curl --fail --silent --show-error "$FRONTEND_URL/api/health/ready"
```

Confirm `config.js` contains only target tenant/client values.

### Entra authentication and source role

```bash
FRONTEND_URL="https://$(terraform -chdir=infra output -raw frontend_fqdn)"

TOKEN="$(
  az account get-access-token \
    --scope "api://<application-client-id>/access_as_user" \
    --query accessToken \
    --output tsv
)"

curl --fail \
  -H "Authorization: Bearer $TOKEN" \
  "$FRONTEND_URL/api/me"
```

Verify an approved operator receives
`can_manage_directive_sources: true`, while an unassigned user does not.

### Runtime acceptance

Verify:

- all required readiness dependencies are `ok`;
- each enabled agent appears in `/api/agents`;
- support Prompt and Hosted Agent requests complete;
- the Hosted Agent MCP call succeeds with app-only `AgentTools.Invoke`;
- directive retrieval returns grounded references;
- unauthorized source-management and document requests return 401 or 403;
- active Container Apps revisions use the intended immutable image tags;
- `azd ai agent show` reports active target-tenant versions.

## 14. Remove temporary elevation

After Terraform, Hosted Agent deployment, and all required Azure role
assignments are complete, the Azure authorization administrator removes the
temporary assignment:

```bash
AZURE_AUTH_ADMIN_CONFIG_DIR="$HOME/.azure-365-azure-admin"

AZURE_CONFIG_DIR="$AZURE_AUTH_ADMIN_CONFIG_DIR" \
  az login --tenant "<target-tenant-guid>"
AZURE_CONFIG_DIR="$AZURE_AUTH_ADMIN_CONFIG_DIR" \
  az account set --subscription "<target-subscription-guid>"

AZURE_CONFIG_DIR="$AZURE_AUTH_ADMIN_CONFIG_DIR" \
  az role assignment delete \
  --assignee-object-id "<operator-object-id>" \
  --role "User Access Administrator" \
  --scope "/subscriptions/<target-subscription-guid>"
```

If a Global Administrator enabled **Access management for Azure resources**,
the same administrator must set it back to **No**. Deactivate any PIM roles
used for the deployment.

The operator retains Contributor plus the narrower data-plane roles managed by
Terraform for repeat application, ingestion, Search, and Foundry releases.
Reactivate the Azure authorization lane before any later Terraform apply that
creates, changes, or removes Azure role assignments or custom roles.

## 15. Elevated administrator handoff checklist

### Microsoft Entra administrator

- [ ] Create the environment-specific app with `create_entra_app.sh`.
- [ ] Return tenant ID and application client ID to the operator.
- [ ] Add the target frontend redirect and remove localhost if policy requires.
- [ ] Assign approved users/groups to `DirectiveSource.Manage`.
- [ ] Grant tenant-wide consent only if target consent policy requires it.
- [ ] Assign `AgentTools.Invoke` to published and project Agent Identities.
- [ ] Assign `Agent365.Observability.OtelWrite` when Agent 365 is in scope.
- [ ] Ensure the Agent 365 license prerequisite is satisfied when in scope.

### Azure authorization administrator

- [ ] Confirm Contributor is assigned at target subscription scope.
- [ ] Temporarily assign User Access Administrator or Owner to the operator.
- [ ] Do not run Terraform as a substitute for the operator unless IaC is
      refactored to use an explicit deployer object ID.
- [ ] Add narrow ACR data/task roles only if the target permission mode requires
      them.
- [ ] Remove temporary Azure elevation after deployment.
- [ ] If Global Administrator root elevation was used, disable it afterward.

## 16. Microsoft references

- [Steps to assign an Azure role](https://learn.microsoft.com/azure/role-based-access-control/role-assignments-steps)
- [Azure custom roles](https://learn.microsoft.com/azure/role-based-access-control/custom-roles)
- [Elevate Global Administrator access to Azure resources](https://learn.microsoft.com/azure/role-based-access-control/elevate-access-global-admin)
- [Delegate app registration permissions](https://learn.microsoft.com/entra/identity/role-based-access-control/delegate-app-roles)
- [Assign users and groups to an enterprise application](https://learn.microsoft.com/entra/identity/enterprise-apps/assign-user-or-group-access-portal)
- [Grant an app role to a service principal](https://learn.microsoft.com/graph/api/serviceprincipal-post-approleassignments)
- [Register Azure resource providers](https://learn.microsoft.com/azure/azure-resource-manager/management/resource-providers-and-types)
- [Deploy a Foundry Hosted Agent](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent)
- [Hosted Agent permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions)
- [Manage Azure OpenAI quota](https://learn.microsoft.com/azure/foundry/openai/how-to/quota)
- [Azure Container Registry roles](https://learn.microsoft.com/azure/container-registry/container-registry-rbac-built-in-roles-overview)
