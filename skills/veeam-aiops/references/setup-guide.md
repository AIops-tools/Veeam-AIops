# veeam-aiops setup guide

## Install

```bash
uv tool install veeam-aiops
# or: pipx install veeam-aiops
```

## Configure

```bash
mkdir -p ~/.veeam-aiops && chmod 700 ~/.veeam-aiops
```

`~/.veeam-aiops/config.yaml` (no secrets here):

```yaml
targets:
  - name: vbr-lab
    host: 10.0.0.20
    username: "DOMAIN\\backup-admin"   # or a local account on the VBR server
    port: 9419                          # Veeam REST API port (default)
    verify_ssl: false                   # self-signed lab certs only; true in prod
```

`~/.veeam-aiops/.env` (chmod 600 — secrets only):

```bash
VEEAM_VBR_LAB_PASSWORD=<password>
```

The password variable is `VEEAM_<TARGET_NAME_UPPER>_PASSWORD` (hyphens → underscores).

```bash
chmod 600 ~/.veeam-aiops/.env
veeam-aiops doctor          # verifies connectivity + credentials
```

## Use as an MCP server

```jsonc
{
  "command": "veeam-aiops",
  "args": ["mcp"],
  "env": { "VEEAM_AIOPS_CONFIG": "~/.veeam-aiops/config.yaml" }
}
```

Using the `veeam-aiops mcp` subcommand (rather than `uvx --from`) means the MCP
client launches the already-installed entry point and does not re-resolve the
package over the network at startup.

## Security

> **Disclaimer**: Community-maintained project, **not affiliated with, endorsed
> by, or sponsored by Veeam Software**. MIT licensed. See `SECURITY.md`.

- **Credentials**: `.env` only, chmod 600, per-target `VEEAM_<TARGET>_PASSWORD`.
  The password is exchanged for a short-lived OAuth2 bearer token at connect
  time and kept only in memory.
- **Audit**: every operation logged to a local SQLite DB under
  `~/.veeam-aiops/` (relocate with `VEEAM_AIOPS_HOME`).
- **Budget guard**: cap calls/wall-time with `VEEAM_MAX_TOOL_CALLS` /
  `VEEAM_MAX_TOOL_SECONDS`; a runaway session-poll/retry loop trips automatically.
- **Risk tiers**: optional `~/.veeam-aiops/rules.yaml` `risk_tiers` require a
  recorded approver (`VEEAM_AUDIT_APPROVED_BY`) for the highest tiers.
- **Destructive ops**: `job stop` and `restore start` require double
  confirmation + support `--dry-run` at the CLI.
- **TLS**: `verify_ssl` defaults true; disable only for self-signed labs.
- **No webhooks / telemetry / background services.**

## Least privilege

Create a dedicated Veeam Backup & Replication user with only the role your
workflows need (e.g. a restricted backup-operator role) rather than a full
administrator account.
