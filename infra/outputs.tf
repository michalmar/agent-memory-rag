output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "backend_app_name" {
  value = azurerm_container_app.backend.name
}

output "frontend_app_name" {
  value = azurerm_container_app.frontend.name
}

output "frontend_fqdn" {
  value = azurerm_container_app.frontend.ingress[0].fqdn
}

output "backend_internal_fqdn" {
  value = "${azurerm_container_app.backend.name}.internal.${azurerm_container_app_environment.main.default_domain}"
}

output "openai_endpoint" {
  value = local.foundry_agents_cognitive_endpoint
}

output "openai_resource_uri" {
  value = local.foundry_agents_openai_resource_uri
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.main.endpoint
}

output "search_endpoint" {
  value = "https://${azurerm_search_service.main.name}.search.windows.net"
}

output "search_knowledge_base" {
  value = local.search_assets.knowledge_base
}

output "search_orders_index" {
  value = local.search_assets.orders_index
}

output "search_policy_index" {
  value = local.search_assets.policy_index
}

output "search_orders_knowledge_source" {
  value = local.search_assets.orders_knowledge_source
}

output "search_policy_knowledge_source" {
  value = local.search_assets.policy_knowledge_source
}

output "search_knowledge_api_version" {
  value = local.search_assets.knowledge_api_version
}

output "app_identity_client_id" {
  value = azurerm_user_assigned_identity.app.client_id
}

output "chat_deployment" {
  value = azurerm_cognitive_deployment.foundry_agents_chat.name
}

output "embedding_deployment" {
  value = azurerm_cognitive_deployment.foundry_agents_embedding.name
}

output "application_insights_name" {
  value = azurerm_application_insights.main.name
}

output "search_service_name" {
  value = azurerm_search_service.main.name
}

output "foundry_agents_account_id" {
  value = azapi_resource.foundry_agents.id
}

output "foundry_agents_project_id" {
  value = azapi_resource.foundry_agents_project.id
}

output "foundry_agents_project_endpoint" {
  value = local.foundry_agents_project_endpoint
}

output "foundry_iq_connection_name" {
  value = azapi_resource.foundry_iq_connection.name
}

output "foundry_iq_mcp_endpoint" {
  value = local.foundry_iq_mcp_endpoint
}

output "foundry_prompt_agent_name" {
  value = var.foundry_prompt_agent_name
}

output "foundry_hosted_agent_name" {
  value = var.foundry_hosted_agent_name
}

output "hosted_agent_image" {
  value = "${azurerm_container_registry.main.login_server}/${var.foundry_hosted_agent_name}:${var.agent_release_id}"
}

output "agent_release_id" {
  value = var.agent_release_id
}

output "directive_foundry_agent_name" {
  value = var.directive_foundry_agent_name
}

output "directive_hosted_agent_image" {
  value = "${azurerm_container_registry.main.login_server}/${var.directive_foundry_agent_name}:${var.directive_agent_release_id}"
}

output "directive_agent_release_id" {
  value = var.directive_agent_release_id
}

output "agent_tool_gateway_url" {
  value = "https://${azurerm_container_app.frontend.ingress[0].fqdn}/api"
}

output "agent_tool_gateway_scope" {
  value = "api://${var.entra_client_id}/.default"
}

output "directive_ingestion_job_name" {
  value = azurerm_container_app_job.directive_ingestion.name
}

output "directive_ingestion_identity_client_id" {
  value = azurerm_user_assigned_identity.directive_ingestion.client_id
}

output "directive_ingestion_identity_principal_id" {
  value = azurerm_user_assigned_identity.directive_ingestion.principal_id
}

output "directive_search_index_name" {
  value = var.directive_search_index_name
}

output "directive_search_knowledge_source_name" {
  value = var.directive_search_knowledge_source_name
}

output "directive_search_knowledge_base_name" {
  value = var.directive_search_knowledge_base_name
}

output "foundry_application_tools_connection_name" {
  value = azapi_resource.foundry_application_tools_connection.name
}

output "foundry_application_tools_mcp_endpoint" {
  value = azapi_resource.foundry_application_tools_connection.body.properties.target
}

output "foundry_agents_project_principal_id" {
  value = azapi_resource.foundry_agents_project.output.identity.principalId
}

output "directive_model_deployment" {
  value = azurerm_cognitive_deployment.directive.name
}

output "directive_knowledge_model_deployment" {
  value = azurerm_cognitive_deployment.directive_knowledge_planner.name
}

output "directive_artifacts_storage_account" {
  value = azurerm_storage_account.directive_artifacts.name
}

output "directive_artifacts_blob_endpoint" {
  value = azurerm_storage_account.directive_artifacts.primary_blob_endpoint
}

output "directive_artifacts_container" {
  value = azapi_resource.directive_artifacts_container.name
}

output "directive_document_intelligence_name" {
  value = azurerm_cognitive_account.directive_layout.name
}

output "directive_document_intelligence_endpoint" {
  value = azurerm_cognitive_account.directive_layout.endpoint
}

output "directive_cosmos_database" {
  value = azurerm_cosmosdb_sql_database.directives.name
}

output "directive_catalog_container" {
  value = azurerm_cosmosdb_sql_container.directive_catalog.name
}

output "directive_mandates_container" {
  value = azurerm_cosmosdb_sql_container.directive_mandates.name
}
