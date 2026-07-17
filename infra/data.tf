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

  lifecycle {
    ignore_changes = [capabilities]
  }
}

resource "terraform_data" "cosmos_vector_search" {
  triggers_replace = {
    account_id = azurerm_cosmosdb_account.main.id
    capability = "EnableNoSQLVectorSearch"
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      current="$(az cosmosdb show \
        --resource-group '${azurerm_resource_group.main.name}' \
        --name '${azurerm_cosmosdb_account.main.name}' \
        --query 'capabilities[].name' \
        --output tsv)"
      if ! grep -qx 'EnableNoSQLVectorSearch' <<<"$current"; then
        az cosmosdb update \
          --resource-group '${azurerm_resource_group.main.name}' \
          --name '${azurerm_cosmosdb_account.main.name}' \
          --capabilities EnableServerless EnableNoSQLVectorSearch \
          --output none
      fi
      updated="$(az cosmosdb show \
        --resource-group '${azurerm_resource_group.main.name}' \
        --name '${azurerm_cosmosdb_account.main.name}' \
        --query 'capabilities[].name' \
        --output tsv)"
      grep -qx 'EnableServerless' <<<"$updated"
      grep -qx 'EnableNoSQLVectorSearch' <<<"$updated"
    EOT
  }
}

resource "time_sleep" "cosmos_vector_search_propagation" {
  depends_on      = [terraform_data.cosmos_vector_search]
  create_duration = "15m"
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

resource "azapi_resource" "cosmos_memories" {
  type      = "Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15"
  name      = "memories"
  parent_id = azurerm_cosmosdb_sql_database.main.id

  body = {
    properties = {
      resource = {
        id = "memories"
        partitionKey = {
          paths   = ["/user_id"]
          kind    = "Hash"
          version = 2
        }
        indexingPolicy = {
          automatic    = true
          indexingMode = "consistent"
          includedPaths = [
            {
              path = "/*"
            }
          ]
          excludedPaths = [
            {
              path = "/_etag/?"
            },
            {
              path = "/embedding/*"
            }
          ]
          vectorIndexes = [
            {
              path = "/embedding"
              type = "quantizedFlat"
            }
          ]
        }
        vectorEmbeddingPolicy = {
          vectorEmbeddings = [
            {
              path             = "/embedding"
              dataType         = "float32"
              distanceFunction = "cosine"
              dimensions       = 3072
            }
          ]
        }
      }
      options = {}
    }
  }

  depends_on = [time_sleep.cosmos_vector_search_propagation]
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
