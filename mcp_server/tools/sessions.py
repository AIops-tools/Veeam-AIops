"""Session MCP tools (read-only): poll async job/restore progress."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import sessions as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def session_list(target: Optional[str] = None) -> list:
    """[READ] List recent sessions with id, name, type, state, result.

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_sessions(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def session_get(session_id: str, target: Optional[str] = None) -> dict:
    """[READ] Poll one session by id to check job/restore progress.

    Use after job_start or start_vm_restore to follow the operation instead of
    re-issuing it.

    Args:
        session_id: Veeam session id (see session_list).
        target: Veeam target name from config.
    """
    return ops.get_session(_get_connection(target), session_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def session_log(session_id: str, target: Optional[str] = None) -> list:
    """[READ] Return the log records (events) of one session.

    Use to see why a session failed instead of re-running the job blind.

    Args:
        session_id: Veeam session id (see session_list).
        target: Veeam target name from config.
    """
    return ops.get_session_log(_get_connection(target), session_id)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def session_stop(session_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE] Stop a running session (cancels the underlying operation).

    No undo token — a stopped session must be re-issued via the originating
    job/restore. Pass dry_run=True to preview.

    Args:
        session_id: Veeam session id (see session_list).
        dry_run: If True, preview without stopping.
        target: Veeam target name from config.
    """
    if dry_run:
        return {"dryRun": True, "wouldStopSession": {"session_id": session_id}}
    return ops.stop_session(_get_connection(target), session_id)
