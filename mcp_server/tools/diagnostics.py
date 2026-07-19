"""Diagnostics / RCA MCP tools: job-failure triage and repository capacity.

Read-only signature analyses (risk_level="low"). Each tool collects Veeam
telemetry once and hands it to a pure analysis function in
``veeam_aiops.ops.diagnostics`` — so the heuristics stay unit-testable without a
live VBR server, and the collection stays here where the connection is.
"""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import diagnostics as diag
from veeam_aiops.ops import repositories as repo_ops
from veeam_aiops.ops import sessions as session_ops

# Session results that warrant pulling the failing log records for classification.
_FAIL_RESULTS = {"failed", "warning"}
# Log-record statuses that identify the failing step inside a session.
_FAIL_STATUSES = {"failed", "warning", "error"}


def _failing_log_titles(conn: Any, session_id: str) -> list[str]:
    """Best-effort titles of the failing records in one session's log."""
    try:
        logs = session_ops.get_session_log(conn, session_id)
    except Exception:  # noqa: BLE001 — advisory context; one bad log must not blank RCA
        return []
    return [
        rec["title"]
        for rec in logs
        if rec.get("title") and str(rec.get("status") or "").lower() in _FAIL_STATUSES
    ]


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def job_failure_rca(target: Optional[str] = None) -> dict:
    """[READ] Triage recent backup-job sessions: flag every Failed/Warning run.

    Pulls the recent sessions, fetches the failing log records for each bad run,
    and categorizes the likely cause (repository full, source/guest unreachable,
    credential/VSS failure, retry exhaustion) worst-first, citing the session
    result and the matched error substring for every finding.

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    conn = _get_connection(target)
    session_rows = session_ops.list_sessions(conn)
    error_index: dict[str, list[str]] = {}
    for s in session_rows:
        if str(s.get("result") or "").lower() in _FAIL_RESULTS:
            sid = str(s.get("id") or "")
            if sid:
                error_index[sid] = _failing_log_titles(conn, sid)
    return diag.job_failure_findings(session_rows, error_index)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def repository_capacity_rca(target: Optional[str] = None) -> dict:
    """[READ] Flag backup repositories running low on free space.

    Pulls per-repository capacity state and reports worst-first findings for any
    repository under the free-space thresholds (<15% warn, <10% critical), each
    citing the measured free% and free bytes plus an extend/offload/retention
    action.

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    conn = _get_connection(target)
    rows = repo_ops.repository_state(conn)
    return diag.repository_capacity_findings(rows)
