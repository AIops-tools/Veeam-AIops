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
- [ ] An unresolvable restore-point id still proceeds (fails open) and the preview
      says `resolved: false` rather than showing a blank name.
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
