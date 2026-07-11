variable "openbao_addr" {
  description = "OpenBao API address (https://openbao.<domain>). Supplied via OPENBAO_ADDR env var at apply time."
  type        = string

  validation {
    # https only: an http:// address would send the auth token in cleartext over
    # the LAN. Empty string is exempt so offline validate/plan (get_env fallback)
    # still works.
    condition     = var.openbao_addr == "" || startswith(var.openbao_addr, "https://")
    error_message = "openbao_addr must start with https:// (plain http would expose the token on the LAN)."
  }
}

variable "openbao_token" {
  description = "OpenBao root/auth token. Supplied via OPENBAO_TOKEN env var at apply time (Operator decrypts from escrowed secrets-openbao.yml)."
  type        = string
  sensitive   = true
}

variable "engines" {
  description = "Secrets engines to mount, keyed by mount path. type defaults to \"kv\" and options to { version = \"2\" } (KV v2), so a bare {} value mounts a KV v2 engine."
  type = map(object({
    type        = optional(string, "kv")
    options     = optional(map(string), { version = "2" })
    description = optional(string, "")
  }))

  validation {
    # Keys become mount paths; a leading/trailing slash or empty key would yield a
    # malformed mount path.
    condition = alltrue([
      for path in keys(var.engines) :
      length(path) > 0 && !startswith(path, "/") && !endswith(path, "/")
    ])
    error_message = "engines keys (mount paths) must be non-empty and contain no leading or trailing slashes."
  }
}

variable "eso_kv_mount" {
  description = "Mount path (must be a key of var.engines) the ESO policy/store read. The policy references vault_mount.engines[this], so a value absent from engines fails loudly at plan."
  type        = string
}

variable "auth_path" {
  description = "OpenBao kubernetes auth backend mount path. Single-sourced from the kubernetes-auth unit's openbao_config.auth_path so the role's backend can never drift from the mounted backend."
  type        = string
}

variable "eso_policy_name" {
  description = "Name of the OpenBao policy granting the External Secrets Operator read access to its KV paths."
  type        = string
}

variable "eso_role_name" {
  description = "Kubernetes auth backend role name the ESO ClusterSecretStore logs in as."
  type        = string
}

variable "eso_sa_name" {
  description = "ServiceAccount name bound to the ESO role (must match the ClusterSecretStore serviceAccountRef.name)."
  type        = string
}

variable "eso_sa_namespace" {
  description = "Namespace of the ESO ServiceAccount bound to the role."
  type        = string
}

variable "eso_audience" {
  description = "JWT audience the ESO role requires. MUST stay lockstep with the ClusterSecretStore audiences (\"openbao\")."
  type        = string
}
