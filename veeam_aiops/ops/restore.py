"""Restore operations for Veeam Backup & Replication.

Restore points are read-only; starting a VM restore is a high-risk write with
NO undo (it overwrites or creates a VM). The ``start_vm_restore`` body is a
documented skeleton: the exact restore endpoint and payload vary by restore
type (instant recovery, full VM restore, restore to new location), so this
keeps a minimal, clearly-marked call that callers should adapt to their
Veeam version and restore intent.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.governance import sanitize


def list_restore_points(conn: Any) -> list[dict]:
    """[READ] List available restore points (id, name, creation time, type)."""
    data = conn.get("/api/v1/restorePoints")
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for rp in items or []:
        out.append(
            {
                "id": sanitize(str(rp.get("id", "")), 64),
                "name": sanitize(str(rp.get("name", "")), 128),
                "creationTime": sanitize(str(rp.get("creationTime", "")), 64),
                "type": sanitize(str(rp.get("platformName", rp.get("type", ""))), 64),
            }
        )
    return out


def start_vm_restore(conn: Any, restore_point_id: str) -> dict:
    """[WRITE] Start a VM restore from a restore point. IRREVERSIBLE — no undo.

    SKELETON: this issues a minimal full-VM-restore start against the restore
    point. Production callers should select the correct restore endpoint
    (instant recovery vs full restore vs restore-to-new-location) and supply
    the matching payload (target host/datastore/network mapping) for their
    Veeam version. Overwriting or creating a VM cannot be undone here.
    """
    body = {"restorePointId": restore_point_id}
    conn.post("/api/v1/restore/vm", json=body)
    return {
        "restore_point_id": sanitize(restore_point_id, 64),
        "action": "vm_restore_started",
    }
