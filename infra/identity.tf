data "azurerm_client_config" "current" {}

# App identity → active Foundry chat and embeddings.
resource "azurerm_role_assignment" "app_active_openai_user" {
  scope                = azapi_resource.foundry_agents.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# App identity → AI Search (runtime query and knowledge-base retrieve only).
resource "azurerm_role_assignment" "app_search_index_reader" {
  scope                = azurerm_search_service.main.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# The principal applying Terraform also runs the direct, image-free knowledge and
# Prompt Agent release commands.
resource "azurerm_role_assignment" "deployer_active_openai_user" {
  scope                = azapi_resource.foundry_agents.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "deployer_search_index_contributor" {
  scope                = azurerm_search_service.main.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "deployer_search_service_contributor" {
  scope                = azurerm_search_service.main.id
  role_definition_name = "Search Service Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "deployer_foundry_project_manager" {
  scope              = azapi_resource.foundry_agents_project.id
  role_definition_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/eadc314b-1a2d-4efa-be10-5d325db5065e"
  principal_id       = data.azurerm_client_config.current.object_id
}

# Search uses its system identity to run Foundry IQ query planning.
resource "azurerm_role_assignment" "search_active_foundry_user" {
  scope                = azapi_resource.foundry_agents.id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_search_service.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "app_appinsights_publisher" {
  scope                = azurerm_application_insights.main.id
  role_definition_name = "Monitoring Metrics Publisher"
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
