# Live verification status

This document records what has and has not been validated against a real Veeam
Backup & Replication server, so the maturity claim is auditable.

## Current status ⚠️ mock-only

`veeam-aiops` has **not** been validated against a live VBR server. The test
suite is mock-based throughout. This is the largest verification gap in the
tool, because a VBR server is the hardest dependency to stand up for a
community self-test (no free/containerised equivalent).

## What the mock suite guarantees

Every module imports; the CLI builds; every MCP tool carries the
`@governed_tool` harness marker; write tools record the correct inverse undo
descriptor against a mocked REST client; the RCA heuristics
(`job_failure_rca`, `repository_capacity_rca`) are unit-tested against synthetic
session and repository telemetry.

It does **not** prove that the VBR REST API's field names, enum values
(session results, job states), or pagination behave as modelled.

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
- [ ] `veeam-aiops doctor` → authenticates against the live REST endpoint.

### 2. Reads return real, well-shaped data
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

### 2b. Backup storage footprint (added 0.12.0 — built from Veeam's OpenAPI spec, never run live)
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
      owner ids and backup object ids are not the same namespace on this build).
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

Every box ticked against a recorded VBR version, any field-shape mismatch fixed
and covered by a test, and the result written up with the date and version.
Until then this document must continue to say mock-only.
