# Writes each var.secrets entry as a KV v2 secret at <kv_mount>/<name>. The mount, the
# External Secrets Operator read policy, and the ESO auth role are provisioned by the
# sibling openbao/kv unit; the mount is guaranteed to pre-exist via the
# dependencies{paths=["../kv"]} edge in terragrunt.hcl.
#
# FIRST CONSUMER: <mount>/authentik-outpost = { token = <authentik outpost token> }.
# ESO reads it (whole-mount ESO policy) and projects it into the cluster proxy outpost's
# token Secret so the Phase-4 outpost Deployment can authenticate back to authentik.
#
# STATE CAVEAT (accepted repo posture): data_json stores the written secret VALUES in this
# unit's OpenTofu state. State lives in the pbkdf2-encrypted PostgreSQL backend, so it is
# encrypted at rest, but the plaintext is recoverable by anyone able to decrypt state.
#   HARDENING OPTION (hashicorp/vault provider v5): switch to the write-only pair
#     data_json_wo         = jsonencode(each.value.data)
#     data_json_wo_version = each.value.version   # bump to push a new value
#   which keeps the secret OUT of state entirely. Trade-off: write-only values are not read
#   back, so an upstream change (e.g. a rotated authentik token flowing in through the
#   dependency output) is NOT auto-detected -- the version counter must be bumped by hand.
#   data_json is kept here precisely because the outpost token is dependency-seeded and
#   SHOULD auto-reconcile when authentik rotates it. Revisit per-secret if a value warrants
#   state-secrecy over auto-reconcile (that would also add a `version` field to var.secrets).
resource "vault_kv_secret_v2" "seeds" {
  for_each = var.secrets

  mount     = var.kv_mount
  name      = each.key
  data_json = jsonencode(each.value.data)
}
