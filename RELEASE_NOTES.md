# Release Notes

## 0.1.0 (preview)

Initial preview release of **veeam-aiops** — governed Veeam Backup & Replication
operations for AI agents.

- **21 MCP tools** (16 read, 5 write), every one wrapped with the bundled
  `@governed_tool` governance harness (audit, policy, token/runaway budget,
  undo-token recording, graduated risk tiers).
- **Encrypted credential store**: passwords are stored in
  `~/.veeam-aiops/secrets.enc` (Fernet/AES-128 + scrypt-derived key, chmod 600)
  — never plaintext on disk. Unlock with `VEEAM_AIOPS_MASTER_PASSWORD` or an
  interactive prompt. New `veeam-aiops init` onboarding wizard and a
  `veeam-aiops secret set/list/rm/migrate/rotate-password` sub-app. The legacy
  plaintext `VEEAM_<TARGET>_PASSWORD` env var remains a fallback (with a
  deprecation warning); `secret migrate` imports an old `.env`.
- **Overview**: a one-shot `overview` health summary (jobs by last result,
  repos near full, running sessions).
- **Backup jobs**: list, get (incl. schedule), start, stop, retry, enable,
  disable.
- **Restore**: list restore points (optionally per backup), start a VM restore
  (high-risk skeleton).
- **Repositories**: list, get (detail), state (capacity/free/used + used%).
- **Backups**: list stored backups, list backup objects.
- **Infrastructure**: managed servers and proxies inventory.
- **Sessions**: list, get, log (events), stop, for polling/cancelling async
  job/restore progress.
- **CLI** (`veeam-aiops ...`) with `--dry-run` and double-confirm on destructive
  ops (`job stop`, `session stop`, `restore start`), plus `init`, `overview`,
  `doctor`, and an `mcp` stdio subcommand.
- Veeam VBR REST API connection layer (OAuth2 password grant → bearer token,
  `x-api-version: 1.1-rev1`, central HTTP-error translation into
  `VeeamApiError` teaching messages).
- Self-contained: bundled governance harness, no external skill-family
  dependency. Dependencies: `httpx`, `typer`, `rich`, `pyyaml`, `cryptography`,
  `mcp[cli]`.
