# OpenBao secrets engines + the External Secrets Operator (ESO) consumption grant:
# the engine mounts, the read policy scoped to the ESO read subtrees, and the
# kubernetes auth role ESO logs in as. The kubernetes auth BACKEND itself is
# mounted by the sibling kubernetes-auth unit; this unit only adds a role on it
# (backend = var.auth_path, single-sourced from that unit's config).
#
# FIRST APPLY: if an engine already exists at a target path server-side, the mount
# create fails with "path is already in use". Run `bao secrets list` first and, if
# present, import it, e.g. `terragrunt import 'vault_mount.engines["<mount>"]' <mount>`,
# before applying.

# Secrets engines, one per var.engines entry (keyed by mount path). MOUNT-PER-ESTATE:
# each estate (a cluster, the VM fleet, ...) gets its OWN mount holding only its own
# secrets in a FLAT <app>/<secret> (or <service>/<secret>) layout -- no shared cross-estate
# mount, no inner cluster prefix. type/options default to KV v2.
# Per-engine removal must be a deliberate two-step edit+apply (drop prevent_destroy on that
# instance, then apply): a mount destroy wipes ALL secrets under that engine.
resource "vault_mount" "engines" {
  for_each = var.engines

  path        = each.key
  type        = each.value.type
  options     = each.value.options
  description = each.value.description

  lifecycle {
    prevent_destroy = true
  }
}

# Read policy for the External Secrets Operator: a WHOLE-MOUNT grant on the ESO mount
# (vault_mount.engines[var.eso_kv_mount] -- a hard reference, so a missing eso_kv_mount
# fails at plan and the policy keeps its implicit dependency on that mount). Per the
# Operator directive "ESO reads everything": under the mount-per-estate model the cluster
# mount holds ONLY that cluster's secrets in a FLAT <app>/<secret> layout, so read-all on
# the mount is exactly the intended scope (data/* read; metadata/* read+list for ESO find).
resource "vault_policy" "eso" {
  name = var.eso_policy_name

  policy = join("\n\n", [
    "path \"${vault_mount.engines[var.eso_kv_mount].path}/data/*\" {\n  capabilities = [\"read\"]\n}",
    "path \"${vault_mount.engines[var.eso_kv_mount].path}/metadata/*\" {\n  capabilities = [\"read\", \"list\"]\n}",
  ])
}

# Kubernetes auth role ESO authenticates as. Bound to the ESO ServiceAccount and
# gated on the audience the ClusterSecretStore presents. Grants the read policy.
# token_policies references vault_policy.eso.name (value == var.eso_policy_name)
# so the role has an implicit dependency on the policy -- the only in-unit
# ordering edge; the auth backend (var.auth_path) pre-exists via the sibling unit.
resource "vault_kubernetes_auth_backend_role" "eso" {
  backend                          = var.auth_path
  role_name                        = var.eso_role_name
  bound_service_account_names      = [var.eso_sa_name]
  bound_service_account_namespaces = [var.eso_sa_namespace]

  # MUST stay lockstep with the ClusterSecretStore audiences: [openbao]
  audience       = var.eso_audience
  token_policies = [vault_policy.eso.name]

  # Short-lived login tokens: ESO re-authenticates every refresh cycle, so the
  # month-long default TTL is surplus leak-lifetime. 10m default / 1h ceiling.
  token_ttl     = 600
  token_max_ttl = 3600
}
