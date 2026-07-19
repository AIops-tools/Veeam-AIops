"""Environment health overview for Veeam Backup & Replication (read-only).

A single high-signal summary an agent can call first: jobs grouped by last
result, repositories near full, and how many sessions are running. Built by
fanning out over the other read ops; each sub-query is best-effort so one
failing collection never blanks the whole picture.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.ops import jobs as jobs_ops
from veeam_aiops.ops import repositories as repo_ops
from veeam_aiops.ops import sessions as session_ops

# Repositories at or above this used-% are flagged as "near full".
_NEAR_FULL_PERCENT = 85.0
_RUNNING_STATES = {"working", "running", "starting", "stopping"}


def _job_health(conn: Any) -> dict:
    try:
        rows = jobs_ops.list_jobs(conn)
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": str(exc)[:200]}
    by_result: dict[str, int] = {}
    disabled = 0
    for r in rows:
        key = (r.get("lastResult") or "Unknown") or "Unknown"
        by_result[key] = by_result.get(key, 0) + 1
        if r.get("isDisabled"):
            disabled += 1
    return {"total": len(rows), "byLastResult": by_result, "disabled": disabled}


def _repo_health(conn: Any) -> dict:
    try:
        rows = repo_ops.repository_state(conn)
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": str(exc)[:200]}
    near_full = [
        {"name": r.get("name"), "usedPercent": r.get("usedPercent")}
        for r in rows
        if isinstance(r.get("usedPercent"), (int, float))
        and r["usedPercent"] >= _NEAR_FULL_PERCENT
    ]
    return {"total": len(rows), "nearFull": near_full}


def _session_health(conn: Any) -> dict:
    try:
        rows = session_ops.list_sessions(conn)
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": str(exc)[:200]}
    running = [
        {"id": r.get("id"), "name": r.get("name")}
        for r in rows
        if str(r.get("state") or "").lower() in _RUNNING_STATES
    ]
    return {"recent": len(rows), "running": running}


def health_overview(conn: Any) -> dict:
    """[READ] One-shot health summary: jobs by last result, repos near full,
    running sessions."""
    return {
        "jobs": _job_health(conn),
        "repositories": _repo_health(conn),
        "sessions": _session_health(conn),
        "nearFullThresholdPercent": _NEAR_FULL_PERCENT,
    }
