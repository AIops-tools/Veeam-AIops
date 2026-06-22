"""Session operations for Veeam Backup & Replication (read-only).

Veeam jobs and restores execute as asynchronous *sessions*. After starting a
job or restore, poll its session to follow progress — the Veeam analog of a
task-status primitive. This lets an agent check a long-running operation once
instead of looping (which, with the veeam-aiops runaway breaker, is the
structural answer to the "poll a slow op, burn tokens" failure mode).
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.governance import sanitize


def _session_summary(s: dict) -> dict:
    return {
        "id": sanitize(str(s.get("id", "")), 64),
        "name": sanitize(str(s.get("name", "")), 128),
        "type": sanitize(str(s.get("sessionType", s.get("type", ""))), 64),
        "state": sanitize(str(s.get("state", "")), 32),
        "result": sanitize(str((s.get("result") or {}).get("result", s.get("result", ""))), 32),
    }


def list_sessions(conn: Any) -> list[dict]:
    """[READ] List recent sessions with id, name, type, state, result."""
    data = conn.get("/api/v1/sessions")
    items = data.get("data", data) if isinstance(data, dict) else data
    return [_session_summary(s) for s in (items or [])]


def get_session(conn: Any, session_id: str) -> dict:
    """[READ] Poll one session by id to check job/restore progress.

    Use after start_job or start_vm_restore to follow the operation instead of
    re-issuing it.
    """
    s = conn.get(f"/api/v1/sessions/{session_id}")
    summary = _session_summary(s)
    summary["progressPercent"] = s.get("progressPercent")
    summary["creationTime"] = sanitize(str(s.get("creationTime", "")), 64)
    return summary
