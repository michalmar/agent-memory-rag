# Private, manual directive ingestion. The first immutable image is released after
# Terraform creates the job and its dedicated least-privilege identity.

locals {
  directive_ingestion_env = {
    AZURE_CLIENT_ID                        = azurerm_user_assigned_identity.directive_ingestion.client_id
    AZURE_TENANT_ID                        = var.entra_tenant_id
    DOCUMENT_INTELLIGENCE_ENDPOINT         = azurerm_cognitive_account.directive_layout.endpoint
    DOCUMENT_INTELLIGENCE_API_VERSION      = "2024-11-30"
    DIRECTIVE_BLOB_ACCOUNT_URL             = azurerm_storage_account.directive_artifacts.primary_blob_endpoint
    DIRECTIVE_BLOB_CONTAINER               = azapi_resource.directive_artifacts_container.name
    COSMOS_ENDPOINT                        = azurerm_cosmosdb_account.main.endpoint
    DIRECTIVE_COSMOS_DATABASE              = azurerm_cosmosdb_sql_database.directives.name
    DIRECTIVE_CATALOG_CONTAINER            = azurerm_cosmosdb_sql_container.directive_catalog.name
    DIRECTIVE_MANDATE_CONTAINER            = azurerm_cosmosdb_sql_container.directive_mandates.name
    AZURE_SEARCH_ENDPOINT                  = "https://${azurerm_search_service.main.name}.search.windows.net"
    AZURE_SEARCH_API_VERSION               = "2024-07-01"
    AZURE_SEARCH_KNOWLEDGE_API_VERSION     = "2026-04-01"
    DIRECTIVE_SEARCH_INDEX                 = var.directive_search_index_name
    DIRECTIVE_SEARCH_KNOWLEDGE_SOURCE      = var.directive_search_knowledge_source_name
    DIRECTIVE_SEARCH_KNOWLEDGE_BASE        = var.directive_search_knowledge_base_name
    AZURE_OPENAI_ENDPOINT                  = local.foundry_agents_cognitive_endpoint
    AZURE_OPENAI_RESOURCE_URI              = local.foundry_agents_openai_resource_uri
    AZURE_OPENAI_API_VERSION               = "2025-04-01-preview"
    AZURE_OPENAI_EMBED_DEPLOYMENT          = azurerm_cognitive_deployment.foundry_agents_embedding.name
    AZURE_OPENAI_EMBED_MODEL               = "text-embedding-3-large"
    DIRECTIVE_EMBEDDING_DIMENSIONS         = "3072"
    DIRECTIVE_SUMMARY_DEPLOYMENT           = azurerm_cognitive_deployment.directive.name
    DIRECTIVE_SUMMARY_MODEL                = var.directive_model_name
    DIRECTIVE_KNOWLEDGE_MODEL_DEPLOYMENT   = azurerm_cognitive_deployment.directive_knowledge_planner.name
    DIRECTIVE_KNOWLEDGE_MODEL_NAME         = var.directive_knowledge_model_name
    DIRECTIVE_SOURCE_DIR                   = "/app/fixtures/pdf"
    DIRECTIVE_MANDATE_CSV                  = "/app/fixtures/mandatory/mand.csv"
    DIRECTIVE_PROCESSING_VERSION           = "directive-v1"
    DIRECTIVE_CHUNK_TOKEN_LIMIT            = "800"
    DIRECTIVE_CHUNK_OVERLAP_TOKENS         = "120"
    DIRECTIVE_SUMMARY_BATCH_TOKENS         = "60000"
    DIRECTIVE_SUMMARY_FULL_DOCUMENT_TOKENS = "180000"
  }
}

resource "azurerm_role_assignment" "directive_ingestion_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.directive_ingestion.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "directive_ingestion_blob_contributor" {
  scope                            = azapi_resource.directive_artifacts_container.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azurerm_user_assigned_identity.directive_ingestion.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "directive_ingestion_document_intelligence" {
  scope                            = azurerm_cognitive_account.directive_layout.id
  role_definition_name             = "Cognitive Services User"
  principal_id                     = azurerm_user_assigned_identity.directive_ingestion.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "directive_ingestion_search_service" {
  scope                            = azurerm_search_service.main.id
  role_definition_name             = "Search Service Contributor"
  principal_id                     = azurerm_user_assigned_identity.directive_ingestion.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "directive_ingestion_search_index" {
  scope                            = azurerm_search_service.main.id
  role_definition_name             = "Search Index Data Contributor"
  principal_id                     = azurerm_user_assigned_identity.directive_ingestion.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_cosmosdb_sql_role_assignment" "directive_ingestion_contributor" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = local.cosmos_data_contributor_role_definition_id
  principal_id        = azurerm_user_assigned_identity.directive_ingestion.principal_id
  scope               = local.directive_cosmos_database_scope
}

resource "azurerm_role_assignment" "directive_ingestion_openai_user" {
  scope                            = azapi_resource.foundry_agents.id
  role_definition_name             = "Cognitive Services OpenAI User"
  principal_id                     = azurerm_user_assigned_identity.directive_ingestion.principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_container_app_job" "directive_ingestion" {
  name                         = local.names.directive_job
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_retry_limit          = 1
  replica_timeout_in_seconds   = 7200
  workload_profile_name        = "Consumption"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.directive_ingestion.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.directive_ingestion.id
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "directive-ingestion"
      image   = local.placeholder_image
      cpu     = 1
      memory  = "2Gi"
      command = ["directive-ingest"]
      args    = ["run-daily"]

      dynamic "env" {
        for_each = local.directive_ingestion_env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.directive_ingestion_acr_pull,
    azurerm_role_assignment.directive_ingestion_blob_contributor,
    azurerm_role_assignment.directive_ingestion_document_intelligence,
    azurerm_role_assignment.directive_ingestion_search_service,
    azurerm_role_assignment.directive_ingestion_search_index,
    azurerm_role_assignment.directive_ingestion_openai_user,
    azurerm_cosmosdb_sql_role_assignment.directive_ingestion_contributor,
    azurerm_private_endpoint.directive_artifacts,
    azurerm_private_endpoint.directive_layout,
  ]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}
