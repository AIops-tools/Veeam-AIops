"""Backup job MCP tools: list/get, start/stop, enable/disable.

Every tool is wrapped with ``@governed_tool`` (the veeam-aiops harness):
budget/runaway guard, audit logging to ~/.veeam-aiops/audit.db with a
descriptive risk tier, and undo-token recording. Write tools with a clean
inverse pass an ``undo=`` lambda so the harness records a reversal descriptor
to the undo store (start↔stop, enable↔disable).
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import jobs as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def job_list(target: Optional[str] = None) -> list:
    """[READ] List backup jobs with id, name, type, status, lastResult.

    Use job_get for full detail of a single job.

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_jobs(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def job_get(job_id: str, target: Optional[str] = None) -> dict:
    """[READ] Return detail for a single backup job by id.

    Args:
        job_id: Veeam job id (see job_list).
        target: Veeam target name from config.
    """
    return ops.get_job(_get_connection(target), job_id)


@mcp.tool()
@governed_tool(
    risk_level="medium",
    undo=lambda params, result: (
        {
            "tool": "job_stop",
            "params": {"job_id": params.get("job_id")},
            "skill": "veeam-aiops",
            "note": "Inverse of job_start: stop the running job.",
        }
        if isinstance(result, dict) and not result.get("dryRun")
        else None
    ),
)
@tool_errors("dict")
def job_start(job_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE] Start a backup job. Runs as an async session. Inverse: job_stop.

    Poll progress with session_list / session_get; do not re-issue. Pass
    dry_run=True to preview.

    Args:
        job_id: Veeam job id.
        dry_run: If True, preview without starting.
        target: Veeam target name from config.
    """
    if dry_run:
        return {"dryRun": True, "wouldStart": {"job_id": job_id}}
    return ops.start_job(_get_connection(target), job_id)


@mcp.tool()
@governed_tool(
    risk_level="medium",
    undo=lambda params, result: (
        {
            "tool": "job_start",
            "params": {"job_id": params.get("job_id")},
            "skill": "veeam-aiops",
            "note": "Inverse of job_stop: start the job again.",
        }
        if isinstance(result, dict) and not result.get("dryRun")
        else None
    ),
)
@tool_errors("dict")
def job_stop(job_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE] Stop a running backup job. Inverse: job_start.

    Pass dry_run=True to preview.

    Args:
        job_id: Veeam job id.
        dry_run: If True, preview without stopping.
        target: Veeam target name from config.
    """
    if dry_run:
        return {"dryRun": True, "wouldStop": {"job_id": job_id}}
    return ops.stop_job(_get_connection(target), job_id)


@mcp.tool()
@governed_tool(
    risk_level="medium",
    undo=lambda params, result: (
        {
            "tool": "job_stop",
            "params": {"job_id": params.get("job_id")},
            "skill": "veeam-aiops",
            "note": "Inverse of job_retry: stop the in-flight retry.",
        }
        if isinstance(result, dict) and not result.get("dryRun")
        else None
    ),
)
@tool_errors("dict")
def job_retry(job_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE] Retry a failed backup job (re-runs failed objects only).

    Runs as an async session — poll with session_list / session_get. Inverse:
    job_stop (cancels the in-flight retry). Pass dry_run=True to preview.

    Args:
        job_id: Veeam job id.
        dry_run: If True, preview without retrying.
        target: Veeam target name from config.
    """
    if dry_run:
        return {"dryRun": True, "wouldRetry": {"job_id": job_id}}
    return ops.retry_job(_get_connection(target), job_id)


@mcp.tool()
@governed_tool(
    risk_level="medium",
    undo=lambda params, result: (
        {
            "tool": "job_disable",
            "params": {"job_id": params.get("job_id")},
            "skill": "veeam-aiops",
            "note": "Inverse of job_enable: disable the job again.",
        }
        if isinstance(result, dict) and not result.get("dryRun")
        else None
    ),
)
@tool_errors("dict")
def job_enable(job_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE] Enable a backup job (clears the disabled flag). Inverse: job_disable.

    Pass dry_run=True to preview.

    Args:
        job_id: Veeam job id.
        dry_run: If True, preview without enabling.
        target: Veeam target name from config.
    """
    if dry_run:
        return {"dryRun": True, "wouldEnable": {"job_id": job_id}}
    return ops.enable_job(_get_connection(target), job_id)


@mcp.tool()
@governed_tool(
    risk_level="medium",
    undo=lambda params, result: (
        {
            "tool": "job_enable",
            "params": {"job_id": params.get("job_id")},
            "skill": "veeam-aiops",
            "note": "Inverse of job_disable: enable the job again.",
        }
        if isinstance(result, dict) and not result.get("dryRun")
        else None
    ),
)
@tool_errors("dict")
def job_disable(job_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE] Disable a backup job (skips scheduled runs). Inverse: job_enable.

    Pass dry_run=True to preview.

    Args:
        job_id: Veeam job id.
        dry_run: If True, preview without disabling.
        target: Veeam target name from config.
    """
    if dry_run:
        return {"dryRun": True, "wouldDisable": {"job_id": job_id}}
    return ops.disable_job(_get_connection(target), job_id)
