include "root" {
  path   = find_in_parent_folders("root.hcl")
  expose = true
}

locals {
  # ArgoCD Helm values live in the Internal repo as HCL (Operator rule: values belong
  # in unit.hcl, never a standalone values.yaml). internal_repo_path is exposed by
  # root.hcl from TERRAGRUNT_INTERNAL_REPO_PATH -- the same mechanism the talos-cluster
  # unit uses. read_terragrunt_config surfaces the unit's locals; the value is passed
  # to the module as a rendered YAML string (yamlencode below), so main.tf is unchanged.
  argocd = read_terragrunt_config("${include.root.locals.internal_repo_path}/terragrunt/argocd/unit.hcl")
}

# Wire the live Kubernetes API connection details from the Talos cluster unit.
# kubeconfig_data carries already base64-DECODED PEM strings (host + CA + client
# cert/key), so they pass straight to the helm provider's kubernetes block.
# mock_outputs cover offline init/validate/plan; they are dummy values only.
dependency "talos_cluster" {
  config_path = "../talos-cluster"

  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan"]
  mock_outputs = {
    kubeconfig_data = {
      host                   = "https://127.0.0.1:6443"
      cluster_ca_certificate = "mock"
      client_certificate     = "mock"
      client_key             = "mock"
    }
  }
}

inputs = {
  kube_host        = dependency.talos_cluster.outputs.kubeconfig_data.host
  kube_ca          = dependency.talos_cluster.outputs.kubeconfig_data.cluster_ca_certificate
  kube_client_cert = dependency.talos_cluster.outputs.kubeconfig_data.client_certificate
  kube_client_key  = dependency.talos_cluster.outputs.kubeconfig_data.client_key

  argocd_chart_version = "10.0.0"

  # Repo identity from the Internal unit.hcl, feeding the deploy-key + repository-Secret
  # resources (main.tf). Single-sourced alongside argocd_values so the root Application's
  # repoURL, the GitHub deploy key, and the in-cluster Secret url stay in lockstep.
  github_owner     = local.argocd.locals.github_owner
  argocd_repo_name = local.argocd.locals.argocd_repo_name
  argocd_repo_url  = local.argocd.locals.argocd_repo_url

  # Rendered from the Internal repo's HCL unit (local.argocd above) to the YAML string
  # main.tf still expects. The module (var/main.tf) is unchanged.
  argocd_values = yamlencode(local.argocd.locals.argocd_values)
}
