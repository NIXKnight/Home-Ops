# hermes_cabal_agent

Deploys the exact Hermes Agent 0.21.5 source and locked Python 3.13 runtime under
`/opt/CABAL/hermes/src/<commit>` and
`/opt/CABAL/hermes/venvs/<version>-<commit>`, then renders the isolated MCP-only
home at `/opt/CABAL/hermes/home`. The home receives only explicit private assets;
it does not import the prepared `.env`,
`config.yaml`, MEMORY, sessions, logs, caches, native Hindsight data, or plugin
metadata.

The managed config uses direct untrusted Hindsight MCP and a disabled-by-default
Hermes-owned WhatsApp stdio MCP child. Hindsight has only retain/recall/reflect;
WhatsApp has only get_messages/get_contact. Resources, prompts, sampling, and
elicitation are disabled for both. Native Hindsight and automatic native memory
capture are disabled.

The Ansible-owned system unit follows Hermes 0.21.5 gateway supervision exit and
shutdown semantics. `BIFROST_HERMES_VIRTUAL_KEY` and `HINDSIGHT_API_KEY` prefer
nonempty Ansible Vault/inventory values and retain same-named controller
environment fallbacks; all resolution and rendering remain `no_log`. The
root-owned mode-0600 environment is `/opt/CABAL/config/hermes.env`; systemd reads
it before starting the service as `saadali`. Shared parent directories remain
root-owned and traversable while the Hermes home and venv hierarchy are owned by
`saadali`. The service remains stopped and disabled until all explicit activation
gates pass. See `ansible/docs/hermes-cabal.md`.
