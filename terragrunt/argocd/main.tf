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

# --- Bootstrap argoproj.io objects, applied AFTER the release via kbst ---------------
#
# The AppProject + root app-of-apps used to ride the helm release as chart extraObjects,
# but the argo-cd chart templates its CRDs as ordinary manifests, so helm's pre-install
# manifest build validates these two argoproj.io CRs against cluster discovery BEFORE the
# chart installs its own CRDs -> "no matches for kind ... ensure CRDs are installed first"
# -> atomic abort (first-apply failure, apply log 2026-07-11). Applying them as separate
# kbst kustomization_resource objects AFTER helm_release.argocd sidesteps the gate: by then
# the CRDs are registered. manifest is jsonencode() of the Internal-sourced maps (fully
# known at plan; no helm binary, no plan-time network). Mirrors the talos-cluster unit's
# Cilium L2 CR pattern.
resource "kustomization_resource" "bootstrap_project" {
  manifest = jsonencode(var.argocd_bootstrap_project)

  depends_on = [helm_release.argocd]
}

# Root Application strictly AFTER the AppProject it references (spec.project = "bootstrap").
# ArgoCD tolerates a transient "project not found", but ordering avoids the reconcile churn.
resource "kustomization_resource" "root_application" {
  manifest = jsonencode(var.argocd_root_application)

  depends_on = [kustomization_resource.bootstrap_project]
}
