"""Backup repository operations for Veeam Backup & Replication (read-only)."""

from __future__ import annotations

from typing import Any

from veeam_aiops.governance import sanitize


def list_repositories(conn: Any) -> list[dict]:
    """[READ] List backup repositories with id, name, type, and path."""
    data = conn.get("/api/v1/backupInfrastructure/repositories")
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for repo in items or []:
        out.append(
            {
                "id": sanitize(str(repo.get("id", "")), 64),
                "name": sanitize(str(repo.get("name", "")), 128),
                "type": sanitize(str(repo.get("type", "")), 64),
                "path": sanitize(str(repo.get("path", "")), 256),
            }
        )
    return out
