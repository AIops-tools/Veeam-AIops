"""Diagnostics / RCA MCP tools: job-failure triage and repository capacity.

Read-only signature analyses (risk_level="low"). Each tool collects Veeam
telemetry once and hands it to a pure analysis function in
``veeam_aiops.ops.diagnostics`` — so the heuristics stay unit-testable without a
live VBR server, and the collection stays here where the connection is.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import diagnostics as diag
from veeam_aiops.ops import repositories as repo_ops
from veeam_aiops.ops import sessions as session_ops

# Session results that warrant pulling the failing log records for classification.
_FAIL_RESULTS = {"failed", "warning"}


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def job_failure_rca(
    limit: int = 100, since_hours: Optional[int] = None, target: Optional[str] = None
) -> dict:
    """[READ] Triage recent backup-job sessions: flag every Failed/Warning run.

    Pulls the newest `limit` sessions, fetches the failing log records for each
    bad run, and categorizes the likely cause (repository full, source/guest
    unreachable, credential/VSS failure, retry exhaustion) worst-first, citing
    the session result and the matched error substring for every finding.
    sessionsTruncated=true means older sessions were not analysed. For "what
    failed last night", pass since_hours=24 so the window is a time span rather
    than whatever fits in the newest `limit` sessions of every type.

    Args:
        limit: Newest sessions to analyse, 1-1000 (default 100).
        since_hours: Only sessions created in the last N hours (optional).
        target: Veeam target name from config; omit to use the default.
    """
    conn = _get_connection(target)
    window = session_ops.list_sessions(conn, limit=limit, since_hours=since_hours)
    session_rows = window["sessions"]
    error_index, unreadable = session_ops.collect_failure_logs(conn, session_rows, _FAIL_RESULTS)
    return diag.job_failure_findings(session_rows, error_index,
                                     sessions_truncated=window["truncated"],
                                     sessions_since=window["since"],
                                     logs_unreadable=unreadable)


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
