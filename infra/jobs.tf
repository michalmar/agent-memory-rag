locals {
  postgres_setup_env = {
    AZURE_CLIENT_ID        = azurerm_user_assigned_identity.postgres_bootstrap.client_id
    POSTGRES_HOST          = azurerm_postgresql_flexible_server.main.fqdn
    POSTGRES_DB            = azurerm_postgresql_flexible_server_database.memory.name
    POSTGRES_PORT          = "5432"
    POSTGRES_USER          = azurerm_user_assigned_identity.postgres_bootstrap.name
    POSTGRES_APP_USER      = azurerm_user_assigned_identity.app.name
    POSTGRES_APP_OBJECT_ID = azurerm_user_assigned_identity.app.principal_id
  }
}

resource "azurerm_role_assignment" "postgres_bootstrap_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.postgres_bootstrap.principal_id
}

resource "azurerm_container_app_job" "postgres_setup" {
  name                         = local.names.pg_setup_job
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 900
  replica_retry_limit          = 1
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.postgres_bootstrap.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.postgres_bootstrap.id
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "postgres-setup"
      image   = local.placeholder_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = []
      args    = []

      dynamic "env" {
        for_each = local.postgres_setup_env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  depends_on = [
    azurerm_postgresql_flexible_server_active_directory_administrator.bootstrap,
  ]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
