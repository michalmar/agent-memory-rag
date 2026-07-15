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

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "search_endpoint" {
  value = "https://${azurerm_search_service.main.name}.search.windows.net"
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

output "postgres_setup_job_name" {
  value = azurerm_container_app_job.postgres_setup.name
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

output "foundry_agents_chat_deployment" {
  value = azurerm_cognitive_deployment.foundry_agents_chat.name
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

output "agent_tool_gateway_url" {
  value = "https://${azurerm_container_app.frontend.ingress[0].fqdn}/api"
}

output "agent_tool_gateway_scope" {
  value = "api://${var.entra_client_id}/.default"
}

output "foundry_agents_project_principal_id" {
  value = azapi_resource.foundry_agents_project.output.identity.principalId
}
