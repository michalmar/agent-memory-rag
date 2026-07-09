variable "subscription_id" {
  type        = string
  description = "Target Azure subscription ID."
}

variable "location" {
  type        = string
  default     = "eastus2"
  description = "Azure region. Must support Foundry gpt-4o-mini + text-embedding-3-large + AI Search."
}

variable "postgres_location" {
  type        = string
  default     = "northcentralus"
  description = "Region for PostgreSQL Flexible Server. Separate from var.location because eastus2 is offer-restricted for Postgres on this subscription. Reached from the eastus2 VNet via a cross-region private endpoint."
}

variable "search_location" {
  type        = string
  default     = "westeurope"
  description = "Region for Azure AI Search. Separate from var.location because eastus2 is out of Search capacity for this subscription. Reached from the eastus2 VNet via a cross-region private endpoint."
}

variable "resource_group_name" {
  type        = string
  default     = "rg-agent-memory-rag"
}

variable "name_prefix" {
  type        = string
  default     = "agmem"
  description = "Short prefix for resource names (lowercase alphanumeric)."
}

variable "tags" {
  type = map(string)
  default = {
    project = "agent-memory-rag"
    env     = "demo"
    owner   = "mimarusa"
  }
}

variable "postgres_admin_login" {
  type    = string
  default = "pgadmin"
}

variable "postgres_admin_password" {
  type        = string
  sensitive   = true
  description = "PostgreSQL administrator password (set via TF_VAR_postgres_admin_password)."
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
