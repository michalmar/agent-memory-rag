# =========================================================== Azure AI Foundry
# AIServices "Foundry" account (kind=AIServices) with project management enabled —
# this is the current Microsoft Foundry resource shape (see
# https://learn.microsoft.com/azure/foundry/how-to/create-resource-terraform).
# It doubles as the Azure OpenAI endpoint used by the app.
resource "azurerm_cognitive_account" "main" {
  name                          = local.names.foundry
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = local.names.foundry
  public_network_access_enabled = false
  project_management_enabled    = true
  tags                          = var.tags

  identity {
    type = "SystemAssigned"
  }
}

# Foundry project (child of the AIServices account).
resource "azurerm_cognitive_account_project" "main" {
  name                 = "${var.name_prefix}-project"
  location             = azurerm_resource_group.main.location
  cognitive_account_id = azurerm_cognitive_account.main.id

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.chat_model_name
  cognitive_account_id = azurerm_cognitive_account.main.id

  model {
    format  = "OpenAI"
    name    = var.chat_model_name
    version = var.chat_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.chat_model_capacity
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.embedding_model_name
  cognitive_account_id = azurerm_cognitive_account.main.id

  model {
    format  = "OpenAI"
    name    = var.embedding_model_name
    version = var.embedding_model_version
  }

  sku {
    name     = "Standard"
    capacity = var.embedding_model_capacity
  }

  depends_on = [azurerm_cognitive_deployment.chat]
}

resource "azurerm_private_endpoint" "foundry" {
  name                = "pe-${local.names.foundry}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-foundry"
    private_connection_resource_id = azurerm_cognitive_account.main.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name = "foundry"
    private_dns_zone_ids = [
      azurerm_private_dns_zone.zones["openai"].id,
      azurerm_private_dns_zone.zones["cognitive"].id,
      azurerm_private_dns_zone.zones["aiservices"].id,
    ]
  }
}
