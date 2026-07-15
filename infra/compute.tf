# =========================================================== Container Registry
# Premium ACR keeps its private endpoint for ACA pulls and exposes an Entra/RBAC-only
# public endpoint so the non-injected Hosted Agent runtime can pull on restart.
resource "azurerm_container_registry" "main" {
  name                          = local.names.acr
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = "Premium"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = true
  network_rule_set = [{
    default_action = "Allow"
    ip_rule        = []
  }]
  tags = var.tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "frontend_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.frontend.principal_id
}

resource "azurerm_private_endpoint" "acr" {
  name                = "pe-${local.names.acr}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-acr"
    private_connection_resource_id = azurerm_container_registry.main.id
    subresource_names              = ["registry"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "acr"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["acr"].id]
  }
}

# =========================================================== Container Apps Env
resource "azurerm_container_app_environment" "main" {
  name                       = local.names.aca_env
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = azurerm_subnet.aca.id
  tags                       = var.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }

  lifecycle {
    ignore_changes = [infrastructure_resource_group_name]
  }
}

locals {
  placeholder_image = "mcr.microsoft.com/k8se/quickstart:latest"

  backend_env = {
    APP_ENV                                    = "production"
    LLM_MODE                                   = "real"
    AZURE_CLIENT_ID                            = azurerm_user_assigned_identity.app.client_id
    AZURE_OPENAI_ENDPOINT                      = local.foundry_agents_cognitive_endpoint
    AZURE_OPENAI_CHAT_DEPLOYMENT               = azurerm_cognitive_deployment.foundry_agents_chat.name
    AZURE_OPENAI_EMBED_DEPLOYMENT              = azurerm_cognitive_deployment.foundry_agents_embedding.name
    COSMOS_ENDPOINT                            = azurerm_cosmosdb_account.main.endpoint
    COSMOS_DATABASE                            = azurerm_cosmosdb_sql_database.main.name
    POSTGRES_HOST                              = azurerm_postgresql_flexible_server.main.fqdn
    POSTGRES_DB                                = azurerm_postgresql_flexible_server_database.memory.name
    POSTGRES_USER                              = azurerm_user_assigned_identity.app.name
    POSTGRES_PORT                              = "5432"
    PG_AUTH_MODE                               = "managed_identity"
    SEARCH_ENDPOINT                            = "https://${azurerm_search_service.main.name}.search.windows.net"
    SEARCH_KB                                  = "customer-support-kb"
    SEARCH_ORDERS_KNOWLEDGE_SOURCE             = "orders-ks"
    SEARCH_POLICY_KNOWLEDGE_SOURCE             = "return-policy-ks"
    SEARCH_KNOWLEDGE_API_VERSION               = "2026-05-01-preview"
    FOUNDRY_PROJECT_ENDPOINT                   = local.foundry_agents_project_endpoint
    FOUNDRY_PROMPT_AGENT_NAME                  = var.foundry_prompt_agent_name
    FOUNDRY_HOSTED_AGENT_NAME                  = var.foundry_hosted_agent_name
    FOUNDRY_PROMPT_ENABLED                     = tostring(var.foundry_prompt_enabled)
    FOUNDRY_HOSTED_ENABLED                     = tostring(var.foundry_hosted_enabled)
    AGENT_RELEASE_ID                           = var.agent_release_id
    AGENT_GATEWAY_AUDIENCE                     = var.entra_client_id
    AGENT_GATEWAY_REQUIRED_ROLE                = "AgentTools.Invoke"
    HOSTED_AGENT_PRINCIPAL_IDS                 = join(" ", sort(tolist(var.hosted_agent_principal_ids)))
    OTEL_SERVICE_NAME                          = "agent-memory-backend"
    OTEL_RESOURCE_ATTRIBUTES                   = "service.namespace=agent-memory,deployment.environment=demo"
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING  = "Authorization=AAD;ClientId=${azurerm_user_assigned_identity.app.client_id}"
    APPLICATIONINSIGHTS_STATSBEAT_DISABLED     = "true"
    APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL = "true"
    AUTH_MODE                                  = "entra"
    ENTRA_TENANT_ID                            = var.entra_tenant_id
    ENTRA_AUDIENCE                             = var.entra_client_id
    ENTRA_REQUIRED_SCOPES                      = "access_as_user"
  }
}

# ------------------------------------------------------------------ backend app
resource "azurerm_container_app" "backend" {
  name                         = local.names.backend_app
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name  = "appinsights-connection-string"
    value = azurerm_application_insights.main.connection_string
  }

  ingress {
    external_enabled = false
    target_port      = 8000
    transport        = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1 # in-memory sessions require a single replica

    container {
      name   = "backend"
      image  = local.placeholder_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection-string"
      }

      dynamic "env" {
        for_each = local.backend_env
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health/live"
        initial_delay           = 1
        interval_seconds        = 5
        timeout                 = 2
        failure_count_threshold = 30
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health/live"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 3
        failure_count_threshold = 3
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8000
        path                    = "/health/ready"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 8
        failure_count_threshold = 3
        success_count_threshold = 1
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# ----------------------------------------------------------------- frontend app
resource "azurerm_container_app" "frontend" {
  name                         = local.names.frontend_app
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.frontend.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.frontend.id
  }

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 2

    container {
      name   = "frontend"
      image  = local.placeholder_image
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "BACKEND_URL"
        value = "https://${local.names.backend_app}.internal.${azurerm_container_app_environment.main.default_domain}"
      }

      env {
        name  = "AUTH_MODE"
        value = "entra"
      }

      env {
        name  = "ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }

      env {
        name  = "ENTRA_CLIENT_ID"
        value = var.entra_client_id
      }

      env {
        name  = "ENTRA_API_SCOPE"
        value = "api://${var.entra_client_id}/access_as_user"
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
