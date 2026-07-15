# =========================================================== Cosmos DB (NoSQL)
resource "azurerm_cosmosdb_account" "main" {
  name                = local.names.cosmos
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  # Application state stays reachable only through Private Link.
  public_network_access_enabled = false
  local_authentication_enabled  = false

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }

  tags = var.tags
}

resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "support"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "history" {
  name                  = "history"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/user_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "profiles" {
  name                  = "profiles"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/user_id"]
  partition_key_version = 2
}

resource "azurerm_private_endpoint" "cosmos" {
  name                = "pe-${local.names.cosmos}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-cosmos"
    private_connection_resource_id = azurerm_cosmosdb_account.main.id
    subresource_names              = ["Sql"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "cosmos"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["cosmos"].id]
  }
}

# =========================================================== PostgreSQL Flexible
# Deployed in var.postgres_location (eastus2 is offer-restricted for Postgres on
# this subscription). Public access disabled; reached from the eastus2 VNet via a
# cross-region private endpoint (VNet injection can't span regions).
resource "azurerm_postgresql_flexible_server" "main" {
  name                          = local.names.postgres
  location                      = var.postgres_location
  resource_group_name           = azurerm_resource_group.main.name
  version                       = "16"
  storage_mb                    = 32768
  sku_name                      = "B_Standard_B1ms"
  public_network_access_enabled = false
  tags                          = var.tags

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = false
    tenant_id                     = data.azurerm_client_config.current.tenant_id
  }

  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "bootstrap" {
  server_name         = azurerm_postgresql_flexible_server.main.name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  object_id           = azurerm_user_assigned_identity.postgres_bootstrap.principal_id
  principal_name      = azurerm_user_assigned_identity.postgres_bootstrap.name
  principal_type      = "ServicePrincipal"
}

# Cross-region private endpoint in the eastus2 PE subnet targeting the Postgres server.
resource "azurerm_private_endpoint" "postgres" {
  name                = "pe-${local.names.postgres}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-postgres"
    private_connection_resource_id = azurerm_postgresql_flexible_server.main.id
    subresource_names              = ["postgresqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "postgres"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["postgres"].id]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.links]
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

resource "azurerm_postgresql_flexible_server_database" "memory" {
  name      = "memory"
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "UTF8"
}

# =========================================================== Azure AI Search
# Deployed in var.search_location (eastus2 is out of Search capacity on this
# subscription). All clients use the Entra-only public endpoint because a Search
# private endpoint makes the MCP hostname unresolvable to non-injected agents.
resource "azurerm_search_service" "main" {
  name                          = local.names.search
  location                      = var.search_location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = var.search_sku
  local_authentication_enabled  = false
  public_network_access_enabled = true
  semantic_search_sku           = "free"
  tags                          = var.tags

  identity {
    type = "SystemAssigned"
  }
}
