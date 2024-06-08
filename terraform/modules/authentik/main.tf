data "authentik_flow" "default_authorization_flow" {
  slug = "default-provider-authorization-implicit-consent"
}

data "authentik_certificate_key_pair" "default" {
  name = "authentik Self-signed Certificate"
}

data "authentik_scope_mapping" "scope_mappings" {
  for_each = { for mapping in local.authentik_config.scope_mappings : mapping.name => mapping }
  managed_list = each.value.managed_list
}

resource "authentik_provider_oauth2" "oauth2_providers" {
  depends_on = [ data.authentik_scope_mapping.scope_mappings ]

  for_each          = { for provider in local.authentik_config.providers : provider.name => provider }
  name              = each.value.name
  client_id         = each.value.client_id
  client_secret     = each.value.client_secret
  authorization_flow = data.authentik_flow.default_authorization_flow.id
  property_mappings = data.authentik_scope_mapping.scope_mappings[each.value.property_mappings].ids
  signing_key       = data.authentik_certificate_key_pair.default.id
  redirect_uris     = each.value.redirect_uris
}

resource "authentik_application" "applications" {
  for_each          = { for app in local.authentik_config.applications : app.name => app }
  name              = each.value.name
  slug              = each.value.slug
  protocol_provider = authentik_provider_oauth2.oauth2_providers[each.value.provider].id
}

resource "authentik_user" "users" {
  for_each = { for user in local.authentik_config.users : user.username => user }
  username = each.value.username
  email    = each.value.email
  name     = each.value.name
  password = each.value.password
}

resource "authentik_group" "groups" {
  for_each = { for group in local.authentik_config.groups : group.name => group }
  name     = each.value.name
  users    = [for user in each.value.users : authentik_user.users[user].id]
}
