<!-- mcp-name: io.github.AIops-tools/veeam-aiops -->

# Veeam AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Veeam Software.** "Veeam" is a trademark of its owner. MIT licensed.

AI-powered Veeam Backup & Replication operations with a **built-in governance
harness** — unified audit log, policy engine, token/runaway budget guard,
undo-token recording, and graduated-autonomy risk tiers. Self-contained: no
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

## Security: read-only mode

This tool is meant to be handed to an AI agent, so its safety story is enforced
by the server rather than requested in a prompt:

```bash
export VEEAM_READ_ONLY=1
```

With that set, the **8 write tools are never registered**. An MCP client
lists **17 tools instead of 25** — the writes are not hidden, not
gated behind a flag, and not merely refused when called. They are absent from
the session. A model cannot invoke a tool it was never offered, and cannot be
argued into one.

That distinction is the whole point. A tool that exists but refuses still invites
retry loops and "I'll describe the call instead" behaviour from smaller models,
and it leaves a reviewer trusting a promise. An absent tool is a fact you can
check: connect, list the tools, and see that the writes are not there.

Enforcement is two layers deep, so the switch cannot be sidestepped by changing
entry point:

| Layer | What it does | Covers |
|---|---|---|
| `@governed_tool` harness | refuses every non-read operation outright | MCP, CLI, and in-process callers |
| MCP registration | write tools are removed from `list_tools()` | anything speaking MCP |

Read operations are unaffected, and every call is still audited to
`~/.veeam-aiops/audit.db`.

> The read/write split is derived from each tool's declared `risk_level`, and a
> test asserts that this never disagrees with the `[READ]`/`[WRITE]` tag in the
> tool's own documentation — so a write can't quietly present itself as a read.

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
(relocatable via `VEEAM_AIOPS_HOME`). Every write tool passes through the
governance harness: policy pre-check, token/runaway budget guard, graduated
risk-tier gate, and audit logging. Destructive CLI commands (`job stop`,
`restore start`) require double confirmation and support `--dry-run`.
API-returned text is run through a prompt-injection sanitizer.

## Contributing & feature requests

Coverage is intentionally focused. **Missing a device, action, or feature you need?** Open an issue or pull request at [github.com/AIops-tools/Veeam-AIops](https://github.com/AIops-tools/Veeam-AIops/issues) — feature requests, contributions, and comments are all welcome.

License: MIT.
