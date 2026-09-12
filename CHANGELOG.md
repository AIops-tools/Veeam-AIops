# Changelog

## v0.15.2 — 2026-09-12

### Changed
- **An irreversible VM restore is now refused when the restore point cannot be
  read** (BREAKING for that one path). `start_vm_restore` sends no target
  mapping, so it is a restore-to-original with no undo; when resolution failed
  the tool proceeded anyway, unable to name the machine it was about to
  overwrite — a preview reporting `vmName: null` is the absence of the one fact
  the decision needs, not consent to it. The self-lockout guard still treats an
  unknown name as "not the VBR server" rather than guessing; this is a separate
  judgement about whether an unnamed target may be overwritten at all.
  `acknowledge_unresolved=True` (CLI `--acknowledge-unresolved`) proceeds for
  when the target has been confirmed in the Veeam console, so the capability is
  preserved for the disaster it exists for. Refusing errs recoverably; the
  previous behaviour did not.

## v0.15.1 — 2026-09-12

### Fixed
- **A long-lived MCP server kept an expired bearer token and never
  reauthenticated** (#3, reported against a persistent Streamable HTTP
  deployment). `ConnectionManager` caches a connection for the life of the
  process while `_login()` only ran at construction, so once VBR expired the
  token every tool returned 401 until the service was restarted. The CLI was
  immune because each invocation logs in afresh — which is why no CLI testing
  could have found it. A 401 is now answered by renewing the token and retrying
  the request exactly once (a 401 is refused before the handler runs, so a
  write cannot be applied twice), and when the server reports `expires_in` the
  token is renewed before it lapses.

### Added
- **The OpenClaw install path is documented.** The ClawHub bundle channel went
  live but neither the README nor this skill said how to install from it:
  `openclaw plugins install clawhub:@aiops-tools/veeam-aiops`. States the `uvx`
  prerequisite (without it the skill installs but reports `Visible to model:
  no`) and that the MCP server is pinned to this exact release.
- **Where an exported master password lives** is now stated next to the
  instruction to export it: readable by every process the shell starts, and
  kept in shell history.

## v0.15.0 — 2026-09-12
### Added
- **Per-target `timeout` (seconds) in `config.yaml`.** The 30 s request budget
  was hardcoded with no override. On a large estate `/jobs` and `/sessions` can
  exceed any client budget while every other endpoint answers in seconds, and
  there was no supported way to give them longer.
- **The storage ranking now separates two kinds of uncharged file** that were
  reported as one `unresolved` number: `ownerlessStoredBytes` (files naming no
  owner — ordinary per-job chain files) and `unmatchedOwnerStoredBytes` (files
  naming an owner id their own backup does not list, which is the signal that
  owner ids and backup-object ids are different namespaces on that build). Only
  the second raises a caveat. `unresolved*` stays as the sum, so existing
  consumers keep working.
- **`backup ranking` prints an explicit PARTIAL line** when it scanned fewer
  than all backups, naming the flag to raise. The counts were already in the
  payload, but a partial ranking rendered exactly like a complete one — and the
  default `--max-backups` is 100, which is below some estates' backup count.

### Fixed
- **A request timeout was reported as "Transport error … Check connectivity".**
  A timeout is a subclass of `httpx.HTTPError` and was falling into the generic
  branch, so a server that accepted the connection and then went quiet was
  diagnosed as a network fault — on an estate where every other endpoint
  answered in seconds. It now says it timed out, names the budget spent, and
  names the config key that raises it.
- **`docs/VERIFICATION.md` gave a criterion that would fail a healthy server**:
  it asked for `unresolvedFiles == 0`, which merged ownerless chain files into
  the namespace signal. The check is now `unmatchedOwnerFiles == 0`.

## v0.14.0 — 2026-09-12
### Added
- **Installable from ClawHub as an OpenClaw bundle plugin** (`@aiops-tools/veeam-aiops`): one install delivers the skill *and* its MCP
  server, pinned to this exact release. `clawhub.ai/plugins`.

### Fixed
- **The skill was invisible to the model in OpenClaw.** Its metadata
  declared `requires.config` (OpenClaw reads that as config *keys*, not file
  paths, so it can never be satisfied), `requires.env` and `requires.bins`
  naming our own CLI — which a plugin user never has on PATH — plus a
  `primaryEnv` that turned a config path into an API-key prompt. Measured on
  OpenClaw 2026.6.35: `Visible to model: no`. It now requires
  `anyBins: [veeam-aiops, uvx]` — either one suffices — with every variable kept
  in `optional.env` (still declared, no longer a load gate), which the same
  command reports as `Visible to model: yes`.

## v0.13.1 — 2026-09-11

### Fixed
- **`session_log` crashed on every server that answers the way Veeam's spec
  says it does, and `job_failure_rca` could never classify a cause.** The spec
  (every revision 1.1-rev0 to 1.3-rev2) returns a session log as
  `{"totalRecords", "records": [...]}`; the reader looked for `data`, so it
  iterated the dict's keys and raised `AttributeError`. `session_log` failed
  outright (CLI traceback, MCP "operation failed"). `job_failure_rca` swallowed
  the same exception, treated the log as empty, and reported every failure as
  "root cause not auto-classified". Present since v0.1.0; every test used a
  `{"data": [...]}` shape the server never returns. Found by running the
  released package over real HTTP against a stub built from the spec.
- Log records now carry `description` (where the error detail is) and
  `updateTime` (the spec has no `endTime`), and the RCA classifies on
  `title: description`.
- `job_failure_rca` lists sessions whose log it could not read in
  `logsUnreadable` instead of silently reporting them as unclassified; the
  MCP tool and CLI share one collector.

## v0.13.0 — 2026-09-11

### Changed (BREAKING)
- **`restore_list_points` and `session_list` return an envelope instead of a
  bare list**: `{"restorePoints"|"sessions": [...], "returned", "limit",
  "truncated", "order"}`, newest first (`orderColumn=CreationTime`,
  `orderAsc=false`), with a new `limit` (default 100, max 1000). `truncated` is
  measured by asking for one more item. The CLI `restore list-points` and
  `session list` gained `--limit` and say when older items exist.
- `job_failure_rca` / `diagnose job-failures` take `limit` and an optional
  `since_hours` (sent as `createdAfterFilter`), and report `sessionsTruncated`
  and `sessionsSince`. For "what failed last night", `since_hours=24` makes the
  window a time span instead of the newest N sessions of every type.
- `overview`'s session block reports its window (`limit`, `truncated`,
  `order`), and `running` entries now carry their `state`. `running` is **not**
  taken from that window: it is queried per unfinished state (`stateFilter`),
  so a job started days ago and still running is found; a state the server
  refuses is listed in `stateQueryErrors`, not read as "none".

### Fixed
- **List reads no longer stop at the server's first page.** Every list read took
  one response. Veeam's spec gives `limit` a default of 200 from REST revision
  1.3-rev0; what the server does under this tool's pinned 1.1-rev1 is
  undocumented. Where the server does cap, a job, repository, proxy or backup
  past the 200th could be silently missing — including from `overview` and
  `repository_capacity_rca`, where a repository past the first page would never
  be capacity-checked. Inventory reads now page explicitly to the end and
  refuse rather than return a partial list; histories use the envelope above.
- `restore_list_points(backup_id=...)` refuses when the server returns points of
  another backup (the filter was ignored) instead of presenting them as the
  requested backup's.
- The agent guardrails said every other read "returns everything VBR returned";
  that was not true until this release, and now says what each read does.

## v0.12.0 — 2026-09-11

### Added
- **Backup storage footprint per VM** (`backup_object_storage_usage`, CLI
  `backup usage <name>`) — requested in #2 for showback / chargeback. For one
  protected object it reports, per backup (primary job and each backup copy
  separately): repository, restore-point count and date range, stored bytes
  split into full / incremental, source bytes, GFS files, the job's retention
  settings and an increment-vs-full change rate. The numbers are sums of
  Veeam's own per-file accounting (`GET /api/v1/backups/{id}/backupFiles`:
  `backupSize` after compression and dedup, `dataSize` before). No pricing —
  cost models are organisation-specific.
- **Storage ranking** (`backup_storage_ranking`, CLI `backup ranking`) —
  protected objects ordered by backup storage consumed, largest first, with an
  explicit rank and a measured `truncated` flag. Answers "which VMs are the most
  expensive to protect".
- **Needs VBR 12.3 or later.** `backupFiles` first appears in REST revision
  1.2-rev0, which shipped in VBR 12.3.0.310 (Veeam's published revision table).
  The size reads pick the newest revision the server's build serves; every other
  call keeps the pinned 1.1-rev1. An older server gets a refusal naming the
  minimum build instead of an opaque 404.
- Built against Veeam's published OpenAPI specification (revisions 1.2-rev0 to
  1.3-rev2). **Not yet run against a live VBR server** — see
  `docs/VERIFICATION.md` §2b.

### Fixed
- **`doctor` no longer reports a working connection as failed for non-admin
  accounts.** It proved connectivity with `GET /api/v1/serverInfo`, which is
  Backup Administrator only from REST revision 1.1-rev2 (VBR 12.2) on, so a
  least-privilege Backup Viewer account — the recommended setup for read-only
  use — logged in fine and was then told "Connect failed". Connectivity is now
  proven with `serverTime` (open to every role); the build is shown when the
  account may read it, and a 403 there is a note, not a failure.

### Design notes (why the numbers can be billed against)
- A file that stores several machines (per-job backup chain) is **never charged
  to one of them**: revision 1.3-rev2 lists every owner, and such files are
  reported as `sharedStoredBytes`, outside the machine's total. Older revisions
  report a single owner per file and cannot express sharing; the payload says
  which field the server used and adds a caveat.
- Full vs incremental comes from each restore point's own `type`, not the file
  extension (which is only a fallback, and `kindBasis` says which was used).
- A file with no reported size is counted as unsized, not as zero.
- Under the single-owner shape, sharing is decided by the restore points each
  file lists, not by the one owner it names; a file whose ownership the server
  states inconsistently is reported as unattributed rather than charged.
- One unreadable backup is listed (`unreadableBackups`) instead of aborting the
  report.
- The restore-point filter is verified with a negative control (a random object
  id must return nothing) instead of trusted; a renamed VM keeps the points
  taken under its old name.
- A read-only Backup Viewer account works: when the build is not readable the
  REST revision is probed on a read every role may make.
- Every collection is paged to completion. From revision 1.3 the server returns
  200 items per page unless asked otherwise, so a single read under-reports a
  VM with more than 200 restore points. Paging advances by what the server
  actually returned and de-duplicates by id; a server that stops short or
  repeats a page is refused rather than billed partially or twice.
- Same-named VMs on different vCenters stay separate (identity = inventory path,
  then moref / BIOS UUID).
- On block-clone repositories (ReFS / XFS fast clone) summed file sizes can
  exceed physical consumption; the payload says to treat the total as an upper
  bound there.

### Also in this release
- **Installable as a Claude Code plugin.** `.claude-plugin/plugin.json` plus a
  root `.mcp.json` make this repo a plugin, so `/plugin install veeam-aiops@aiops-tools`
  delivers the skill and registers the MCP server in one step. The server is
  pinned to the exact package version the manifest declares, so an audit row
  stays traceable to the code that produced it. Nothing about the tool itself
  changed — the CLI and the standalone MCP server work exactly as before.

## v0.11.0 — 2026-08-10

### Fixed
- **An undetermined outcome no longer exits as a plain failure.** A write whose response was lost carries *both* `error` and `outcomeUnknown`, and the harness deliberately judges unknown first when writing the audit row — the change may have taken effect, so a blind retry could apply it twice. The CLI guard judged `error` first, so the audit said "may have taken effect" while the exit status told a script it had not happened. The two layers now agree (exit 2, not 1), and a test pins the ordering so it cannot silently flip back.

## v0.10.0 — 2026-08-03

### Fixed
- **`undo apply` replays against the target the original write ran on.** It dispatched the inverse against whatever target the *caller* named — in practice the config's first entry — while the write's own target sat unused in the undo record. On a multi-target config the inverse therefore ran against the wrong host; it only looks harmless because the resource usually is not there, but two hosts holding the same name and the inverse **succeeds on the wrong one, silently**. An explicitly named target still wins. Line-wide: all 24 copies had the identical defect. Caught live in container-host-aiops, where a stop recorded against a Podman target replayed against a Portainer one.

## v0.9.0 — 2026-08-02

### Changed (BREAKING)
- **Requires MCP SDK 2.0** (`mcp[cli]>=2.0,<3.0`). `mcp.server.fastmcp` no longer exists in 2.0; the server is now built with `MCPServer` and reports its package version in the stdio handshake.

### Fixed
- **`undo apply` works from the CLI.** Every write tool is imported lazily inside its own CLI command, so a CLI-driven undo ran in a process where the inverse tool was never registered and failed with "inverse tool is not registered" — for every write tool. Only the MCP entry point, which imports the whole server, worked. Found while live-verifying against a real cluster.
- **An undetermined outcome is audited `unknown`, not `ok`.** The harness only classified a result as undetermined when the payload *also* carried an `error` key, so a write that looked successful but had not been confirmed was recorded as a success.


## v0.8.0 — 2026-07-21

### Changed
- CLI `--dry-run` previews for the remaining write commands now route through the governed twin (run the guards, land an audit row) instead of a static unaudited banner.

See RELEASE_NOTES.md for detail.


## v0.7.0 — 2026-07-21

### Changed (BREAKING)
- **Removed the authorization layer** — read-only mode, the approver gate, and rules.yaml deny are gone. The skill no longer decides read vs write; that is the agent's judgement or the connecting account's permissions. `<PREFIX>_READ_ONLY` now has no effect (a startup warning is logged); `<PREFIX>_AUDIT_APPROVED_BY`/`_RATIONALE` are optional audit annotations.
- The retained guarantee is **unbypassable audit over MCP and CLI alike** — no unaudited entry point. Harness = audit + runaway safety guard + undo + sanitize; `risk_level` is a descriptive audit label, not a gate.

See RELEASE_NOTES.md for tool-specific changes.


## v0.6.0 — 2026-07-20

### Fixed
- **`start_vm_restore` refuses a restore point whose VM is the VBR server itself.** The payload carries no target mapping, so a restore is an in-place overwrite — and Veeam's own best practice is to back up the VBR server, which puts such a restore point in the very list this tool returns, unmarked.
- The restore dry-run now resolves the restore point to a VM name and creation time.
- **CLI writes now exit non-zero on a governed error.** A refused restore, a policy denial or an unreachable VBR previously printed the error and still exited 0, so a CI job read it as success..
- Harness: a write whose response is lost is audited `status=unknown`, not `error` — it may have taken effect. Undo tokens gain `effectVerified` (undo.db migrated in place).
- Harness: a dry-run no longer records an undo token, and no longer requires a named approver. Guards now run on the preview path.
- Truncated strings end in an ellipsis instead of being cut silently; error messages are capped at 800 chars, not 300.

See RELEASE_NOTES.md for the full detail.

## v0.4.0 — 2026-07-17

### Added
- **Undo executor**: `undo list` / `undo apply <id>` (CLI + MCP) — apply a recorded replayable inverse; the dispatched inverse is re-gated by its own risk tier; single-use, dry-run, double-confirm, both wrapper + inverse audited.

## v0.3.1 — 2026-07-16

### Fixed
- **`secrets.enc` now follows `VEEAM_AIOPS_HOME`** (secretstore hardcoded the real
  home directory; config/audit/undo already relocated — found in live verification).
- **Audit fidelity**: failures sanitized into `{"error": ...}` results by the MCP error
  layer are now audited as `status=error` (they previously read as `ok`, hiding failed
  attempts from exception reports), and no undo is recorded for a call that failed.

### Tests
- `doctor` and the `init` wizard are now fully covered (previously ~10–20%); plus a
  regression test for the sanitized-failure audit status.

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
