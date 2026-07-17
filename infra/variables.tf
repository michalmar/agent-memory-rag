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
  default = "dual-foundry-001"
}

variable "hosted_agent_principal_ids" {
  type        = set(string)
  default     = []
  description = "Foundry-created Hosted Agent principal IDs allowed to invoke the private tool gateway."

  validation {
    condition = alltrue([
      for principal_id in var.hosted_agent_principal_ids :
      can(regex("^[0-9a-fA-F-]{36}$", principal_id))
    ])
    error_message = "Every Hosted Agent principal ID must be a GUID."
  }
}

variable "foundry_iq_connection_name" {
  type    = string
  default = "customer-support-kb-mcp"
}
