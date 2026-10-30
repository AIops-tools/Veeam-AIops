# Live verification status

This document records what has and has not been validated against a real Veeam
Backup & Replication server, so the maturity claim is auditable.

## Current status: partial live validation

The automated test suite remains mock-based. An independent operator ran the
**CLI → REST API** path against a production Veeam Backup & Replication
**13.1.1.18** server on **2026-09-12–13**, using `veeam-aiops` **0.13.1** and
then **0.15.2**. The observations below come from
[issue #2](https://github.com/AIops-tools/Veeam-AIops/issues/2); they do not
validate every tool, the MCP + LLM path, writes, restore safety, or later
releases. In particular, fixes added in 0.16.0 and after have not been
retested in that environment in the issue thread.

### What the live run established

- Authentication, `doctor`, repository list/state/capacity diagnosis, proxy and
  server inventory, and one VM's `backup usage` completed. That VM reported
  7 restore points, 38,416.9 GiB stored, 0 shared and 0 unattributed; its
  derived average incremental change rate was about 9.39%. The reporter did
  **not** establish that every byte value matched the VBR console.
- `/backupFiles` pagination reached at least `skip=1800` with HTTP 200. With a
  per-target timeout of 300 seconds, a full ranking scan completed for all
  **123 backups visible to that API account**, reporting 4,246 objects and
  0 unreadable backups. It took **21m 10s**. A 100-backup scan was explicitly
  partial and changed the ranking materially.
- That scan reported 3,214 ownerless files and **one 5 MiB unmatched-owner
  file**. The ownerless bytes were kept separate from per-VM charges. The
  unmatched owner was investigated but its cause and the 0.16.0 lookup-based
  resolution have **not** been confirmed on the live server.
- `/jobs?skip=0&limit=1` and `/sessions?skip=0&limit=1` each timed out after
  120 seconds in a direct authenticated HTTPX test. `job list`, `session list`,
  `diagnose job-failures`, and the corresponding parts of `overview` therefore
  did **not** pass in that environment. Other endpoints on the same server
  responded, so this observation does not establish a general connection
  failure.

Separately, [issue #3](https://github.com/AIops-tools/Veeam-AIops/issues/3)
reported an expired OAuth token in a persistent MCP server. The renewal fix
released in 0.15.1 was checked against an HTTP stub, but the thread contains
no live VBR confirmation of the fixed long-running MCP path.

## What the mock suite guarantees

Every module imports; the CLI builds; every MCP tool carries the
`@governed_tool` harness marker; write tools record the correct inverse undo
descriptor against a mocked REST client; the RCA heuristics
(`job_failure_rca`, `repository_capacity_rca`) are unit-tested against synthetic
session and repository telemetry.

On its own, the mock suite does **not** prove that the VBR REST API's field
names, enum values (session results, job states), or pagination behave as
modelled. The live observations above cover only the named paths and build.

## Prerequisites for a live run

A reachable Veeam Backup & Replication server with the REST API enabled, and a
**non-production** backup job you may run and inspect. Create a least-privilege
API account. Never verify restore operations against production data.

```bash
uv tool install veeam-aiops
veeam-aiops init      # encrypted secret store, TLS verify on by default
```

## Checklist

### 1. Connectivity
- [x] `veeam-aiops doctor` → authenticates against the live REST endpoint
      (VBR 13.1.1.18, package 0.13.1; issue #2).

### 2. Reads return real, well-shaped data
- [x] Repository list/state and `diagnose repo-capacity` complete; proxies
      and servers return inventory (VBR 13.1.1.18, package 0.13.1; issue #2).
- [x] `backup usage` returns one VM's storage breakdown and `/backupFiles`
      pagination reaches at least `skip=1800` (same run; issue #2). This does
      not establish console-level byte reconciliation.
- [x] `backup ranking` scans 123/123 API-visible backups with 0 unreadable
      backups after raising the per-target timeout to 300 seconds (package
      0.15.2; issue #2). One unmatched-owner file remains unresolved.
- [ ] Job / session / repository listings match what the VBR console shows.
- [ ] `veeam-aiops diagnose job-failures` → against a job you deliberately
      failed, confirm the session is flagged and the cause category matches the
      real failure reason (repository full / source unreachable / credential or
      VSS failure / retry exhaustion).
- [ ] `veeam-aiops diagnose repo-capacity` → the reported free% matches the
      console's repository free space.
- [ ] **Paging** (needs a collection over 200 items — restore points get there
      fastest): `restore list-points --limit 300` returns 300 with `truncated`
      true, newest first, matching the console's order; `job list` /
      `repository list` on an estate with more than 200 of either returns all of
      them. Record what the server does under the pinned `x-api-version:
      1.1-rev1` with no `limit` — that default is undocumented.
- [ ] `backup objects <id>` on a job with more than 200 VMs returns all of them
      (its first request carries no `skip`/`limit`).
- [ ] `session list` is newest first and `diagnose job-failures` reports
      `sessionsTruncated` when older sessions exist.
- [ ] `session log <id>` on a failed session shows its records with
      `description`, and `diagnose job-failures` classifies that failure's cause
      (not "not auto-classified") with `logsUnreadable` empty. Before 0.13.1 the
      log reader crashed on the spec's `records` shape.

### 2b. Backup storage footprint (added 0.12.0; partially validated live)
Needs VBR 12.3+. Ground truth is the VBR console: *Backups → Disk → (job) →
Properties* lists every file with its size.
- [ ] `veeam-aiops backup usage <vm>` → `apiRevision.revision` matches the
      server build (12.3.x → 1.2-rev*, 13.0.x → 1.3-rev0/1, 13.1+ → 1.3-rev2).
- [ ] Per backup, `storedBytes` equals the sum of that VM's file sizes in the
      console, and the full / incremental split matches the .vbk / .vib files.
- [ ] `restorePoints` equals the console's restore-point count for the VM,
      including a VM with **more than 200** points (exercises paging).
- [ ] A backup-copy job appears as its own entry with the copy repository.
- [ ] **Per-job chain** (job with "per-machine backup files" off): the shared
      file lands in `sharedStoredBytes`, not `storedBytes`, for **every** VM in
      it. On 12.3 / 13.0 record what `objectId` and `restorePointIds` the server
      puts on a shared file — sharing detection there rests on `restorePointIds`.
- [ ] `unattributedFiles` is 0 on a per-machine chain. A non-zero value with
      `storedBytes` 0 means file owner ids and backup-object ids differ on this
      build — the one assumption the spec does not settle.
- [ ] `restorePointFilter` is `honoured` (a random object id returns no points).
- [ ] With a **Backup Viewer** account: `doctor` connects and notes the build is
      not readable; `backup usage` reports `apiRevision.basis: "probe"` with the
      same revision an administrator account gets from the build.
- [ ] A VM renamed after some backups keeps all its restore points and lists the
      old name in `restorePointNamesSeen`.
- [ ] `veeam-aiops backup ranking` → the top rows match the largest VMs you
      expect, and **`unmatchedOwnerFiles` is 0** (a non-zero value means file
      owner ids and backup object ids may not agree for some files on this build).
      `ownerlessFiles` being non-zero is NOT that signal — those are per-job
      chain files that name no owner, and a live VBR 13.1 environment reported
      a large ownerless total with per-object attribution intact. This criterion
      previously read `unresolvedFiles`, which merged the two and would have
      failed a healthy server.
- [ ] `backup ranking --backup <job>` and `--repository <name>` read only the
      selected backups (compare `backupsInScope` with the console) and print
      SCOPED; the per-object numbers match the full ranking's rows for them.
- [ ] `--concurrency 4` produces the same ranking as the default (1); record
      both wall-clock times and whether any backup newly times out on a large
      estate (issue #2: 21 min sequential for 123 at a 300 s timeout).
- [ ] Every `unmatchedOwners` entry: look the id up in the console. `otherBackup`
      should be a machine that changed jobs; `notFound` one removed from
      inventory. Issue #2 reported exactly one such file (5 MiB) on VBR 13.1.1.18.
- [ ] On a VBR 12.1 / 12.2 server the command refuses and names build 12.3.0.310.

### 3. A reversible write + its undo
- [ ] Run a governed write that has a recorded inverse; confirm the result
      carries an `_undo_id` and an audit row lands in the audit DB.
- [ ] `veeam-aiops undo apply <id>` → the inverse executes as recorded.

### 4. Restore safety (the highest-risk path)
- [ ] `restore start ... --dry-run` → prints the exact API call plus the **VM name
      and creation time** behind the restore-point id, and changes nothing.
- [ ] A restore point whose VM name equals the VBR host is **refused** before any
      POST; a restore point for any other VM still runs. Confirm both — the guard
      is worthless if it over-blocks and dangerous if it under-blocks.
- [ ] `--dry-run` on that same restore point is refused too, and `--dry-run` on
      any other one still prints its preview.
- [ ] Every CLI write exits non-zero on a refusal or an aborted confirmation (`echo $?`).
- [ ] Every `--dry-run` leaves an audit row and changes nothing on the VBR server.
- [ ] An unresolvable restore-point id is REFUSED with a message naming
      `acknowledge_unresolved`, and no POST reaches the server; with the
      acknowledgement it proceeds and reports `resolved: false`.
- [ ] A real restore into a **free** target records an undo; a forced overwrite
      correctly declares none and is tagged `high` risk.

### 5. Governance records (it does not gate)
- [ ] A `high`-risk op runs with no approver set and still lands an audit row
      whose `risk_tier` is the descriptive label `review` — it gates nothing.
- [ ] `VEEAM_AUDIT_APPROVED_BY` / `VEEAM_AUDIT_RATIONALE`, when set, appear on the
      audit row as annotations and never change whether the call runs.

### 6. Cleanup
- [ ] Remove any test restore point / job created during verification.

## Criteria to claim live verification

Claim only the specific live coverage recorded above. A broader live-verification
claim requires the remaining boxes to be checked against a recorded VBR
version, any field-shape mismatch fixed and covered by a test, and the results
written up with the date and package version. Until then the overall status
remains **partial live validation**.

## Governance hardening — local synthetic verification (2026-10-30)

The shared governance tests exercise a durable `pending` row before a write, refusal when the audit DB cannot persist that row, a visible pending row after finalization failure, explicit reconciliation to `unknown`, async/preview parity, result minimization, and metadata-only telemetry with `AIOPS_WORKFLOW_ID`. The tests use a temporary SQLite DB and synthetic tools; they do not claim a new live upstream write was performed. Existing audit rows and rotated archives from earlier releases are not automatically scrubbed. A pending intent from another process is never auto-reconciled. On POSIX the rotation/intent lock spans processes; on Windows it currently protects only threads in this process.
