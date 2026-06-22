"""Backup job operations for Veeam Backup & Replication.

Bodies are thin wrappers over the VBR REST API (``/api/v1/jobs``). All
API-returned text is run through ``sanitize()`` before reaching the caller
(prompt-injection defense). Returns are high-signal summaries, not full blobs.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.governance import sanitize


def _job_summary(job: dict) -> dict:
    """Reduce a Veeam job record to a high-signal summary."""
    return {
        "id": sanitize(str(job.get("id", "")), 64),
        "name": sanitize(str(job.get("name", "")), 128),
        "type": sanitize(str(job.get("type", "")), 64),
        "status": sanitize(str(job.get("status", "")), 32),
        "lastResult": sanitize(str(job.get("lastResult", "")), 32),
        "isDisabled": job.get("isDisabled"),
    }


def list_jobs(conn: Any) -> list[dict]:
    """[READ] List backup jobs with id, name, type, status, lastResult."""
    data = conn.get("/api/v1/jobs")
    items = data.get("data", data) if isinstance(data, dict) else data
    return [_job_summary(j) for j in (items or [])]


def get_job(conn: Any, job_id: str) -> dict:
    """[READ] Return detail for a single backup job by id."""
    job = conn.get(f"/api/v1/jobs/{job_id}")
    summary = _job_summary(job)
    summary["description"] = sanitize(str(job.get("description", "")), 200)
    return summary


def start_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Start a backup job. Runs as an async session. Inverse: stop_job."""
    conn.post(f"/api/v1/jobs/{job_id}/start")
    return {"job_id": sanitize(job_id, 64), "action": "start"}


def stop_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Stop a running backup job. Inverse: start_job."""
    conn.post(f"/api/v1/jobs/{job_id}/stop")
    return {"job_id": sanitize(job_id, 64), "action": "stop"}


def enable_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Enable a backup job (clears the disabled flag). Inverse: disable_job."""
    conn.post(f"/api/v1/jobs/{job_id}/enable")
    return {"job_id": sanitize(job_id, 64), "action": "enable"}


def disable_job(conn: Any, job_id: str) -> dict:
    """[WRITE] Disable a backup job (skips scheduled runs). Inverse: enable_job."""
    conn.post(f"/api/v1/jobs/{job_id}/disable")
    return {"job_id": sanitize(job_id, 64), "action": "disable"}
