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
  value = azurerm_ai_services.main.endpoint
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
  value = azurerm_cognitive_deployment.chat.name
}

output "embedding_deployment" {
  value = azurerm_cognitive_deployment.embedding.name
}
