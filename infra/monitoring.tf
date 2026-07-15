resource "azurerm_application_insights" "main" {
  name                = local.names.app_insights
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  # Foundry project tracing uses the App Insights connection string and emits
  # outside the application VNet. The backend still uses UAMI through AMPLS.
  local_authentication_enabled = true
  internet_ingestion_enabled   = true
  internet_query_enabled       = true
  tags                         = var.tags
}

resource "azapi_resource" "foundry_app_insights_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = azurerm_application_insights.main.name
  parent_id                 = azapi_resource.foundry_agents_project.id
  schema_validation_enabled = false

  body = {
    properties = {
      authType      = "ApiKey"
      category      = "AppInsights"
      target        = azurerm_application_insights.main.id
      isSharedToAll = false
      credentials = {
        key = azurerm_application_insights.main.connection_string
      }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.main.id
      }
    }
  }
}

resource "azurerm_monitor_diagnostic_setting" "foundry_agents" {
  name                       = "diag-${local.names.foundry_agents}"
  target_resource_id         = azapi_resource.foundry_agents.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "Audit"
  }

  enabled_log {
    category = "RequestResponse"
  }

  enabled_log {
    category = "AzureOpenAIRequestUsage"
  }

  enabled_log {
    category = "Trace"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

resource "azurerm_monitor_private_link_scope" "main" {
  name                = "ampls-${var.name_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  # Open permits Foundry's public platform path while private DNS keeps ACA
  # telemetry on the existing private endpoint.
  ingestion_access_mode = "Open"
  query_access_mode     = "Open"
  tags                  = var.tags
}

resource "azurerm_monitor_private_link_scoped_service" "app_insights" {
  name                = "ampls-app-insights"
  resource_group_name = azurerm_resource_group.main.name
  scope_name          = azurerm_monitor_private_link_scope.main.name
  linked_resource_id  = azurerm_application_insights.main.id
}

resource "azurerm_monitor_private_link_scoped_service" "workspace" {
  name                = "ampls-workspace"
  resource_group_name = azurerm_resource_group.main.name
  scope_name          = azurerm_monitor_private_link_scope.main.name
  linked_resource_id  = azurerm_log_analytics_workspace.main.id
}

resource "azurerm_private_endpoint" "monitor" {
  name                = "pe-ampls-${var.name_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-azure-monitor"
    private_connection_resource_id = azurerm_monitor_private_link_scope.main.id
    subresource_names              = ["azuremonitor"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name = "azure-monitor"
    private_dns_zone_ids = [
      azurerm_private_dns_zone.zones["monitor"].id,
      azurerm_private_dns_zone.zones["oms"].id,
      azurerm_private_dns_zone.zones["ods"].id,
      azurerm_private_dns_zone.zones["agentsvc"].id,
      azurerm_private_dns_zone.zones["blob"].id,
    ]
  }
}
