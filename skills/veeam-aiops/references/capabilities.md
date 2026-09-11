# veeam-aiops capabilities

27 MCP tools (19 read, 8 write), each wrapped with the bundled `@governed_tool`
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

`job_failure_rca` scans the newest `limit` sessions (default 100, newest first by
`creationTime`; `sessionsTruncated` says older ones exist), flags every Failed/Warning run, and
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

`restore_list_points` returns the newest `limit` points (default 100, max 1000) as
`{"restorePoints", "returned", "limit", "truncated", "order"}` — an estate's restore
points are far too many to return whole. With `backup_id`, a point from another
backup coming back means the server ignored the filter, and that is refused.

REST endpoints: `GET /api/v1/restorePoints` (`orderColumn=CreationTime&orderAsc=false`,
optional `backupIdFilter`),
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

## Backups (4 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `backup_list` | R | low | 150–800 |
| `backup_object_list` | R | low | 150–800 |
| `backup_object_storage_usage` | R | low | 400–1500 |
| `backup_storage_ranking` | R | low | 300–2000 |

REST endpoints: `GET /api/v1/backups`, `GET /api/v1/backups/{id}/objects`.

### Backup storage footprint (`backup_object_storage_usage`, `backup_storage_ranking`)

Sums Veeam's own per-file accounting from `GET /api/v1/backups/{id}/backupFiles`:
`backupSize` (on disk after compression and deduplication) and `dataSize`
(before). Other reads: `GET /api/v1/serverInfo` (build → REST revision),
`GET /api/v1/backupObjects?nameFilter=`, `GET /api/v1/restorePoints?backupObjectIdFilter=`,
`GET /api/v1/backups/{id}`, `GET /api/v1/jobs/{id}` (retention). Every collection
is paged to completion — the server caps a page at 200 by default.

- **Needs VBR 12.3+.** `backupFiles` first appears in REST revision 1.2-rev0
  (VBR 12.3.0.310, per Veeam's published revision table). The size reads send
  the newest revision the server's build serves; the rest of the tool keeps its
  pinned 1.1-rev1. Older builds get a refusal that names the minimum build.
- **Works with a read-only Backup Viewer account.** `/api/v1/serverInfo` (the
  build) is Backup Administrator only from revision 1.1-rev2 on; without it the
  tools offer each revision newest-first on a one-item `GET /api/v1/backups` and
  use the first the server accepts (`apiRevision.basis: "probe"`; refused
  revisions are listed in `apiRevision.probeRefusals`).
- **Shared files are never charged to one machine.** Per-job backup chains keep
  several VMs in one file; revision 1.3-rev2 (VBR 13.1+) lists every owner, and
  such files land in `sharedStoredBytes`, outside `storedBytes`. Older revisions
  name one owner per file, so sharing is decided by the restore points the file
  itself lists (`restorePointIds`): any point that is not this machine's makes
  it shared, whatever owner it names. A file whose ownership the server states
  inconsistently (no owner and no point list, or a single other owner with only
  this machine's points) goes to `unattributedStoredBytes` — never charged, and
  visible if owner ids ever turn out not to match backup-object ids. The ranking
  reads no restore points and charges each file to its listed owner.
- **One unreadable backup does not blank the result**: it is listed in
  `unreadableBackups` (with the error) and left out of every total.
- **The restore-point filter is checked, not trusted.** A query for a random
  object id must come back empty (`restorePointFilter: "honoured"`); then points
  under a machine's old name are kept (`restorePointNamesSeen`). If the server
  ignores the filter — or the check itself fails — points are matched by name,
  the others are counted in `restorePointsSetAside`, and a caveat says so; if
  two same-named machines then get the same points, totals are withheld and each
  machine is marked `attributable: false`.
- **Full vs incremental** comes from each restore point's `type` and the
  `backupFileId` it lives in; the `.vbk`/`.vib`/`.vrb` extension is a fallback,
  and `kindBasis` counts which rule classified each file.
- **Missing is not zero**: files without a size are counted in `unsizedFiles`
  and left out of the sums. `approxSourceBytes` is null before revision 1.3-rev2.
- **Block cloning**: on ReFS / XFS fast-clone repositories synthetic fulls share
  blocks, so summed file sizes can exceed physical consumption — an upper bound.
- **Same name, different machines** (two vCenters) are kept apart by identity
  (`path`, then `objectId` / BIOS UUID) and a caveat is added; the ranking merges
  one machine across backups by the same identity. Where the revision exposes no
  inventory id (Hyper-V and agents before 1.3-rev2) identity falls back to name,
  with a caveat.
- **Paging checks the server**: `skip` advances by what was actually returned,
  items are de-duplicated by id, and a server that stops short of its own total
  or repeats a page is refused rather than billed partially or twice.
- `changeRate` is mean incremental `dataSize` ÷ latest full `dataSize`, per
  increment (not per day).
- No pricing: storage cost models are organisation-specific.

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
`session_list` returns the newest `limit` sessions (default 100, max 1000) as
`{"sessions", "returned", "limit", "truncated", "order"}`, sorted by the server
(`orderColumn=CreationTime&orderAsc=false`), so "recent" is explicit.

### List reads and paging

The VBR REST API pages its collections and, from revision 1.3, returns 200 items
per page unless asked otherwise. Inventory reads (`backup_list`,
`backup_object_list`, `job_list`, `repository_list`, `repository_state`,
`managed_server_list`, `proxy_list`) page to the end; `overview` and
`repository_capacity_rca` therefore see every repository. The pager advances by
what the server actually returned, de-duplicates by id, and raises instead of
returning a partial list if the server stops short or repeats a page.
`backup_object_list` sends no paging parameters on its first request (the pinned
revision 1.1-rev1 declares none for that endpoint) and pages only if the server's
pagination block shows more.

## Undo (2 — 1 read, 1 write)

| Tool | R/W | Risk | Undo | Typical response tokens |
|------|:---:|:----:|------|:----------------------:|
| `undo_list` | R | low | — | ~100–400 |
| `undo_apply` | W | medium | **none — single-use** | ~60 |

Generic governance tools provided by the bundled harness, not the Veeam REST
API. `undo_list` lists the recorded reversible writes whose undo tokens have not
yet been applied. `undo_apply` executes a recorded inverse for one token — it is
itself governed (audited and budget-checked), single-use (a token
cannot be replayed), and supports `dry_run` to preview the inverse first.

## Harness behavior

- **Encrypted credentials**: passwords are stored in `~/.veeam-aiops/secrets.enc`
  (Fernet + scrypt), unlocked by `VEEAM_AIOPS_MASTER_PASSWORD` or a prompt —
  never plaintext on disk.
- **Audit**: all 27 tools log to `~/.veeam-aiops/audit.db`.
- **Undo store**: the five reversible job writes record an inverse descriptor
  (`_undo_id` on the result); `session_stop` and the high-risk restore record none.
- **Budget/runaway guard**: caps cumulative calls + wall-time and trips tight
  session-poll loops.
- **Risk tier**: a descriptive label on each audit row derived from `risk_level`;
  it gates nothing. `VEEAM_AUDIT_APPROVED_BY` / `VEEAM_AUDIT_RATIONALE` are
  optional annotations recorded on the audit row, never required.
- **Sanitize**: all API-returned text is truncated + control-char stripped.
