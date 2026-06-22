---
name: veeam-aiops
description: >
  Use this skill whenever the user needs to operate Veeam Backup & Replication — list/inspect backup jobs, start/stop jobs, enable/disable jobs, list restore points and start a VM restore, list backup repositories and stored backups, and poll async sessions for job/restore progress.
  Always use this skill for "list veeam jobs", "run veeam backup", "start veeam job", "veeam restore", "veeam repository", "veeam backup status", or "veeam session" when the context is explicitly Veeam / Veeam Backup & Replication / VBR.
  Do NOT use when the target is not Veeam Backup & Replication (other backup products, hypervisor lifecycle, or cloud providers are out of scope).
  Preview — common Veeam B&R operations with a built-in governance harness (audit, policy, token budget, undo, risk-tiers).
installer:
  kind: uv
  package: veeam-aiops
argument-hint: "[job id or describe your Veeam task]"
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["VEEAM_AIOPS_CONFIG"],"bins":["veeam-aiops"],"config":["~/.veeam-aiops/config.yaml","~/.veeam-aiops/.env"]},"optional":{"env":["VEEAM_TARGET_PASSWORD"]},"primaryEnv":"VEEAM_AIOPS_CONFIG","homepage":"https://github.com/AIops-tools/Veeam-AIops","emoji":"💾","os":["macos","linux"]}}
compatibility: >
  Standalone, self-governed Veeam Backup & Replication operations (preview). The governance harness (audit, policy, token/runaway budget, undo, risk-tiers) is bundled in the package — no external skill-family dependency.
  All write operations are audited to a local SQLite DB under ~/.veeam-aiops/ (relocatable via VEEAM_AIOPS_HOME).
  Credentials: Each Veeam target requires a per-target password env var in ~/.veeam-aiops/.env following the pattern VEEAM_<TARGET_NAME_UPPER>_PASSWORD. The password is exchanged for a short-lived OAuth2 bearer token at connect time and held only in memory; passwords/tokens are never logged or echoed; .env should be chmod 600.
  Destructive operations (job stop, restore start) require double confirmation at the CLI layer and support --dry-run. All write tools pass through the @governed_tool decorator (pre-check + budget guard + audit + risk-tier gate). Reversible writes (job start/stop, enable/disable) record an inverse undo descriptor; the VM restore is irreversible and records none.
  Webhooks: none — no outbound network calls beyond the configured Veeam REST API endpoint.
  SSL: verify_ssl defaults to true; disable only for self-signed lab certificates.
  Transitive dependencies: httpx (HTTP client) and the MCP SDK. No post-install scripts or background services.
---

# Veeam AIops (preview)

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by Veeam Software.** "Veeam" is a trademark of its owner. Source code is publicly auditable at [github.com/AIops-tools/Veeam-AIops](https://github.com/AIops-tools/Veeam-AIops) under the MIT license.

Governed Veeam Backup & Replication operations — **12 MCP tools**, every one wrapped with the bundled `@governed_tool` harness: a local unified audit log under `~/.veeam-aiops/`, policy engine, token/runaway budget guard, undo-token recording, and graduated-autonomy risk tiers.

> **Standalone**: the governance harness is bundled in the package (`veeam_aiops.governance`) — veeam-aiops has no external skill-family dependency. Preview: common Veeam operations, not yet exhaustive.

## What This Skill Does

| Category | Tools | Count | Read or Write |
|----------|-------|:-----:|:-------------:|
| **Backup Jobs** | list, get, start, stop, enable, disable | 6 | 2 read / 4 write |
| **Restore** | list restore points, start VM restore | 2 | 1 read / 1 write |
| **Repositories** | list repositories | 1 | 1 read |
| **Backups** | list stored backups | 1 | 1 read |
| **Sessions** | list, get (poll async progress) | 2 | 2 read |

## Quick Install

```bash
uv tool install veeam-aiops
veeam-aiops doctor
```

## When to Use This Skill

- List/inspect Veeam backup jobs and their last result
- Start or stop a backup job on demand
- Enable or disable a job's schedule
- List available restore points and start a VM restore
- List backup repositories and stored backups
- Poll async sessions to follow job/restore progress

**Do NOT use when** the target is not Veeam Backup & Replication (other backup products, hypervisor VM lifecycle, Kubernetes, or cloud providers are out of scope for this skill).

## Related Skills — Skill Routing

| If the user wants… | Use |
|--------------------|-----|
| Veeam backup jobs / restore / repositories | **veeam-aiops** (this skill) |
| Hypervisor VM lifecycle (power, snapshot, migrate) | a hypervisor ops skill |
| Container/cluster lifecycle | a cluster ops skill |

## Common Workflows

### Run a backup job and follow it to completion

1. `veeam-aiops job list` → find the job id and confirm `lastResult`
2. `veeam-aiops job start <job_id>` → starts the job (records an inverse `job_stop` undo descriptor)
3. `veeam-aiops session list` → find the running session; `veeam-aiops session get <session_id>` → check `state` / `progressPercent`
4. **Failure branch**: if `session get` shows the session `Failed`, inspect `result`, then re-run `job start` after fixing the cause — do not loop `session get` rapidly (the runaway budget guard will trip a tight poll loop).

### Restore a VM from a restore point

1. `veeam-aiops restore list-points` → identify the correct restore point id
2. `veeam-aiops restore start --restore-point-id <id> --dry-run` → preview the exact API call
3. `veeam-aiops restore start --restore-point-id <id>` → double confirmation required; this is IRREVERSIBLE (overwrites/creates a VM) and records no undo token
4. **Failure branch**: if `doctor` shows the VBR server unreachable or the password env var is missing, fix `~/.veeam-aiops/.env` (chmod 600) before retrying — the restore is never issued against an unauthenticated session.

## Usage Mode

| Scenario | Recommended | Why |
|----------|:-----------:|-----|
| Local/small models (Ollama, Qwen) | **CLI** | fewer tokens than MCP |
| Cloud models (Claude, GPT) | Either | MCP gives structured JSON I/O |
| Automated pipelines | **MCP** | type-safe parameters, audited |

## MCP Tools (12 — 8 read, 4 write)

| Category | Tools | R/W |
|----------|-------|:---:|
| Backup Jobs | `job_list`, `job_get` | Read |
| | `job_start`, `job_stop`, `job_enable`, `job_disable` | Write |
| Restore | `restore_list_points` | Read |
| | `start_vm_restore` | Write |
| Repositories | `repository_list` | Read |
| Backups | `backup_list` | Read |
| Sessions | `session_list`, `session_get` | Read |

**Harness features that light up**: write tools with a clean inverse (`job_start`↔`job_stop`, `job_enable`↔`job_disable`) pass an `undo=` lambda so the harness records an inverse descriptor (with `_undo_id`) to the undo store. The irreversible `start_vm_restore` declares no undo and is tagged `risk_level=high`. All 12 tools are audit-logged under `~/.veeam-aiops/` and pass through the policy pre-check + budget/runaway guard + graduated risk-tier gate. Veeam jobs/restores run as async sessions — poll with `session_get` instead of re-issuing (the runaway breaker backs this up).

## CLI Quick Reference

```bash
veeam-aiops job list [--target <t>]
veeam-aiops job get <job_id>
veeam-aiops job start <job_id>
veeam-aiops job stop <job_id> [--dry-run]              # double confirm
veeam-aiops job enable <job_id>
veeam-aiops job disable <job_id>
veeam-aiops restore list-points
veeam-aiops restore start --restore-point-id <id> [--dry-run]   # double confirm
veeam-aiops repository list
veeam-aiops session list
veeam-aiops session get <session_id>
veeam-aiops backup list
veeam-aiops doctor
veeam-aiops mcp                                        # start MCP server (stdio)
```

See `references/cli-reference.md` for the full command list.

## Troubleshooting

### "Config file not found"
Create `~/.veeam-aiops/config.yaml` with a `targets:` list (see README), and put passwords in `~/.veeam-aiops/.env` (chmod 600).

### "Password not found. Set environment variable: VEEAM_<NAME>_PASSWORD"
Each target needs a per-target password env var. For target `vbr-lab`, set `VEEAM_VBR_LAB_PASSWORD=<password>` in `.env`.

### "Authentication/authorization failed (401)"
The username/password is wrong, or the account lacks a Veeam role. Veeam usernames are typically `DOMAIN\\user` or a local Windows account on the VBR server. Confirm the account can log in to the Veeam console.

### "Could not reach Veeam server … check the host/port"
The default REST API port is 9419 — confirm the Veeam Backup & Replication REST API service is running and the port is open. For self-signed certificates set `verify_ssl: false` on the target (lab only).

### "Resource not found (404)"
The job/session/restore-point id is stale. List the parent collection first (`job list`, `session list`, `restore list-points`) to get a current id.

## Audit & Safety

All operations are automatically audited via the bundled `@governed_tool` decorator (`veeam_aiops.governance`):
- Every tool call logged to `~/.veeam-aiops/audit.db` (local SQLite audit DB; relocate with `VEEAM_AIOPS_HOME`)
- Policy rules enforced via `~/.veeam-aiops/rules.yaml` (deny rules, maintenance windows, risk tiers)
- Budget / runaway guard caps cumulative tool calls and wall-time, and trips on tight session-poll/retry loops
- Undo store records inverse descriptors for reversible writes (job start/stop, enable/disable)
- Graduated-autonomy risk tiers gate write operations (require a recorded approver for the highest tiers)

The harness is bundled in the package — no external dependency, no manual setup. See `references/setup-guide.md` for security details.

## License

MIT — [github.com/AIops-tools/Veeam-AIops](https://github.com/AIops-tools/Veeam-AIops)
