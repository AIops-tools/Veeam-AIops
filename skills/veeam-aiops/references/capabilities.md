# veeam-aiops capabilities

12 MCP tools (8 read, 4 write), each wrapped with the bundled `@governed_tool`
harness. Typical response token estimates assume a small/medium environment.

## Backup Jobs (6 — 2 read, 4 write)

| Tool | R/W | Risk | Undo | Typical response tokens |
|------|:---:|:----:|------|:----------------------:|
| `job_list` | R | low | — | 150–600 (depends on job count) |
| `job_get` | R | low | — | ~120 |
| `job_start` | W | medium | `job_stop` | ~40 |
| `job_stop` | W | medium | `job_start` | ~40 |
| `job_enable` | W | medium | `job_disable` | ~40 |
| `job_disable` | W | medium | `job_enable` | ~40 |

REST endpoints: `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`,
`POST /api/v1/jobs/{id}/{start|stop|enable|disable}`.

## Restore (2 — 1 read, 1 write)

| Tool | R/W | Risk | Undo | Typical response tokens |
|------|:---:|:----:|------|:----------------------:|
| `restore_list_points` | R | low | — | 150–800 |
| `start_vm_restore` | W | high | **none — irreversible** | ~40 |

REST endpoints: `GET /api/v1/restorePoints`, `POST /api/v1/restore/vm`.
`start_vm_restore` is a documented skeleton: the exact restore endpoint and
payload vary by restore type and Veeam version.

## Repositories (1 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `repository_list` | R | low | 100–400 |

REST endpoint: `GET /api/v1/backupInfrastructure/repositories`.

## Backups (1 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `backup_list` | R | low | 150–800 |

REST endpoint: `GET /api/v1/backups`.

## Sessions (2 — read)

| Tool | R/W | Risk | Typical response tokens |
|------|:---:|:----:|:----------------------:|
| `session_list` | R | low | 150–800 |
| `session_get` | R | low | ~120 |

REST endpoints: `GET /api/v1/sessions`, `GET /api/v1/sessions/{id}`. Sessions
are how Veeam exposes async job/restore progress — poll these instead of
re-issuing the originating operation.

## Harness behavior

- **Audit**: all 12 tools log to `~/.veeam-aiops/audit.db`.
- **Undo store**: the four reversible job writes record an inverse descriptor
  (`_undo_id` on the result); the high-risk restore records none.
- **Budget/runaway guard**: caps cumulative calls + wall-time and trips tight
  session-poll loops.
- **Risk tiers**: `~/.veeam-aiops/rules.yaml` can require a recorded approver
  for high-tier writes.
- **Sanitize**: all API-returned text is truncated + control-char stripped.
