"""Backup repository MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import repositories as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def repository_list(target: Optional[str] = None) -> list:
    """[READ] List backup repositories with id, name, type, path.

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_repositories(_get_connection(target))
