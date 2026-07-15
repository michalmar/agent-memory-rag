locals {
  # Deterministic short suffix keeps globally-unique names stable across applies.
  suffix = substr(sha1("${var.subscription_id}-${var.resource_group_name}-${var.name_prefix}"), 0, 6)

  base = "${var.name_prefix}${local.suffix}"

  names = {
    log_analytics     = "log-${var.name_prefix}-${local.suffix}"
    app_insights      = "appi-${var.name_prefix}-${local.suffix}"
    vnet              = "vnet-${var.name_prefix}-${local.suffix}"
    identity          = "id-${var.name_prefix}-${local.suffix}"
    frontend_identity = "id-${var.name_prefix}-frontend-${local.suffix}"
    pg_identity       = "id-${var.name_prefix}-pgbootstrap-${local.suffix}"
    foundry_agents    = "${local.base}aif2"
    foundry_project   = "${var.name_prefix}-agents"
    cosmos            = "${local.base}cosmos"
    postgres          = "${local.base}pgnc"
    search            = "${local.base}search"
    acr               = "${local.base}acr"
    aca_env           = "cae-${var.name_prefix}-${local.suffix}"
    backend_app       = "ca-${var.name_prefix}-backend"
    frontend_app      = "ca-${var.name_prefix}-frontend"
    pg_setup_job      = "caj-${var.name_prefix}-pgsetup"
  }
}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = local.names.log_analytics
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "app" {
  name                = local.names.identity
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "frontend" {
  name                = local.names.frontend_identity
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "postgres_bootstrap" {
  name                = local.names.pg_identity
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}
