resource "helm_release" "argocd" {
  name             = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argocd_chart_version
  namespace        = var.argocd_namespace
  create_namespace = true
  atomic           = true

  values = [var.argocd_values]
}

# --- Private-repo SSH credential, generated and managed end-to-end -----------------
#
# The Internal repo is private and fetched over SSH. This unit mints the credential
# and wires it through GitHub and the cluster: generate key -> register a read-only
# GitHub deploy key -> create the in-cluster ArgoCD repository Secret. No key material
# is ever hand-handled.

# ED25519 keypair born at APPLY time. The private half lives ONLY in Tofu state
# (encrypted PG backend) and the in-cluster Secret below; it is sensitive-masked in
# plan output and never printed. Rotation:
#   terragrunt apply -- -replace=tls_private_key.argocd_repo
# regenerates the key and swaps the GitHub deploy key in the same apply (old key
# revoked as the new one is created), so any earlier state versions retain only dead
# credentials. ED25519 has no classic PEM encoding here -- the *_openssh attributes
# are the ones consumed below.
resource "tls_private_key" "argocd_repo" {
  algorithm = "ED25519"
}

# Read-only deploy key on the Internal repo. GitHub takes the OpenSSH public key;
# read_only = true lets it pull but never push. Replacing the tls key above forces
# this deploy key to be recreated (old revoked, new added) within one apply.
resource "github_repository_deploy_key" "argocd_internal" {
  repository = var.argocd_repo_name
  title      = "argocd-repo-access"
  key        = tls_private_key.argocd_repo.public_key_openssh
  read_only  = true
}

# The one and only declaration of the Internal repo to ArgoCD: this single Secret both
# REGISTERS the repo and supplies its SSH credential, which is why the chart's
# configs.repositories stays deliberately empty. Replaces the manual
# bootstrap/repo-secret.TEMPLATE.yaml flow. depends_on the release -- the argocd
# namespace is created by helm_release.argocd (create_namespace = true).
resource "kubernetes_secret" "repo_internal_config" {
  metadata {
    name      = "repo-internal-config"
    namespace = var.argocd_namespace
    labels = {
      "argocd.argoproj.io/secret-type" = "repository"
    }
  }

  type = "Opaque"

  data = {
    type          = "git"
    name          = "argocd-internal-config-ro"
    url           = var.argocd_repo_url
    sshPrivateKey = tls_private_key.argocd_repo.private_key_openssh
  }

  depends_on = [helm_release.argocd]
}
