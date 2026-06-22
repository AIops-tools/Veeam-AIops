# veeam-aiops CLI reference

Global options on most commands: `--target / -t <name>` selects a configured
target (default: first target in `config.yaml`).

## Backup jobs

```bash
veeam-aiops job list                       # id, name, type, status, lastResult
veeam-aiops job get <job_id>               # detail for one job
veeam-aiops job start <job_id>             # start a backup job (async session)
veeam-aiops job stop <job_id> [--dry-run]  # stop a running job — double confirm
veeam-aiops job enable <job_id>            # enable the job schedule
veeam-aiops job disable <job_id>           # disable the job schedule
```

## Restore

```bash
veeam-aiops restore list-points            # available restore points
veeam-aiops restore start --restore-point-id <id> [--dry-run]
                                           # IRREVERSIBLE — double confirm
```

## Repositories

```bash
veeam-aiops repository list                # id, name, type, path
```

## Sessions (async progress)

```bash
veeam-aiops session list                   # recent sessions: state, result
veeam-aiops session get <session_id>       # poll one session (progressPercent)
```

## Backups

```bash
veeam-aiops backup list                    # stored backups: id, name, type, time
```

## Diagnostics & MCP

```bash
veeam-aiops doctor [--skip-auth]           # config + secrets + connectivity check
veeam-aiops mcp                            # start the MCP server (stdio transport)
```

## Notes

- `job start` and `restore start` kick off **async sessions**. Follow progress
  with `session list` / `session get`, not by re-issuing the command.
- Destructive commands (`job stop`, `restore start`) require two confirmations
  and accept `--dry-run` to preview the exact API call without executing.
