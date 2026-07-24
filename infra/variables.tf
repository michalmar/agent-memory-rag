variable "subscription_id" {
  type        = string
  description = "Target Azure subscription ID."
}

variable "location" {
  type        = string
  default     = "eastus2"
  description = "Azure region. Must support Foundry gpt-4o-mini + text-embedding-3-large + AI Search."
}

variable "search_location" {
  type        = string
  default     = "westeurope"
  description = "Region for public Entra/RBAC-only Azure AI Search. Separate from var.location because eastus2 is out of Search capacity for this subscription."
}

variable "resource_group_name" {
  type    = string
  default = "rg-agent-memory-rag"
}

variable "name_prefix" {
  type        = string
  default     = "agmem"
  description = "Short prefix for resource names (lowercase alphanumeric)."
}

variable "entra_tenant_id" {
  type        = string
  description = "Microsoft Entra tenant ID used to authenticate end users."

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.entra_tenant_id))
    error_message = "entra_tenant_id must be a tenant GUID."
  }
}

variable "entra_client_id" {
  type        = string
  description = "Client ID of the single-tenant SPA/API app registration."

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.entra_client_id))
    error_message = "entra_client_id must be an application GUID."
  }
}

variable "tags" {
  type = map(string)
  default = {
    project = "agent-memory-rag"
    env     = "demo"
    owner   = "mimarusa"
  }
}

variable "vnet_address_space" {
  type    = string
  default = "10.42.0.0/16"
}

variable "chat_model_name" {
  type    = string
  default = "gpt-4o-mini"
}

variable "chat_model_version" {
  type    = string
  default = "2024-07-18"
}

variable "chat_model_capacity" {
  type        = number
  default     = 30
  description = "TPM (thousands) capacity for the chat deployment."
}

variable "embedding_model_name" {
  type    = string
  default = "text-embedding-3-large"
}

variable "embedding_model_version" {
  type    = string
  default = "1"
}

variable "embedding_model_capacity" {
  type    = number
  default = 30
}

variable "search_sku" {
  type    = string
  default = "basic"
}

variable "foundry_prompt_enabled" {
  type        = bool
  default     = false
  description = "Expose the native Foundry Prompt Agent after its release and smoke tests pass."
}

variable "foundry_hosted_enabled" {
  type        = bool
  default     = false
  description = "Expose the Hosted MAF Agent after identity, gateway, and isolation tests pass."
}

variable "foundry_prompt_agent_name" {
  type    = string
  default = "customer-support-prompt"
}

variable "foundry_hosted_agent_name" {
  type    = string
  default = "customer-support-maf-hosted"
}

variable "agent_release_id" {
  type    = string
  default = "mcp-agent-id-20260720-r6"
}

variable "hosted_agent_principal_ids" {
  type        = set(string)
  default     = []
  description = "Foundry project and published Agent Identity principal IDs allowed to invoke protected application tools."

  validation {
    condition = alltrue([
      for principal_id in var.hosted_agent_principal_ids :
      can(regex("^[0-9a-fA-F-]{36}$", principal_id))
    ])
    error_message = "Every Hosted Agent principal ID must be a GUID."
  }
}

variable "support_hosted_agent_principal_ids" {
  type        = set(string)
  default     = []
  description = "Support Hosted Agent principals. Empty preserves the legacy hosted_agent_principal_ids fallback."

  validation {
    condition = alltrue([
      for principal_id in var.support_hosted_agent_principal_ids :
      can(regex("^[0-9a-fA-F-]{36}$", principal_id))
    ])
    error_message = "Every support Hosted Agent principal ID must be a GUID."
  }
}

variable "directive_hosted_agent_principal_ids" {
  type        = set(string)
  default     = []
  description = "Directive Hosted Agent principals allowed to invoke only directive tools."

  validation {
    condition = alltrue([
      for principal_id in var.directive_hosted_agent_principal_ids :
      can(regex("^[0-9a-fA-F-]{36}$", principal_id))
    ])
    error_message = "Every directive Hosted Agent principal ID must be a GUID."
  }
}

variable "foundry_iq_connection_name" {
  type    = string
  default = "customer-support-kb-mcp"
}

variable "foundry_application_tools_connection_name" {
  type    = string
  default = "customer-support-tools-mcp"
}

variable "directive_model_name" {
  type        = string
  default     = "gpt-5.6-sol"
  description = "Existing GPT deployment adopted for the directive Hosted Agent."
}

variable "directive_model_version" {
  type        = string
  default     = "2026-07-09"
  description = "Exact model version of the existing directive deployment."
}

variable "directive_model_capacity" {
  type        = number
  default     = 250
  description = "Existing Global Standard capacity, in thousands of tokens per minute."

  validation {
    condition     = var.directive_model_capacity > 0
    error_message = "directive_model_capacity must be greater than zero."
  }
}

variable "directive_knowledge_model_deployment" {
  type        = string
  default     = "gpt-5-nano-directive-kb"
  description = "Dedicated Azure AI Search knowledge-base planner deployment."
}

variable "directive_knowledge_model_name" {
  type        = string
  default     = "gpt-5-nano"
  description = "GA-supported Azure AI Search knowledge-base planner model."
}

variable "directive_knowledge_model_version" {
  type        = string
  default     = "2025-08-07"
  description = "Exact planner model version used by the stable Search API."
}

variable "directive_knowledge_model_capacity" {
  type        = number
  default     = 10
  description = "Global Standard planner capacity, in thousands of tokens per minute."

  validation {
    condition     = var.directive_knowledge_model_capacity > 0
    error_message = "directive_knowledge_model_capacity must be greater than zero."
  }
}

variable "directive_agent_enabled" {
  type        = bool
  default     = false
  description = "Instantiate the directive Hosted Agent runtime. Keep false through the Phase 2 data-infrastructure release."
}

variable "directive_agent_visible" {
  type        = bool
  default     = false
  description = "Expose the directive agent from /agents. Keep false until the final rollout gate."
}

variable "directive_foundry_agent_name" {
  type    = string
  default = "directive-rag-maf-hosted"
}

variable "directive_agent_release_id" {
  type    = string
  default = "directive-rag-20260723-r2"
}

variable "directive_search_knowledge_base_name" {
  type    = string
  default = "directive-kb-v1"
}

variable "directive_search_knowledge_source_name" {
  type    = string
  default = "directive-chunks-ks-v1"
}

variable "directive_search_index_name" {
  type    = string
  default = "directive-chunks-v1"
}

variable "directive_cosmos_database_name" {
  type    = string
  default = "directives"
}

variable "directive_catalog_container_name" {
  type    = string
  default = "catalog"
}

variable "directive_mandates_container_name" {
  type    = string
  default = "user_mandates"
}

variable "directive_artifacts_container_name" {
  type    = string
  default = "directive-artifacts"
}

variable "directive_storage_replication_type" {
  type        = string
  default     = "LRS"
  description = "Artifact storage redundancy. LRS is the MVP default; use ZRS for a production regional-HA deployment."

  validation {
    condition = contains(
      ["LRS", "ZRS", "GRS", "GZRS", "RAGRS", "RAGZRS"],
      var.directive_storage_replication_type,
    )
    error_message = "directive_storage_replication_type must be a supported Standard storage replication type."
  }
}

variable "directive_artifact_retention_days" {
  type        = number
  default     = 30
  description = "Soft-delete retention for directive blobs and containers."

  validation {
    condition = (
      var.directive_artifact_retention_days >= 1 &&
      var.directive_artifact_retention_days <= 365
    )
    error_message = "directive_artifact_retention_days must be between 1 and 365."
  }
}
