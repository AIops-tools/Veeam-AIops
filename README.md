<!-- mcp-name: io.github.AIops-tools/veeam-aiops -->

# Veeam AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Veeam Software.** "Veeam" is a trademark of its owner. MIT licensed.

AI-powered Veeam Backup & Replication operations with a **built-in governance
harness** — unified audit log, policy engine, token/runaway budget guard,
undo-token recording, and descriptive risk tiers. Self-contained: no
external dependencies beyond `httpx` and the MCP SDK. Coverage is not yet full
coverage of every Veeam operation.

> **Verification status**: the test suite is mock-based; this package has not yet been
> validated against a live Veeam B&R server. See [docs/VERIFICATION.md](docs/VERIFICATION.md).

## What works

- **CLI** (`veeam-aiops ...`): `init`, `overview`, `job list/get/start/stop/retry/enable/disable`, `restore list-points/start`, `repository list/get/state`, `session list/get/log/stop`, `backup list/objects`, `diagnose job-failures/repo-capacity`, `infra servers/proxies`, `secret set/list/rm/migrate/rotate-password`, `doctor`, `mcp`.
- **MCP server** (`veeam-aiops mcp` or `veeam-aiops-mcp`): **25 tools** (17 read, 8 write), every one wrapped with the bundled `@governed_tool` harness.
- **Diagnostics / RCA** (read-only): `diagnose job-failures` triages recent job sessions — flags every Failed/Warning run and categorizes the likely cause (repository full, source/guest unreachable, credential/VSS failure, retry exhaustion), citing the session result + matched error substring; `diagnose repo-capacity` flags repositories under the free-space thresholds (<15% warn, <10% critical). Both cite the measured number that tripped each finding, worst-first.
- **Encrypted credentials**: passwords live in an encrypted store `~/.veeam-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `VEEAM_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: write ops with a clean inverse (job start/stop/retry, enable/disable) record an inverse undo descriptor; the irreversible VM restore declares none and is tagged `high` risk.
- **Async sessions**: Veeam jobs and restores run as sessions — poll progress with `session list` / `session get` / `session log` (the runaway budget guard prevents poll loops from running away).

## What this tool does, and does not, decide

It delivers Veeam Backup & Replication operations — reads and writes —
accurately and efficiently, and records every one of them. It does **not**
decide whether a write is allowed to happen. That is the agent's judgement, or
the permission of the Veeam account you connect it with: give that account a
read-only or restricted role on the VBR server and the writes fail at the
server — the place that actually owns the permission.

So there is no read-only switch, no policy file, no approval gate to configure.
The one thing the tool guarantees is that nothing is silent: **every call, over
MCP and over the CLI alike, lands an audit row** in `~/.veeam-aiops/audit.db`,
and reversible writes still capture their before-state and record an inverse.

> Each tool declares a `risk_level`, kept in agreement with its `[READ]`/`[WRITE]`
> documentation tag by a test, and carried into the audit row as a descriptive
> tier — so a reviewer can see at a glance that a row was a high-risk restore. It
> is a label, not a gate.

Running a smaller / local model? See
[agent-guardrails.md](skills/veeam-aiops/references/agent-guardrails.md) — it lists
the guardrails this tool now enforces for you (so you don't spend prompt budget
restating them) and gives a ready-made system prompt for what's left.

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
(relocatable via `VEEAM_AIOPS_HOME`) — the harness **records**, it does not
authorize. Every call, over MCP and the CLI alike, lands an audit row; the
token/runaway budget guard is a safety backstop (not an authorization gate) that
stops a stuck agent from burning unbounded calls, and each row carries a
descriptive risk tier that gates nothing. Destructive CLI commands (`job stop`,
`restore start`) require double confirmation and support `--dry-run`.
API-returned text is run through a prompt-injection sanitizer.

## Contributing & feature requests

Coverage is intentionally focused. **Missing a device, action, or feature you need?** Open an issue or pull request at [github.com/AIops-tools/Veeam-AIops](https://github.com/AIops-tools/Veeam-AIops/issues) — feature requests, contributions, and comments are all welcome.

License: MIT.
