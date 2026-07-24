# Directive-only data and model infrastructure. All runtime rollout flags remain
# false until ingestion, backend tools, the Hosted Agent, and UI pass later gates.

locals {
  support_cosmos_database_scope = (
    "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.main.name}"
  )
  directive_cosmos_database_scope = (
    "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.directives.name}"
  )
  cosmos_data_reader_role_definition_id = (
    "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001"
  )
  cosmos_data_contributor_role_definition_id = (
    "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  )
}

# This deployment already exists and was validated in Phase 0. The import block
# adopts it without creating a duplicate or changing the support model.
resource "azurerm_cognitive_deployment" "directive" {
  name                 = var.directive_model_name
  cognitive_account_id = azapi_resource.foundry_agents.id

  model {
    format  = "OpenAI"
    name    = var.directive_model_name
    version = var.directive_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.directive_model_capacity
  }

  version_upgrade_option = "OnceNewDefaultVersionAvailable"

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = azurerm_cognitive_deployment.directive
  id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.CognitiveServices/accounts/${local.names.foundry_agents}/deployments/${var.directive_model_name}"
}

resource "azurerm_cognitive_deployment" "directive_knowledge_planner" {
  name                 = var.directive_knowledge_model_deployment
  cognitive_account_id = azapi_resource.foundry_agents.id

  model {
    format  = "OpenAI"
    name    = var.directive_knowledge_model_name
    version = var.directive_knowledge_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.directive_knowledge_model_capacity
  }

  version_upgrade_option = "NoAutoUpgrade"

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_account" "directive_artifacts" {
  name                            = local.names.directive_storage
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = var.directive_storage_replication_type
  access_tier                     = "Hot"
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  public_network_access_enabled   = false
  tags                            = var.tags

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = var.directive_artifact_retention_days
    }

    container_delete_retention_policy {
      days = var.directive_artifact_retention_days
    }
  }

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }

  lifecycle {
    # Defender for Storage policy owns the storageDataScanner private-link rule.
    ignore_changes = [network_rules[0].private_link_access]
  }
}

# ARM creation avoids shared-key data-plane access from the Terraform provider.
resource "azapi_resource" "directive_artifacts_container" {
  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01"
  name      = var.directive_artifacts_container_name
  parent_id = "${azurerm_storage_account.directive_artifacts.id}/blobServices/default"

  body = {
    properties = {
      publicAccess = "None"
    }
  }
}

resource "azurerm_private_endpoint" "directive_artifacts" {
  name                = "pe-${local.names.directive_storage}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-directive-artifacts"
    private_connection_resource_id = azurerm_storage_account.directive_artifacts.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "blob"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["blob"].id]
  }
}

resource "azurerm_cognitive_account" "directive_layout" {
  name                          = local.names.directive_docint
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  kind                          = "FormRecognizer"
  sku_name                      = "S0"
  custom_subdomain_name         = local.names.directive_docint
  local_auth_enabled            = false
  public_network_access_enabled = false
  tags                          = var.tags

  identity {
    type = "SystemAssigned"
  }
  network_acls {
    default_action = "Deny"
  }
}

resource "azurerm_private_endpoint" "directive_layout" {
  name                = "pe-${local.names.directive_docint}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.pe.id
  tags                = var.tags

  private_service_connection {
    name                           = "psc-directive-layout"
    private_connection_resource_id = azurerm_cognitive_account.directive_layout.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "cognitive"
    private_dns_zone_ids = [azurerm_private_dns_zone.zones["cognitive"].id]
  }
}

resource "azurerm_cosmosdb_sql_database" "directives" {
  name                = var.directive_cosmos_database_name
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "directive_catalog" {
  name                  = var.directive_catalog_container_name
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.directives.name
  partition_key_paths   = ["/directive_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "directive_mandates" {
  name                  = var.directive_mandates_container_name
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.directives.name
  partition_key_paths   = ["/user_id"]
  partition_key_version = 2
}

# Stage one of Cosmos privilege narrowing: establish the replacement support
# database assignment before removing the pre-existing account-wide assignment.
resource "azurerm_cosmosdb_sql_role_assignment" "app_support_contributor" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = local.cosmos_data_contributor_role_definition_id
  principal_id        = azurerm_user_assigned_identity.app.principal_id
  scope               = local.support_cosmos_database_scope
}

resource "azurerm_cosmosdb_sql_role_assignment" "app_directive_reader" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = local.cosmos_data_reader_role_definition_id
  principal_id        = azurerm_user_assigned_identity.app.principal_id
  scope               = local.directive_cosmos_database_scope
}

resource "azurerm_role_assignment" "app_directive_blob_reader" {
  scope                            = azapi_resource.directive_artifacts_container.id
  role_definition_name             = "Storage Blob Data Reader"
  principal_id                     = azurerm_user_assigned_identity.app.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "deployer_directive_blob_contributor" {
  scope                = azapi_resource.directive_artifacts_container.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "deployer_directive_document_intelligence" {
  scope                = azurerm_cognitive_account.directive_layout.id
  role_definition_name = "Cognitive Services User"
  principal_id         = data.azurerm_client_config.current.object_id
}
