# Release Notes

## 0.1.0 (preview)

Initial preview release of **veeam-aiops** — governed Veeam Backup & Replication
operations for AI agents.

- **12 MCP tools** (8 read, 4 write), every one wrapped with the bundled
  `@governed_tool` governance harness (audit, policy, token/runaway budget,
  undo-token recording, graduated risk tiers).
- **Backup jobs**: list, get, start, stop, enable, disable.
- **Restore**: list restore points, start a VM restore (high-risk skeleton).
- **Repositories** and **stored backups**: list.
- **Sessions**: list and get, for polling async job/restore progress.
- **CLI** (`veeam-aiops ...`) with `--dry-run` and double-confirm on destructive
  ops, plus `doctor` and an `mcp` stdio subcommand.
- Veeam VBR REST API connection layer (OAuth2 password grant → bearer token,
  `x-api-version: 1.1-rev1`, central HTTP-error translation into
  `VeeamApiError` teaching messages).
- Self-contained: bundled governance harness, no external skill-family
  dependency. Dependencies: `httpx`, `typer`, `rich`, `pyyaml`,
  `python-dotenv`, `mcp[cli]`.
