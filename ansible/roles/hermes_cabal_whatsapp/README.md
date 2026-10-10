# hermes_cabal_whatsapp

Stages the exact WhatsApp bridge revision under
`/opt/CABAL/whatsapp/src/<commit>`, builds its static Linux AMD64 Go binary and
manifest in `/opt/CABAL/whatsapp/bin`, creates the locked Python MCP environment
at `/opt/CABAL/whatsapp/venvs/mcp-<commit>`, and prepares a TLS-verified
connection to the external Kubernetes CNPG service. The pinned Go toolchain is
installed at `/opt/CABAL/toolchains/go/<version>`. Hermes owns the MCP process
over stdio; this role never creates a WhatsApp MCP daemon or port 3000 listener.
It does not create, mutate, migrate, or read secrets from the database cluster.

ExternalDNS publishes `postgresql.h.nixknight.pk` from the generic CNPG
primary-selector LoadBalancer Service `postgresql-lan`. The Service requests VIP
`192.168.0.172` and admits the trusted LAN source range `192.168.0.0/23` as a
shared endpoint for trusted LAN clients. The bridge retains its dedicated
`whatsapp_bridge` database and role. The Ansible role requires `getent ahostsv4`
to resolve the DNS name exclusively to the approved VIP; it does not install an
`/etc/hosts` mapping. When database preparation is enabled, it idempotently
removes the legacy block marked `ANSIBLE MANAGED HERMES CABAL WHATSAPP CNPG` so
partially staged hosts converge to DNS. `/etc/hosts` is system integration only
for that cleanup, not desired role content.

The role installs an operator-approved public CA at
`/opt/CABAL/config/postgresql-ca.crt` and performs fixed-diagnostic DNS target,
TCP 5432, TLS hostname, and authenticated `SELECT 1` readiness checks. The CA
source and lowercase `sha256:` checksum are mandatory when
`hermes_cabal_whatsapp_database_prepare` is enabled. Copy installation is atomic,
root-owned, and mode 0644 so the dedicated bridge account can read the public CA
through the root-owned mode-0755 config directory without reading either mode-0600
environment file.

The bridge has its own service account and unit. It remains stopped and disabled
until database, backup, TLS, PostgreSQL 18 migration, pairing, and CONNECTED
readiness gates have all been accepted. Pairing is deliberately outside Ansible:
the role never launches a QR flow, copies device state, or captures terminal
output. Bridge startup owns schema migrations only after all gates pass.

## Section 14 binding

`WHATSAPP_BRIDGE_DB_PASSWORD` is the only database secret interface. It must be
an externally generated, URL-safe value of at least 32 characters. A nonempty
Ansible Vault/inventory value takes precedence over the same-named controller
environment fallback. Resolution and validation use `no_log`; the role never
places the value in argv and writes the resulting verify-full DSN only to
`/opt/CABAL/config/whatsapp-bridge.env`, owned by root at mode 0600. The systemd
manager reads it before dropping to the bridge service account. Probes use
`PGPASSWORD`; diagnostics contain fixed text and never print the DSN or password.
No Kubernetes Secret is read or copied.

See `ansible/docs/hermes-cabal.md` for the staged gates and operator procedure.
