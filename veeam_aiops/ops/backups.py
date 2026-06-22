"""Backup (stored backup chain) operations for Veeam B&R (read-only)."""

from __future__ import annotations

from typing import Any

from veeam_aiops.governance import sanitize


def list_backups(conn: Any) -> list[dict]:
    """[READ] List stored backups with id, name, type, and creation time."""
    data = conn.get("/api/v1/backups")
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for b in items or []:
        out.append(
            {
                "id": sanitize(str(b.get("id", "")), 64),
                "name": sanitize(str(b.get("name", "")), 128),
                "type": sanitize(str(b.get("jobType", b.get("type", ""))), 64),
                "creationTime": sanitize(str(b.get("creationTime", "")), 64),
            }
        )
    return out
