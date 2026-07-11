terraform {
  required_version = ">= 1.8"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2"
    }
    kustomization = {
      source  = "kbst/kustomization"
      version = "~> 0.9"
    }
  }
}
