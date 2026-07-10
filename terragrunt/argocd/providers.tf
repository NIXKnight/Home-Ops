# helm provider v3 changed `kubernetes` from a nested block to an attribute.
provider "helm" {
  kubernetes = {
    host                   = var.kube_host
    cluster_ca_certificate = var.kube_ca
    client_certificate     = var.kube_client_cert
    client_key             = var.kube_client_key
  }
}

# kubernetes provider wired from the same 4 kube vars the helm provider uses (host +
# decoded PEM CA + client cert/key from the talos-cluster dependency). It creates the
# in-cluster ArgoCD repository Secret in main.tf. Mirrors the openbao unit's block.
provider "kubernetes" {
  host                   = var.kube_host
  cluster_ca_certificate = var.kube_ca
  client_certificate     = var.kube_client_cert
  client_key             = var.kube_client_key
}

# github provider for the read-only deploy key on the internal config repository. The
# token comes EXCLUSIVELY from the GITHUB_TOKEN env var at plan/apply time (a fine-grained
# PAT for that repository; see the repo identity locals read from the internal unit
# config) -- it never appears in code or state. owner scopes the provider to the repo's owner.
provider "github" {
  owner = var.github_owner
}
