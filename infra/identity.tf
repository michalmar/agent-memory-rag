data "azurerm_client_config" "current" {}

# App identity → Azure OpenAI (chat + embeddings via Responses/Embeddings API).
resource "azurerm_role_assignment" "app_openai_user" {
  scope                = azurerm_cognitive_account.main.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# App identity → AI Search (query + index data-plane, and index management for setup).
resource "azurerm_role_assignment" "app_search_index_contributor" {
  scope                = azurerm_search_service.main.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "app_search_service_contributor" {
  scope                = azurerm_search_service.main.id
  role_definition_name = "Search Service Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# App identity → Cosmos DB data-plane (NoSQL built-in Data Contributor).
resource "azurerm_cosmosdb_sql_role_assignment" "app_cosmos_contributor" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_user_assigned_identity.app.principal_id
  scope               = azurerm_cosmosdb_account.main.id
}

# ---- Deployer (the human/SP running Terraform) also needs data-plane access
# ---- so `az` can be used for break-glass ops; Cosmos control-plane already covered by RBAC.
resource "azurerm_cosmosdb_sql_role_assignment" "deployer_cosmos_contributor" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = azurerm_cosmosdb_account.main.id
}
