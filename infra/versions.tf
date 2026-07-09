terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.20"
    }
    azapi = {
      source  = "azure/azapi"
      version = "~> 2.2"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  features {
    cognitive_account {
      purge_soft_delete_on_destroy = true
    }
  }
  # Resource providers are registered out-of-band via scripts/register_providers.sh
  resource_provider_registrations = "none"
}

provider "azapi" {
  subscription_id = var.subscription_id
}
