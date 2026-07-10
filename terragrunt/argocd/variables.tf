variable "kube_host" {
  description = "Kubernetes API server endpoint (https://host:6443) from the Talos cluster unit."
  type        = string
}

variable "kube_ca" {
  description = "Cluster CA certificate (decoded PEM) for the helm provider."
  type        = string
  sensitive   = true
}

variable "kube_client_cert" {
  description = "Admin client certificate (decoded PEM) for the helm provider."
  type        = string
  sensitive   = true
}

variable "kube_client_key" {
  description = "Admin client key (decoded PEM) for the helm provider."
  type        = string
  sensitive   = true
}

variable "argocd_chart_version" {
  description = "Pinned version of the argo-cd Helm chart (argoproj/argo-helm)."
  type        = string
}

variable "argocd_namespace" {
  description = "Namespace into which ArgoCD is installed."
  type        = string
  default     = "argocd"
}

variable "github_owner" {
  description = "GitHub owner/org of the Internal repo; sets the github provider's default owner for the deploy key."
  type        = string
}

variable "argocd_repo_name" {
  description = "Name of the private Internal repo that receives the read-only ArgoCD deploy key."
  type        = string
}

variable "argocd_repo_url" {
  description = "SSH clone URL of the Internal repo; must exactly match the root Application's spec.source.repoURL."
  type        = string
}

variable "argocd_values" {
  description = "ArgoCD Helm values (YAML string), sourced from the Internal repo's terragrunt/argocd/unit.hcl via read_terragrunt_config. NOT marked sensitive: it holds only chart configuration and the root-app repo pointer, never credentials. The repo credential is now Tofu-managed by this unit (tls_private_key -> GitHub deploy key -> repository Secret); admin secrets remain out of band. Keeping it non-sensitive preserves readable plan diffs of the bootstrap config."
  type        = string
}
