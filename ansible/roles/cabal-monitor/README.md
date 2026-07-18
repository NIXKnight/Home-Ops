# cabal-monitor

Deploys the headless CABAL Claude Code monitor harness on ZEROKAGE (Debian 13,
user `saadali`). It is a systemd `--user` + config-templating role on one host,
**not** a docker-compose service.

## What it does

1. **Isolated config dir** (`CLAUDE_CONFIG_DIR=/home/saadali/.claude-cabal`)
   - Merges the CABAL overlay into `settings.json` (preserves `statusLine`,
     `theme`, `effortLevel`; removes `skipDangerousModePermissionPrompt`).
   - Adds the `env` block that points Claude Code at the self-hosted
     `qwen3.6-27b` endpoint. **Every** model alias
     (`ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`)
     maps to the one served model, because the sealed `web-recon`/`docs-recon`
     workers carry `model: sonnet` and an unmapped alias fails against a
     single-model server.
   - Tight `permissions.allow`, a `PreToolUse` send-guard hook on
     `send_auto_message` / `send_group_message`, and `enabledMcpjsonServers`.
   - Symlinks **only** the two sealed workers (`agents/`) and the persona
     command set (`commands/`) back to the repo, plus a rendered `.mcp.json`.
     The default `~/.claude` fleet is never pulled in.
2. **systemd `--user` units** - `cabal-monitor.service` (oneshot, one
   `claude -p "/monitor"` pass wrapped in `flock -n`) fired by
   `cabal-monitor.timer`. Fresh per-cycle sessions bound prompt context, the
   core fix for the in-session monitor's unbounded-context failure.
3. Enables **linger** for `saadali` and pulls the persona repo current
   (`git pull --ff-only`, fails loudly on a non-fast-forward).

## Run

```bash
# Group path (add `zerokage` to a cabal_harness group in your inventory first):
ansible-playbook playbooks/cabal.yml -l zerokage

# No-inventory path (edit nothing):
ansible-playbook playbooks/cabal.yml -i 'zerokage,' -e cabal_target=zerokage -e ansible_user=saadali
```

## Manual steps for the Master (this role does not do these)

- **Verify the `claude` binary path.** Default is
  `/home/saadali/.local/bin/claude`. Check with `command -v claude` on ZEROKAGE
  and override `CABAL_CLAUDE_BIN` if it differs.
- **Stop the legacy in-session monitor cron** (the one created via `CronCreate`
  that fired `/monitor`). This role does not touch it; if left running it will
  race the new timer.
- **`saadali` needs sudo** - enabling linger runs with `become`.
- **`ansible.cfg` / inventory** - this role adds no inventory entries and edits
  no config. If you prefer a real group, add `zerokage` under `cabal_harness`
  in your inventory yourself; the shared `hosts.yml` should not be touched while
  it carries uncommitted work.
- **The send-guard hook** (`/home/saadali/Projects/CABAL/.claude/hooks/send-guard.py`)
  is shipped by the CABAL persona repo. Ensure it is present (committed/pushed)
  before the timer runs, or the `PreToolUse` hook command will fail.

## Not managed

The WhatsApp bridge stack (`whatsapp-bridge`, `whatsapp-mcp-server` on
`http://127.0.0.1:3000/mcp`, bridge API `:8080`, whisper/embedder/transcriber/
dashboard) is already deployed and is left completely untouched.
