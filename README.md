<!-- mcp-name: io.github.AIops-tools/veeam-aiops -->

# Veeam AIops (preview)

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Veeam Software.** "Veeam" is a trademark of its owner. MIT licensed.

AI-powered Veeam Backup & Replication operations with a **built-in governance
harness** — unified audit log, policy engine, token/runaway budget guard,
undo-token recording, and graduated-autonomy risk tiers. Self-contained: no
external dependencies beyond `httpx` and the MCP SDK. Preview — not yet full
coverage of every Veeam operation.

## What works

- **CLI** (`veeam-aiops ...`): `job list/get/start/stop/enable/disable`, `restore list-points/start`, `repository list`, `session list/get`, `backup list`, `doctor`, `mcp`.
- **MCP server** (`veeam-aiops mcp` or `veeam-aiops-mcp`): **12 tools** (8 read, 4 write), every one wrapped with the bundled `@governed_tool` harness.
- **Reversibility**: write ops with a clean inverse (job start/stop, enable/disable) record an inverse undo descriptor; the irreversible VM restore declares none and is tagged `high` risk.
- **Async sessions**: Veeam jobs and restores run as sessions — poll progress with `session list` / `session get` (the runaway budget guard prevents poll loops from running away).

## Quick start

```bash
uv tool install veeam-aiops
mkdir -p ~/.veeam-aiops
# create ~/.veeam-aiops/config.yaml with a targets: list
# put passwords in ~/.veeam-aiops/.env  (chmod 600)
veeam-aiops doctor
```

Example `~/.veeam-aiops/config.yaml`:

```yaml
targets:
  - name: vbr-lab
    host: 10.0.0.20
    username: "DOMAIN\\backup-admin"
    port: 9419
    verify_ssl: false          # self-signed lab certs only
```

`~/.veeam-aiops/.env` (chmod 600): `VEEAM_VBR_LAB_PASSWORD=<password>`

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
