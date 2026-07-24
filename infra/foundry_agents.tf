# Additive Foundry generation for the selectable native Prompt Agent and Hosted
# Microsoft Agent Framework agent. It uses Basic Agent setup so platform-managed
# session state remains compatible with tenant policy that disables Storage keys.

locals {
  search_assets = {
    knowledge_base          = "customer-support-kb"
    orders_index            = "orders"
    policy_index            = "return-policy"
    orders_knowledge_source = "orders-ks"
    policy_knowledge_source = "return-policy-ks"
    knowledge_api_version   = "2026-05-01-preview"
  }

  foundry_agents_project_endpoint    = "https://${local.names.foundry_agents}.services.ai.azure.com/api/projects/${local.names.foundry_project}"
  foundry_agents_cognitive_endpoint  = "https://${local.names.foundry_agents}.cognitiveservices.azure.com/"
  foundry_agents_openai_resource_uri = "https://${local.names.foundry_agents}.openai.azure.com"
  foundry_iq_mcp_endpoint            = "https://${azurerm_search_service.main.name}.search.windows.net/knowledgebases/${local.search_assets.knowledge_base}/mcp?api-version=${local.search_assets.knowledge_api_version}"
}

resource "azapi_resource" "foundry_agents" {
  type                      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name                      = local.names.foundry_agents
  parent_id                 = azurerm_resource_group.main.id
  location                  = azurerm_resource_group.main.location
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    kind = "AIServices"
    sku = {
      name = "S0"
    }
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      allowProjectManagement = true
      customSubDomainName    = local.names.foundry_agents
      disableLocalAuth       = true
      publicNetworkAccess    = "Enabled"
      networkAcls = {
        bypass              = "AzureServices"
        defaultAction       = "Allow"
        ipRules             = []
        virtualNetworkRules = []
      }
    }
  }

  response_export_values = [
    "identity.principalId",
  ]
}

resource "azurerm_cognitive_deployment" "foundry_agents_chat" {
  name                 = var.chat_model_name
  cognitive_account_id = azapi_resource.foundry_agents.id

  model {
    format  = "OpenAI"
    name    = var.chat_model_name
    version = var.chat_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.chat_model_capacity
  }
}

resource "azurerm_cognitive_deployment" "foundry_agents_embedding" {
  name                 = var.embedding_model_name
  cognitive_account_id = azapi_resource.foundry_agents.id

  model {
    format  = "OpenAI"
    name    = var.embedding_model_name
    version = var.embedding_model_version
  }

  sku {
    name     = "Standard"
    capacity = var.embedding_model_capacity
  }

  depends_on = [azurerm_cognitive_deployment.foundry_agents_chat]
}

resource "azapi_resource" "foundry_agents_project" {
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name                      = local.names.foundry_project
  parent_id                 = azapi_resource.foundry_agents.id
  location                  = azurerm_resource_group.main.location
  schema_validation_enabled = false
  tags                      = var.tags

  body = {
    identity = {
      type = "SystemAssigned"
    }
    properties = {
      displayName = "Customer support agents"
      description = "Basic setup project for the native Prompt Agent and Hosted MAF Agent."
    }
  }

  response_export_values = [
    "identity.principalId",
    "properties.internalId",
  ]

}

resource "azapi_resource" "foundry_iq_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = var.foundry_iq_connection_name
  parent_id                 = azapi_resource.foundry_agents_project.id
  schema_validation_enabled = false

  body = {
    properties = {
      authType      = "ProjectManagedIdentity"
      category      = "RemoteTool"
      target        = local.foundry_iq_mcp_endpoint
      isSharedToAll = false
      audience      = "https://search.azure.com/"
      metadata = {
        ApiType = "Azure"
      }
    }
  }
}

resource "azapi_resource" "foundry_application_tools_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview"
  name                      = var.foundry_application_tools_connection_name
  parent_id                 = azapi_resource.foundry_agents_project.id
  schema_validation_enabled = false

  body = {
    properties = {
      authType      = "AgenticIdentityToken"
      category      = "RemoteTool"
      target        = "https://${azurerm_container_app.frontend.ingress[0].fqdn}/api/mcp/"
      isSharedToAll = false
      audience      = "api://${var.entra_client_id}"
      metadata = {
        ApiType = "Azure"
      }
    }
  }
}

resource "azurerm_role_assignment" "foundry_project_kb_reader" {
  scope                            = azurerm_search_service.main.id
  role_definition_name             = "Search Index Data Reader"
  principal_id                     = azapi_resource.foundry_agents_project.output.identity.principalId
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "foundry_project_foundry_user" {
  scope                            = azapi_resource.foundry_agents.id
  role_definition_id               = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d"
  principal_id                     = azapi_resource.foundry_agents_project.output.identity.principalId
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "foundry_project_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azapi_resource.foundry_agents_project.output.identity.principalId
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "foundry_project_log_reader" {
  scope                            = azurerm_log_analytics_workspace.main.id
  role_definition_name             = "Log Analytics Reader"
  principal_id                     = azapi_resource.foundry_agents_project.output.identity.principalId
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_definition" "backend_foundry_agent_consumer" {
  name        = "Agent Memory Foundry Agent Consumer"
  scope       = "/subscriptions/${var.subscription_id}"
  description = "Invoke Foundry agent endpoints and attach trusted end-user identity."

  permissions {
    data_actions = [
      "Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action",
      "Microsoft.CognitiveServices/accounts/AIServices/agents/endpoints/UserIdentityImpersonation/action",
      # Foundry maps runtime conversation create/delete calls to these agent actions.
      "Microsoft.CognitiveServices/accounts/AIServices/agents/write",
      "Microsoft.CognitiveServices/accounts/AIServices/agents/delete",
    ]
  }

  assignable_scopes = [
    "/subscriptions/${var.subscription_id}",
  ]
}

resource "azurerm_role_assignment" "app_foundry_agent_consumer" {
  scope                            = azapi_resource.foundry_agents_project.id
  role_definition_id               = azurerm_role_definition.backend_foundry_agent_consumer.role_definition_resource_id
  principal_id                     = azurerm_user_assigned_identity.app.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azapi_update_resource" "acr_arm_authentication" {
  type        = "Microsoft.ContainerRegistry/registries@2023-07-01"
  resource_id = azurerm_container_registry.main.id

  body = {
    properties = {
      policies = {
        azureADAuthenticationAsArmPolicy = {
          status = "enabled"
        }
      }
    }
  }
}
