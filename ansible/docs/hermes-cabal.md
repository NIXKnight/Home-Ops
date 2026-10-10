# Hermes CABAL deployment on cawl-inferior

`playbooks/hermes.yml` is a standalone, gate-driven deployment. It does not use
or depend on the rejected WhatsApp daemon/webhook playbook or roles, and it does
not manage Kubernetes manifests.

## Managed components

- Hermes Agent `v2026.9.24`, package `0.21.5`, commit
  `f97608f178d1ffeca59860195ab7da295f7c8e5f`, in versioned source and venv
  paths with Python 3.13 and uv 0.9.30.
- Direct untrusted Hindsight MCP at the CABAL-private endpoint. Its positive
  allowlist is exactly `retain`, `recall`, `reflect`.
- WhatsApp-MCP-Server commit
  `0fab6208fd6e457efcd348d97f3e84e4832357e2`, cloned independently on the
  target. The Go 1.25.0 bridge build is static and reproducible; its SHA-256 is
  recorded next to the target binary.
- A client-only connection to external Kubernetes CNPG. No database container,
  volume, image, local listener, credential rotation, or database lifecycle is
  managed by this role.
- A locked Python 3.13 WhatsApp MCP environment. Hermes launches it over stdio
  with the exact uv binary, project, and venv. No MCP system service or port
  3000 exists.
- An Ansible-owned Hermes gateway system unit running as `saadali`. No API
  server or firewall opening is configured.

Both MCP servers disable resources, prompts, sampling, and elicitation and are
marked untrusted. WhatsApp's positive allowlist is exactly `get_messages` and
`get_contact`; send, write, search, enumeration, check, and poll tools are not
registered.

## Filesystem layout and access

The shared application root is `/opt/CABAL`:

- Hermes source: `/opt/CABAL/hermes/src/<commit>`
- Hermes venv: `/opt/CABAL/hermes/venvs/<version>-<commit>`
- Hermes home: `/opt/CABAL/hermes/home` (`config.yaml`, `AGENTS.md`, `SOUL.md`,
  `memories/USER.md`, and `skills/`)
- WhatsApp source: `/opt/CABAL/whatsapp/src/<commit>`
- bridge binary and manifest: `/opt/CABAL/whatsapp/bin/`
- WhatsApp MCP venv: `/opt/CABAL/whatsapp/venvs/mcp-<commit>`
- Go toolchain: `/opt/CABAL/toolchains/go/<version>`
- shared config: `/opt/CABAL/config`
- Hermes environment: `/opt/CABAL/config/hermes.env`
- bridge environment: `/opt/CABAL/config/whatsapp-bridge.env`
- PostgreSQL public CA: `/opt/CABAL/config/postgresql-ca.crt`

Root owns the shared application/config parents at mode 0755. `saadali` owns the
private mode-0700 Hermes home and the writable venv hierarchies. The bridge
account can traverse the root-owned application tree, execute the root-owned
bridge binary, and own `/var/lib/hermes-cabal-whatsapp`, but cannot read either
environment file. Both environment files are root:root mode 0600 and systemd
reads them before dropping privileges. The public CA is root:root mode 0644 so
the bridge can read it without exposing the adjacent environments. The mutable
bridge state remains `/var/lib/hermes-cabal-whatsapp`; Go build/module cache may
remain under `/var/cache/hermes-cabal`; units remain in `/etc/systemd/system`.

This relocation does not automatically remove old, partially staged
`/opt/hermes-*` trees. Inspect and remove obsolete trees separately only after
confirming the `/opt/CABAL` deployment.

## External CNPG contract

ExternalDNS publishes `postgresql.h.nixknight.pk` from the generic CNPG
primary-selector LoadBalancer Service `postgresql-lan`. The Service requests VIP
`192.168.0.172` and admits the trusted LAN source range `192.168.0.0/23` as a
shared endpoint for trusted LAN clients, not a WhatsApp-specific endpoint. The
bridge still uses the dedicated `whatsapp_bridge` database and role. The role
does not install or preserve an `/etc/hosts` mapping. When database preparation
is enabled, it idempotently removes any legacy block delimited by these markers
so partially staged hosts converge to DNS:

```text
# BEGIN ANSIBLE MANAGED HERMES CABAL WHATSAPP CNPG
# END ANSIBLE MANAGED HERMES CABAL WHATSAPP CNPG
```

`/etc/hosts` is system integration only for that cleanup, not desired content.
The fixed connection and DNS assertion values are:

- Kubernetes Service: `postgresql-lan`
- trusted LAN source range: `192.168.0.0/23`
- host: `postgresql.h.nixknight.pk`
- exact resolved address: `192.168.0.172`
- port: `5432`
- username/database: `whatsapp_bridge`
- TLS mode: `verify-full`
- CA destination: `/opt/CABAL/config/postgresql-ca.crt`

The approved public CA is a deployment input, not cluster state. It is supplied
at
`/home/saadali/Projects/Home-Ops-Internal/ansible/files/hermes-cabal/postgresql-ca.crt`
with SHA-256
`53169e2cc2fce8090a056422e01b01c13724bbe845fedcaf0fe665898f945e27`.
The private policy must declare that exact source and
`sha256:53169e2cc2fce8090a056422e01b01c13724bbe845fedcaf0fe665898f945e27`.
Do not read a Kubernetes Secret to obtain it. The focused offline contract
rejects an absent or malformed certificate and any file/checksum mismatch.
`hermes_cabal_whatsapp_database_prepare` remains false as a separate activation
gate; supplying the CA does not enable database preparation. Enabling
preparation without a present source and matching checksum fails before target
trust or connection state is changed. TLS verification rejects a malformed CA.
The role atomically installs the verified file as root:root mode 0644.

Preparation removes the obsolete marked hosts block, then requires the DNS lookup
via `getent ahostsv4` to resolve the hostname exclusively to the approved VIP. It
also
verifies TCP 5432, PostgreSQL STARTTLS with CA and hostname verification, and
authenticated readiness using only `SELECT 1`. It never executes a migration as
a probe. The bridge, not Ansible, owns its application schema migrations after
the operational acceptance gates pass.

## Section 14 binding

The preferred source is this Ansible Vault-encrypted private inventory file:

```text
Home-Ops-Internal/ansible/inventories/group_vars/ai_hosts/secrets-hermes-cabal-mcp.yml
```

It defines these exact variables:

- `WHATSAPP_BRIDGE_DB_PASSWORD` (externally generated, URL-safe, at least 32
  characters; accepted alphabet is `A-Z a-z 0-9 . _ ~ -`)
- `BIFROST_HERMES_VIRTUAL_KEY`
- `HINDSIGHT_API_KEY`

Edit the existing encrypted file with:

```bash
ansible-vault edit \
  --vault-password-file ../Home-Ops/ansible/.ansible_vault_password \
  ansible/inventories/group_vars/ai_hosts/secrets-hermes-cabal-mcp.yml
```

A nonempty encrypted inventory variable takes precedence over the same-named
controller environment variable. Environment lookup remains an emergency
fallback. Never pass these values through extra-vars or command-line arguments.
Secret-bearing lookups, probes, assertions, and renders are `no_log`. PostgreSQL
receives the password through `PGPASSWORD`, never argv. Failures expose fixed
diagnostics only. The verify-full DSN exists only in the bridge service account's
root-owned mode-0600 environment file; it includes
`sslrootcert=/opt/CABAL/config/postgresql-ca.crt`. Both service environments are
root-owned at mode 0600 and are read by systemd before privilege drop.

No task reads a Kubernetes Secret, prints the DSN/password, or supplies the
password on a process command line.

## Initial artifact staging

The private policy enables source, bridge, MCP, Hermes runtime, and home
preparation. Database preparation and both services remain disabled even though
the approved CA input is present; CA supply does not satisfy an activation gate.

From `ansible/`:

```bash
ansible-playbook \
  -i ../../Home-Ops-Internal/ansible/inventories/hosts.yml \
  playbooks/hermes.yml \
  --tags hermes \
  --limit cawl-inferior \
  -vv
```

This stages non-database artifacts only. It does not pair WhatsApp, contact
CNPG, or start either service. Do not use check mode as an offline substitute.

## Prepare and accept external CNPG

After independently re-verifying the supplied public CA and matching checksum,
set `hermes_cabal_whatsapp_database_prepare: true`, ensure the encrypted
`WHATSAPP_BRIDGE_DB_PASSWORD` inventory variable is present, and run:

```bash
ansible-playbook \
  -i ../../Home-Ops-Internal/ansible/inventories/hosts.yml \
  playbooks/hermes.yml \
  --tags whatsapp-bridge \
  --limit cawl-inferior \
  -vv
```

The bridge remains stopped. Independently verify the external backup/restore
position, verify the TLS identity and policy, and complete/approve the CNPG
PostgreSQL 18 migration. Only then set these non-secret private gates:

```yaml
hermes_cabal_whatsapp_database_accepted: true
hermes_cabal_whatsapp_database_backup_accepted: true
hermes_cabal_whatsapp_database_tls_accepted: true
hermes_cabal_whatsapp_database_pg18_migration_accepted: true
```

Ansible never performs those acceptance actions and never starts a bridge to use
migration as a health probe.

## Manual pairing gate

Pair only after all four external database gates are accepted, from an
interactive terminal on `cawl-inferior`. Do not redirect, tee, screenshot, log,
or otherwise capture the QR, and do not copy an existing session directory. The
bridge system service must remain stopped during pairing. Starting the exact
bridge is the first operation permitted to own application schema migrations.
The root shell reads the mode-0600 environment and `runuser` drops privilege
before executing the bridge; do not loosen the environment file mode for manual
pairing.

```bash
sudo systemctl stop hermes-cabal-whatsapp-bridge.service
sudo /bin/bash -c \
  'set -a; . /opt/CABAL/config/whatsapp-bridge.env; set +a; exec /usr/sbin/runuser -u hermes-wa-bridge -- /opt/CABAL/whatsapp/bin/whatsapp-bridge-0fab6208fd6e457efcd348d97f3e84e4832357e2'
```

After the interactive process reports a connected account, privately verify
`GET http://127.0.0.1:8080/api/status` has both `state: CONNECTED` and
`is_connected: true`, then stop the foreground process. HTTP 200 alone is not
acceptance. Record only a category-level acceptance, never the QR, device state,
account identifiers, messages, or response body.

Set these non-secret private variables only after that gate:

```yaml
hermes_cabal_whatsapp_pairing_accepted: true
hermes_cabal_whatsapp_readiness_accepted: true
hermes_cabal_whatsapp_bridge_enabled: true
```

Reconcile and verify the bridge:

```bash
ansible-playbook \
  -i ../../Home-Ops-Internal/ansible/inventories/hosts.yml \
  playbooks/hermes.yml \
  --tags whatsapp-bridge,hermes-verify \
  --limit cawl-inferior \
  -vv
```

The role starts the service only when database, backup, TLS, PostgreSQL 18
migration, pairing, and CONNECTED-readiness gates are all true. It independently
parses both required readiness fields. It does not pair, send, notify, poll, or
expose a network MCP service.

## Enable WhatsApp MCP and Hermes

After bridge readiness is accepted, set:

```yaml
hermes_cabal_agent_whatsapp_mcp_enabled: true
```

Render and verify the config:

```bash
ansible-playbook \
  -i ../../Home-Ops-Internal/ansible/inventories/hosts.yml \
  playbooks/hermes.yml \
  --tags hermes-home,whatsapp-mcp,hermes-verify \
  --limit cawl-inferior \
  -vv
```

Finally, after reviewing the rendered policy and all inference/Hindsight gates,
set both:

```yaml
hermes_cabal_agent_activation_accepted: true
hermes_cabal_agent_service_enabled: true
```

Activate only the Hermes service:

```bash
ansible-playbook \
  -i ../../Home-Ops-Internal/ansible/inventories/hosts.yml \
  playbooks/hermes.yml \
  --tags hermes-service,hermes-verify \
  --limit cawl-inferior \
  -vv
```

Managed changes restart only services whose activation gates are true. Setting
an activation variable false converges the corresponding unit to stopped and
disabled without deleting external database state, pairing state, source, venv,
or home state.

## Tags

- `hermes`
- `hermes-runtime`
- `hermes-home`
- `hindsight-mcp`
- `whatsapp-bridge`
- `whatsapp-mcp`
- `hermes-service`
- `hermes-verify`

## Offline validation

These commands do not contact managed hosts:

```bash
yamllint playbooks/hermes.yml roles/hermes_cabal_agent roles/hermes_cabal_whatsapp
ansible-lint playbooks/hermes.yml roles/hermes_cabal_agent roles/hermes_cabal_whatsapp
ansible-playbook -i localhost, --syntax-check playbooks/hermes.yml
python3 tests/hermes_contract.py
```
