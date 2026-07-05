terraform {
  required_version = ">= 1.8"

  required_providers {
    vault = {
      source  = "hashicorp/vault"
      version = "~> 5"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2"
    }
  }
}
