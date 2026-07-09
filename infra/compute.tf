# =========================================================== Container Registry
# Premium SKU is required for private endpoints. Public access is disabled; ACA
# pulls over the private endpoint. Image *push* happens from inside the VNet (or
# via a temporary access toggle) — see scripts/deploy_images.sh.
resource "azurerm_container_registry" "main" {
  name                          = local.names.acr
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = "Premium"
  admin_enabled                 = false
  public_network_access_enabled = false
  tags                          = var.tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
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
}

locals {
  placeholder_image = "mcr.microsoft.com/k8se/quickstart:latest"

  backend_env = {
    LLM_MODE                     = "real"
    AZURE_CLIENT_ID              = azurerm_user_assigned_identity.app.client_id
    AZURE_OPENAI_ENDPOINT        = azurerm_cognitive_account.main.endpoint
    AZURE_OPENAI_CHAT_DEPLOYMENT = azurerm_cognitive_deployment.chat.name
    AZURE_OPENAI_EMBED_DEPLOYMENT = azurerm_cognitive_deployment.embedding.name
    COSMOS_ENDPOINT              = azurerm_cosmosdb_account.main.endpoint
    COSMOS_DATABASE              = azurerm_cosmosdb_sql_database.main.name
    POSTGRES_HOST               = azurerm_postgresql_flexible_server.main.fqdn
    POSTGRES_DB                 = azurerm_postgresql_flexible_server_database.memory.name
    POSTGRES_USER               = var.postgres_admin_login
    POSTGRES_PORT               = "5432"
    SEARCH_ENDPOINT             = "https://${azurerm_search_service.main.name}.search.windows.net"
    RAG_MODE_DEFAULT            = "classic"
    AUTH_MODE                   = "mock"
  }
}

# ------------------------------------------------------------------ backend app
resource "azurerm_container_app" "backend" {
  name                         = local.names.backend_app
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
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
    name  = "postgres-password"
    value = var.postgres_admin_password
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
        name  = "POSTGRES_PASSWORD"
        secret_name = "postgres-password"
      }

      dynamic "env" {
        for_each = local.backend_env
        content {
          name  = env.key
          value = env.value
        }
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
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
