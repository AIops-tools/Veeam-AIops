# veeam-aiops capabilities

25 MCP tools (17 read, 8 write), each wrapped with the bundled `@governed_tool`
harness. Typical response token estimates assume a small/medium environment.

## Overview (1 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `overview` | R | low | ~150 |

Fan-out health summary: jobs grouped by last result, repositories at/above 85%
used, and currently-running sessions. Call this first to triage an environment.

## Diagnostics / RCA (2 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `job_failure_rca` | R | low | ~200–600 |
| `repository_capacity_rca` | R | low | ~150 |

`job_failure_rca` scans recent job sessions, flags every Failed/Warning run, and
categorizes the likely cause (repository full, source/guest unreachable,
credential/VSS failure, retry exhaustion) from the failing log records — each
finding cites the session result + matched error substring, worst-first.
`repository_capacity_rca` flags repositories under the free-space thresholds
(<15% warn, <10% critical), citing the measured free% and free bytes.

## Backup Jobs (7 — 2 read, 5 write)

| Tool | R/W | Risk | Undo | Typical response tokens |
|------|:---:|:----:|------|:----------------------:|
| `job_list` | R | low | — | 150–600 (depends on job count) |
| `job_get` | R | low | — | ~120 |
| `job_start` | W | medium | `job_stop` | ~50 |
| `job_stop` | W | medium | `job_start` | ~50 |
| `job_retry` | W | medium | `job_stop` | ~50 |
| `job_enable` | W | medium | `job_disable` | ~40 |
| `job_disable` | W | medium | `job_enable` | ~40 |

REST endpoints: `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`,
`POST /api/v1/jobs/{id}/{start|stop|retry|enable|disable}`. The write tools
capture the job's prior status/lastResult for context.

## Restore (2 — 1 read, 1 write)

| Tool | R/W | Risk | Undo | Typical response tokens |
|------|:---:|:----:|------|:----------------------:|
| `restore_list_points` | R | low | — | 150–800 |
| `start_vm_restore` | W | high | **none — irreversible** | ~40 |

REST endpoints: `GET /api/v1/restorePoints` (optional `backupIdFilter`),
`GET /api/v1/restorePoints/{id}` (to name what a restore would overwrite),
`POST /api/v1/restore/vm`. `start_vm_restore` is a documented skeleton: the
exact restore endpoint and payload vary by restore type and Veeam version.

The payload carries **no target mapping**, so it is a restore-to-original — an
in-place overwrite. Two consequences worth knowing before you call it:

- `dry_run=True` resolves the opaque restore-point id to the **VM name and
  creation time** it would overwrite. `resolved: false` means it could not be
  read; the restore still proceeds, so treat that as a reason to check the
  console, not as reassurance.
- It **refuses** when that VM name matches the configured VBR host — **on the
  dry-run as well as the real call**, with identical fail-open behaviour. A
  preview that returns green for a call that will then be refused is a preview
  reporting the wrong outcome. Veeam's own
  guidance is to back up the VBR server itself, so its restore point sits in the
  same list as every other one with nothing marking it as special. **This check
  is a safety net, not a proof**: a VM display name is not a hostname, so a VBR
  server whose VM is named `Backup Server 01` is not caught, and it fails open
  when the restore point cannot be resolved.

### Dry-run semantics (line-wide)

`dry_run=True` returns `{"dryRun": true, "would...": {...}}`. A dry-run **may read** —
resolving ids and evaluating guards is exactly what lets it answer "would this be
refused?" — but it **never writes** and records **no undo**. It runs through
`@governed_tool` like any other call, so it is audited and it can be refused. The CLI
`--dry-run` routes through the same governed function, so both entry points behave
identically.

## Repositories (3 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `repository_list` | R | low | 100–400 |
| `repository_get` | R | low | ~120 |
| `repository_state` | R | low | 100–400 |

REST endpoints: `GET /api/v1/backupInfrastructure/repositories`,
`GET /api/v1/backupInfrastructure/repositories/{id}`,
`GET /api/v1/backupInfrastructure/repositories/states` (capacity / free / used,
plus a computed used%). `repository_get` merges the static record with its state
row when available.

## Backups (2 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `backup_list` | R | low | 150–800 |
| `backup_object_list` | R | low | 150–800 |

REST endpoints: `GET /api/v1/backups`, `GET /api/v1/backups/{id}/objects`.

## Infrastructure (2 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `managed_server_list` | R | low | 150–600 |
| `proxy_list` | R | low | 150–600 |

REST endpoints: `GET /api/v1/backupInfrastructure/managedServers`,
`GET /api/v1/backupInfrastructure/proxies`. Read-only inventory of where jobs
run and what moves the data.

## Sessions (4 — 3 read, 1 write)

| Tool | R/W | Risk | Undo | Typical response tokens |
|------|:---:|:----:|------|:----------------------:|
| `session_list` | R | low | — | 150–800 |
| `session_get` | R | low | — | ~120 |
| `session_log` | R | low | — | 150–800 |
| `session_stop` | W | medium | **none** | ~40 |

REST endpoints: `GET /api/v1/sessions`, `GET /api/v1/sessions/{id}`,
`GET /api/v1/sessions/{id}/logs`, `POST /api/v1/sessions/{id}/stop`. Sessions
are how Veeam exposes async job/restore progress — poll these instead of
re-issuing the originating operation; read `session_log` to see *why* one failed.

## Undo (2 — 1 read, 1 write)

| Tool | R/W | Risk | Undo | Typical response tokens |
|------|:---:|:----:|------|:----------------------:|
| `undo_list` | R | low | — | ~100–400 |
| `undo_apply` | W | medium | **none — single-use** | ~60 |

Generic governance tools provided by the bundled harness, not the Veeam REST
API. `undo_list` lists the recorded reversible writes whose undo tokens have not
yet been applied. `undo_apply` executes a recorded inverse for one token — it is
itself governed (audited, policy- and budget-checked), single-use (a token
cannot be replayed), and supports `dry_run` to preview the inverse first.

## Harness behavior

- **Encrypted credentials**: passwords are stored in `~/.veeam-aiops/secrets.enc`
  (Fernet + scrypt), unlocked by `VEEAM_AIOPS_MASTER_PASSWORD` or a prompt —
  never plaintext on disk.
- **Audit**: all 25 tools log to `~/.veeam-aiops/audit.db`.
- **Undo store**: the five reversible job writes record an inverse descriptor
  (`_undo_id` on the result); `session_stop` and the high-risk restore record none.
- **Budget/runaway guard**: caps cumulative calls + wall-time and trips tight
  session-poll loops.
- **Risk tiers**: `~/.veeam-aiops/rules.yaml` can require a recorded approver
  for high-tier writes.
- **Sanitize**: all API-returned text is truncated + control-char stripped.
