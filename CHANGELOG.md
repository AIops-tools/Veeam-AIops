# Changelog

## v0.3.0 — 2026-07-13

Security-hardening release from a line-wide code review.

### Changed (behavior)
- **Secure by default**: with no `rules.yaml`, high/critical operations now require a
  named approver (`VEEAM_AUDIT_APPROVED_BY`). A fresh install no longer allows
  destructive writes unattended; `init` seeds a starter `rules.yaml` you can edit,
  and an operator-authored rules file is honoured as-is.
- `__version__` is now single-sourced from package metadata (the previous release
  self-reported a stale version string).
- Sanitize docs no longer overstate scope: it strips control/format characters and
  truncates; semantic prompt-injection resistance must come from the consuming agent.

### Fixed
- Agent-supplied ids are percent-encoded in REST URL paths (path-traversal hardening, 12 sites).
- All write tools accept `dry_run=True` previews.
- Functional test suite now exercises all 21 MCP tools against a recording fake (endpoint paths/params asserted).

### Tests
- Governance persistence is now tested against REAL `audit.db`/`undo.db` files
  (write → audit row + inverse undo row with captured prior state).
- The CLI confirmed-write path (dry-run / double-confirm / governed execution) is
  covered end-to-end.
- `pytest-cov` added to the dev dependencies.

## v0.2.1

- Fix: `VEEAM_AIOPS_HOME` now also relocates `config.yaml` (was hardcoded to `~/.veeam-aiops`).
- Fix: **CLI writes are now audited + undo-recorded** via the governance path — previously only the MCP tools recorded audit/undo; CLI `manage`/`remediate`/etc. writes now go through the same `@governed_tool` layer (they keep their dry-run + double-confirm). CLI write output is now the governed JSON result. No API/tool changes.


All notable changes to **veeam-aiops** are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-06-27

Encrypted credentials, a friendly onboarding wizard, and MCP tools expanded from
**12 → 21**.

### Added
- **Encrypted credential store** — the VBR login password now lives in
  `~/.veeam-aiops/secrets.enc` (Fernet/AES + HMAC, scrypt-derived master password).
  No plaintext on disk; `chmod 600`.
- **Onboarding wizard** — `veeam-aiops init` collects host/username/port/verify_ssl
  and the password (stored encrypted), then offers a connectivity check.
- **Secret management** — `veeam-aiops secret set/list/rm/migrate/rotate-password`
  (`migrate` imports a legacy `.env`).
- **Health summary** — `overview` (jobs by last status, repos near full, running
  sessions).
- **Jobs** — `job_get`, `job_retry`, `job_enable`, `job_disable`; `job_start/stop`
  now capture prior state for context.
- **Restore** — `backup_object_list`; `restore_list_points` gained an optional
  `backup_id` filter.
- **Repositories** — `repository_get`, `repository_state` (capacity/free/used + %).
- **Sessions** — `session_log`, `session_stop`.
- **Infrastructure** — `managed_server_list`, `proxy_list`.

### Changed
- `config.py` resolves the password from the encrypted store first, then a legacy
  `VEEAM_<TARGET>_PASSWORD` env var (with a deprecation warning).
- `doctor` reports encrypted-store presence/permissions and nudges to `init`.
- Dropped the "SKELETON / preview" label from the CLI help; dropped the now-unused
  `python-dotenv` dependency, added `cryptography`.

### Security
- Master password via `VEEAM_AIOPS_MASTER_PASSWORD` for non-interactive/MCP use.
  No tool returns credentials; destructive ops keep dry-run + double-confirm.

### Notes
- Still preview/mock-validated — `repository_state`/`session_log`/`managed_servers`/
  `proxies` use the documented `/api/v1` endpoints, but exact field names
  (capacity GB vs bytes) and `--backup-id` filter support vary by Veeam version.

## [0.1.0] — 2026-06-22

Initial preview release: jobs, restore, repositories, sessions, backups
(12 MCP tools), with the vendored governance harness.

[0.2.0]: https://github.com/AIops-tools/Veeam-AIops/releases/tag/v0.2.0
[0.1.0]: https://github.com/AIops-tools/Veeam-AIops/releases/tag/v0.1.0
