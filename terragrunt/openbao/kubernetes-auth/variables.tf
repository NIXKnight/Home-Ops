variable "openbao_addr" {
  description = "OpenBao API address (https://openbao.<domain>). Supplied via OPENBAO_ADDR env var at apply time."
  type        = string

  validation {
    # https only: the server also listens plain-HTTP on 0.0.0.0:8200, and an
    # http:// address would send the root token cleartext over the LAN. Empty
    # string is exempt so offline validate/plan (get_env fallback) still works.
    condition     = var.openbao_addr == "" || startswith(var.openbao_addr, "https://")
    error_message = "openbao_addr must start with https:// (plain http would expose the token on the LAN)."
  }
}

variable "openbao_token" {
  description = "OpenBao root/auth token. Supplied via OPENBAO_TOKEN env var at apply time (Operator decrypts from escrowed secrets-openbao.yml)."
  type        = string
  sensitive   = true
}

variable "kube_host" {
  description = "Kubernetes API server endpoint (https://host:6443) from the Talos cluster unit."
  type        = string
}

variable "kube_ca" {
  description = "Cluster CA certificate (decoded PEM) for the kubernetes provider."
  type        = string
  sensitive   = true
}

variable "kube_client_cert" {
  description = "Admin client certificate (decoded PEM) for the kubernetes provider."
  type        = string
  sensitive   = true
}

variable "kube_client_key" {
  description = "Admin client key (decoded PEM) for the kubernetes provider."
  type        = string
  sensitive   = true
}

variable "reviewer_sa_name" {
  description = "Token reviewer ServiceAccount name created on the Talos cluster."
  type        = string
}

variable "reviewer_sa_namespace" {
  description = "Namespace in which the reviewer ServiceAccount is created."
  type        = string
}

variable "auth_path" {
  description = "OpenBao kubernetes auth backend mount path."
  type        = string
}
