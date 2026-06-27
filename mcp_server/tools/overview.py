"""Environment health overview MCP tool (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import overview as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def overview(target: Optional[str] = None) -> dict:
    """[READ] One-shot health summary: jobs by last result, repos near full,
    running sessions.

    Call this first to triage a Veeam environment before drilling into a
    specific job, repository, or session.

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.health_overview(_get_connection(target))
