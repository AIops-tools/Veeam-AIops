"""Backup job operations for Veeam Backup & Replication.

Bodies are thin wrappers over the VBR REST API (``/api/v1/jobs``). All
API-returned text is run through ``opt_str()`` before reaching the caller
(output hygiene; an absent field stays ``None`` rather than collapsing to
``""``, so a caller can tell "the API had no value" from "the value was
empty"). Returns are high-signal summaries, not full blobs.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.connection import _seg
from veeam_aiops.governance import opt_str, sanitize


def _job_summary(job: dict) -> dict:
    """Reduce a Veeam job record to a high-signal summary."""
    return {
        "id": opt_str(job.get("id"), 64),
        "name": opt_str(job.get("name"), 128),
        "type": opt_str(job.get("type"), 64),
        "status": opt_str(job.get("status"), 32),
        "lastResult": opt_str(job.get("lastResult"), 32),
        "isDisabled": job.get("isDisabled"),
    }


def _job_state(conn: Any, job_id: str) -> dict:
    """Best-effort runtime state of a single job (status/lastResult), or {}.

    Capturing the pre-action state gives an agent context for a start/stop/retry
    and a record of what was true before the change. Failures are swallowed —
    this is advisory context, never the operation itself.
    """
    try:
        job = conn.get(f"/api/v1/jobs/{_seg(job_id)}")
    except Exception:  # noqa: BLE001 — advisory only
        return {}
    return {
        "status": opt_str(job.get("status"), 32),
        "lastResult": opt_str(job.get("lastResult"), 32),
    }


def list_jobs(conn: Any) -> list[dict]:
    """[READ] List backup jobs with id, name, type, status, lastResult."""
    data = conn.get("/api/v1/jobs")
    items = data.get("data", data) if isinstance(data, dict) else data
    return [_job_summary(j) for j in (items or [])]


def get_job(conn: Any, job_id: str) -> dict:
    """[READ] Return detail for a single backup job by id (incl. schedule)."""
    job = conn.get(f"/api/v1/jobs/{_seg(job_id)}")
    summary = _job_summary(job)
    summary["description"] = opt_str(job.get("description"), 200)
    schedule = job.get("schedule") or {}
    if isinstance(schedule, dict):
        summary["scheduleEnabled"] = schedule.get("runAutomatically")
    return summary


def start_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Start a backup job. Runs as an async session. Inverse: stop_job."""
    prior = _job_state(conn, job_id)
    conn.post(f"/api/v1/jobs/{_seg(job_id)}/start")
    return {"job_id": sanitize(job_id, 64), "action": "start", "priorState": prior}


def stop_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Stop a running backup job. Inverse: start_job."""
    prior = _job_state(conn, job_id)
    conn.post(f"/api/v1/jobs/{_seg(job_id)}/stop")
    return {"job_id": sanitize(job_id, 64), "action": "stop", "priorState": prior}


def retry_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Retry a failed backup job (re-runs failed objects only).

    Runs as an async session. Inverse: stop_job (cancels the in-flight retry).
    """
    prior = _job_state(conn, job_id)
    conn.post(f"/api/v1/jobs/{_seg(job_id)}/retry")
    return {"job_id": sanitize(job_id, 64), "action": "retry", "priorState": prior}


def enable_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Enable a backup job (clears the disabled flag). Inverse: disable_job."""
    conn.post(f"/api/v1/jobs/{_seg(job_id)}/enable")
    return {"job_id": sanitize(job_id, 64), "action": "enable"}


def disable_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Disable a backup job (skips scheduled runs). Inverse: enable_job."""
    conn.post(f"/api/v1/jobs/{_seg(job_id)}/disable")
    return {"job_id": sanitize(job_id, 64), "action": "disable"}
