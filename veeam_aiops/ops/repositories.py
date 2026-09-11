"""Backup repository operations for Veeam Backup & Replication (read-only)."""

from __future__ import annotations

from typing import Any

from veeam_aiops.connection import _seg
from veeam_aiops.governance import opt_str
from veeam_aiops.ops._paging import fetch_all

_REPOS = "/api/v1/backupInfrastructure/repositories"
_REPO_STATES = "/api/v1/backupInfrastructure/repositories/states"


def list_repositories(conn: Any) -> list[dict]:
    """[READ] List backup repositories with id, name, type, and path."""
    out: list[dict] = []
    for repo in fetch_all(conn, _REPOS):
        out.append(
            {
                "id": opt_str(repo.get("id"), 64),
                "name": opt_str(repo.get("name"), 128),
                "type": opt_str(repo.get("type"), 64),
                "path": opt_str(repo.get("path"), 256),
            }
        )
    return out


def get_repository(conn: Any, repository_id: str) -> dict:
    """[READ] Return detail for a single repository (config + capacity if known).

    Merges the static repository record with its runtime state row (capacity /
    free / used) when the states endpoint exposes it.
    """
    repo = conn.get(f"{_REPOS}/{_seg(repository_id)}")
    out = {
        "id": opt_str(repo.get("id"), 64),
        "name": opt_str(repo.get("name"), 128),
        "type": opt_str(repo.get("type"), 64),
        "path": opt_str(repo.get("path"), 256),
        "description": opt_str(repo.get("description"), 200),
    }
    state = _state_for(conn, repository_id)
    if state:
        out.update(state)
    return out


def _capacity_fields(row: dict) -> dict:
    """Pull capacity/free/used (bytes) + computed used% from a state row."""
    capacity = row.get("capacityGB", row.get("capacity"))
    free = row.get("freeGB", row.get("freeSpace"))
    used = row.get("usedSpaceGB", row.get("usedSpace"))
    out: dict[str, Any] = {"capacity": capacity, "free": free, "used": used}
    try:
        if capacity and free is not None:
            out["usedPercent"] = round(100.0 * (float(capacity) - float(free)) / float(capacity), 1)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return out


def _state_for(conn: Any, repository_id: str) -> dict:
    """Best-effort lookup of one repository's state row by id."""
    try:
        rows = fetch_all(conn, _REPO_STATES)
    except Exception:  # noqa: BLE001 — advisory capacity context only
        return {}
    for row in rows:
        if str(row.get("id", "")) == str(repository_id):
            return _capacity_fields(row)
    return {}


def repository_state(conn: Any) -> list[dict]:
    """[READ] Capacity summary for every repository (capacity/free/used/used%)."""
    out: list[dict] = []
    for row in fetch_all(conn, _REPO_STATES):
        entry = {
            "id": opt_str(row.get("id"), 64),
            "name": opt_str(row.get("name"), 128),
            "type": opt_str(row.get("type"), 64),
        }
        entry.update(_capacity_fields(row))
        out.append(entry)
    return out
