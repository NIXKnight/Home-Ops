# Kubernetes resources that let OpenBao review tokens issued by the Talos
# cluster, plus the OpenBao kubernetes auth backend + its config. Scoped unit:
# reviewer SA, token Secret, ClusterRoleBinding, auth backend, backend config.
# KV mount, policy, role, and ESO-side objects are deferred to later units.

# Token reviewer ServiceAccount. OpenBao uses this SA's token to call the
# Kubernetes TokenReview API when validating client JWTs.
resource "kubernetes_service_account" "openbao_reviewer" {
  metadata {
    name      = var.reviewer_sa_name
    namespace = var.reviewer_sa_namespace

    labels = {
      "app.kubernetes.io/name"       = var.reviewer_sa_name
      "app.kubernetes.io/component"  = "openbao-auth"
      "app.kubernetes.io/managed-by" = "terragrunt"
    }
  }
}

# Bind the reviewer SA to system:auth-delegator so it can delegate token review
# to the API server's authentication layer (required by the Kubernetes auth
# backend's token_reviewer_jwt flow).
resource "kubernetes_cluster_role_binding" "openbao_reviewer" {
  metadata {
    name = "${var.reviewer_sa_name}-tokenreview-binding"

    labels = {
      "app.kubernetes.io/name"       = var.reviewer_sa_name
      "app.kubernetes.io/component"  = "openbao-auth"
      "app.kubernetes.io/managed-by" = "terragrunt"
    }
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = "system:auth-delegator"
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.openbao_reviewer.metadata[0].name
    namespace = kubernetes_service_account.openbao_reviewer.metadata[0].namespace
  }
}

# Long-lived service-account token Secret. The Kubernetes controller populates
# data["token"] asynchronously after creation; wait_for_service_account_token
# blocks Terraform until the Kubernetes controller populates data["token"], so
# downstream readers see a populated token.
resource "kubernetes_secret" "openbao_reviewer_token" {
  metadata {
    name      = "${var.reviewer_sa_name}-token"
    namespace = var.reviewer_sa_namespace

    annotations = {
      "kubernetes.io/service-account.name" = kubernetes_service_account.openbao_reviewer.metadata[0].name
    }

    labels = {
      "app.kubernetes.io/name"       = var.reviewer_sa_name
      "app.kubernetes.io/component"  = "openbao-auth"
      "app.kubernetes.io/managed-by" = "terragrunt"
    }
  }

  type                           = "kubernetes.io/service-account-token"
  wait_for_service_account_token = true

  depends_on = [kubernetes_service_account.openbao_reviewer]
}

# OpenBao kubernetes auth backend mount. Path is the mount point used by all
# downstream roles (deferred unit will add the role + policy).
resource "vault_auth_backend" "kubernetes" {
  type        = "kubernetes"
  path        = var.auth_path
  description = "Kubernetes auth backend for the Talos cluster"
}

# Backend config: wires the cluster API endpoint, CA, and the reviewer SA's JWT
# into OpenBao. disable_iss_validation matches the upstream default (on since
# Vault 1.9): the iss check is a local string compare of the JWT claim — never
# a network fetch — and is redundant because TokenReview is authoritative.
resource "vault_kubernetes_auth_backend_config" "cluster" {
  backend                = vault_auth_backend.kubernetes.path
  kubernetes_host        = var.kube_host
  kubernetes_ca_cert     = var.kube_ca
  token_reviewer_jwt     = kubernetes_secret.openbao_reviewer_token.data["token"]
  disable_iss_validation = true

  depends_on = [kubernetes_secret.openbao_reviewer_token]
}
