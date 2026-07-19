"""Backup (stored backup chain) operations for Veeam B&R (read-only)."""

from __future__ import annotations

from typing import Any

from veeam_aiops.connection import _seg
from veeam_aiops.governance import opt_str


def list_backups(conn: Any) -> list[dict]:
    """[READ] List stored backups with id, name, type, and creation time."""
    data = conn.get("/api/v1/backups")
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for b in items or []:
        out.append(
            {
                "id": opt_str(b.get("id"), 64),
                "name": opt_str(b.get("name"), 128),
                "type": opt_str(b.get("jobType", b.get("type")), 64),
                "creationTime": opt_str(b.get("creationTime"), 64),
            }
        )
    return out


def list_backup_objects(conn: Any, backup_id: str) -> list[dict]:
    """[READ] List the protected objects (VMs/agents) inside a stored backup.

    Use to see which machines a backup actually protects before restoring.
    """
    data = conn.get(f"/api/v1/backups/{_seg(backup_id)}/objects")
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for o in items or []:
        out.append(
            {
                "id": opt_str(o.get("id"), 64),
                "name": opt_str(o.get("name"), 128),
                "type": opt_str(o.get("type", o.get("platformName")), 64),
                "objectId": opt_str(o.get("objectId"), 128),
            }
        )
    return out
