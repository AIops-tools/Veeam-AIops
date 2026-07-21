# Security Policy

## Disclaimer

Community-maintained open-source project. **Not affiliated with, endorsed by, or
sponsored by Veeam Software.** "Veeam" is a trademark of its owner. Source is
publicly auditable under the MIT license.

## Reporting Vulnerabilities

Report privately via a GitHub Security Advisory on
[github.com/AIops-tools/Veeam-AIops](https://github.com/AIops-tools/Veeam-AIops/security/advisories)
or email zhouwei008@gmail.com. Please do not open public issues for security
reports.

## Security Design

### Credential Management
- Per-target passwords live in `~/.veeam-aiops/.env` (chmod 600), never in
  `config.yaml` and never in source. Variable pattern:
  `VEEAM_<TARGET_NAME_UPPER>_PASSWORD`.
- Passwords are exchanged for a short-lived OAuth2 bearer token at connect time;
  the token is held only in memory (side-stored by connection id, never set as
  an attribute on the HTTP client). Secrets are never logged or echoed; the
  config file holds only host, port, username, and TLS settings.

### Governed Operations
Every MCP tool runs through the bundled `@governed_tool` harness
(`veeam_aiops.governance`):
- **Audit** — every call logged to a local SQLite DB under `~/.veeam-aiops/`
  (relocatable via `VEEAM_AIOPS_HOME`), agent-attributed, secret-redacted.
- **Token/runaway budget** — hard ceilings (`VEEAM_MAX_TOOL_CALLS` /
  `VEEAM_MAX_TOOL_SECONDS`) plus an on-by-default guard that trips a tight
  poll/retry loop, preventing unbounded API consumption (e.g. polling a slow
  session).
- **Risk tier** — a descriptive label on each audit row derived from
  `risk_level`; it gates nothing. `VEEAM_AUDIT_APPROVED_BY` /
  `VEEAM_AUDIT_RATIONALE` are optional annotations recorded on the row, never
  required and never blocking.
- **Undo-token recording** — reversible writes (job start/stop, enable/disable)
  record an inverse descriptor so a change can be rolled back.

### Destructive Operations
`job stop` and `restore start` require double confirmation at the CLI layer and
support `--dry-run`. The VM restore is irreversible (overwrites/creates a VM),
tagged `risk_level=high`, and records no undo token.

### SSL/TLS Verification
`verify_ssl` defaults to true; disable only for self-signed lab certificates.

### Prompt-Injection Protection
All Veeam-API-returned text (job names, session results, descriptions) is passed
through a `sanitize()` truncate + control-character strip before reaching the
agent.

### Network Scope
No webhooks, no telemetry, no outbound calls beyond the configured Veeam B&R
REST API endpoint. No post-install scripts or background services.

## Static Analysis

```bash
uvx bandit -r veeam_aiops/ mcp_server/
uv run ruff check .
```

## Supported Versions

The latest released version receives security fixes. This is a preview (0.x);
pin a version in production.
