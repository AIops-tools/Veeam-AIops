<!-- mcp-name: io.github.AIops-tools/veeam-aiops -->

# Veeam AIops (preview)

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Veeam Software.** "Veeam" is a trademark of its owner. MIT licensed.

AI-powered Veeam Backup & Replication operations with a **built-in governance
harness** — unified audit log, policy engine, token/runaway budget guard,
undo-token recording, and graduated-autonomy risk tiers. Self-contained: no
external dependencies beyond `httpx` and the MCP SDK. Preview — not yet full
coverage of every Veeam operation.

## What works

- **CLI** (`veeam-aiops ...`): `init`, `overview`, `job list/get/start/stop/retry/enable/disable`, `restore list-points/start`, `repository list/get/state`, `session list/get/log/stop`, `backup list/objects`, `infra servers/proxies`, `secret set/list/rm/migrate/rotate-password`, `doctor`, `mcp`.
- **MCP server** (`veeam-aiops mcp` or `veeam-aiops-mcp`): **21 tools** (16 read, 5 write), every one wrapped with the bundled `@governed_tool` harness.
- **Encrypted credentials**: passwords live in an encrypted store `~/.veeam-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `VEEAM_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: write ops with a clean inverse (job start/stop/retry, enable/disable) record an inverse undo descriptor; the irreversible VM restore declares none and is tagged `high` risk.
- **Async sessions**: Veeam jobs and restores run as sessions — poll progress with `session list` / `session get` / `session log` (the runaway budget guard prevents poll loops from running away).

## Quick start

```bash
uv tool install veeam-aiops
veeam-aiops init        # interactive wizard: connection details + encrypted password
veeam-aiops doctor      # verify config, encrypted store, connectivity
```

`init` writes `~/.veeam-aiops/config.yaml` (non-secret connection details) and
stores the login password **encrypted** in `~/.veeam-aiops/secrets.enc`. Example
config it produces:

```yaml
targets:
  - name: vbr-lab
    host: 10.0.0.20
    username: "DOMAIN\\backup-admin"
    port: 9419
    verify_ssl: false          # self-signed lab certs only
```

For non-interactive use (MCP server, CI, cron) export the master password so the
store can be unlocked without a prompt:

```bash
export VEEAM_AIOPS_MASTER_PASSWORD='your-master-password'
```

### Managing secrets

```bash
veeam-aiops secret set vbr-lab            # prompts hidden for the password
veeam-aiops secret list                   # names only, values never shown
veeam-aiops secret rm vbr-lab
veeam-aiops secret rotate-password        # re-encrypt under a new master password
veeam-aiops secret migrate                # import a legacy plaintext .env, then deletes it
```

Migrating from an old `~/.veeam-aiops/.env` (legacy `VEEAM_<TARGET>_PASSWORD`
vars)? Run `veeam-aiops secret migrate`; the old `.env` is renamed to
`.env.migrated`. The plaintext env var is still honoured as a fallback (with a
deprecation warning) for a smooth transition.

## Audit & safety

All operations are logged to a local SQLite audit DB under `~/.veeam-aiops/`
(relocatable via `VEEAM_AIOPS_HOME`). Every write tool passes through the
governance harness: policy pre-check, token/runaway budget guard, graduated
risk-tier gate, and audit logging. Destructive CLI commands (`job stop`,
`restore start`) require double confirmation and support `--dry-run`.
API-returned text is run through a prompt-injection sanitizer.

## Contributing & feature requests

This is a preview — coverage is intentionally focused. **Missing a device, action, or feature you need?** Open an issue or pull request at [github.com/AIops-tools/Veeam-AIops](https://github.com/AIops-tools/Veeam-AIops/issues) — feature requests, contributions, and comments are all welcome.

License: MIT.
