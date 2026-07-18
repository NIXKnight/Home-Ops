variable "openbao_addr" {
  description = "OpenBao API address (https://openbao.<domain>). Supplied via OPENBAO_ADDR env var at apply time."
  type        = string

  validation {
    # https only: an http:// address would send the auth token in cleartext over the LAN.
    # Empty string is exempt so offline validate/plan (get_env fallback) still works.
    condition     = var.openbao_addr == "" || startswith(var.openbao_addr, "https://")
    error_message = "openbao_addr must start with https:// (plain http would expose the token on the LAN)."
  }
}

variable "openbao_token" {
  description = "OpenBao root/auth token. Supplied via OPENBAO_TOKEN env var at apply time (Operator decrypts from the escrowed secrets-openbao.yml)."
  type        = string
  sensitive   = true
}

variable "kv_mount" {
  description = "KV v2 mount path secrets are written under. Single-sourced from the openbao/kv unit's eso_kv_mount so it cannot drift from the mounted engine."
  type        = string

  validation {
    condition     = length(var.kv_mount) > 0 && !startswith(var.kv_mount, "/") && !endswith(var.kv_mount, "/")
    error_message = "kv_mount must be non-empty and contain no leading or trailing slashes."
  }
}

variable "secrets" {
  description = "KV v2 secrets to write, keyed by secret name (the path under kv_mount). Each value's `data` map (string->string) is JSON-encoded into the secret. Example: { \"authentik-outpost\" = { data = { token = \"...\" } } }."
  type = map(object({
    data = map(string)
  }))

  validation {
    # Keys become the secret path under the mount; a leading/trailing slash or an empty key
    # would yield a malformed KV path.
    condition = alltrue([
      for name in keys(var.secrets) :
      length(name) > 0 && !startswith(name, "/") && !endswith(name, "/")
    ])
    error_message = "secrets keys (secret names) must be non-empty and contain no leading or trailing slashes."
  }
}
