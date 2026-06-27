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


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def repository_get(repository_id: str, target: Optional[str] = None) -> dict:
    """[READ] Detail for one repository incl. capacity/free/used when known.

    Args:
        repository_id: Veeam repository id (see repository_list).
        target: Veeam target name from config.
    """
    return ops.get_repository(_get_connection(target), repository_id)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def repository_state(target: Optional[str] = None) -> list:
    """[READ] Capacity summary for every repository (capacity/free/used/used%).

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.repository_state(_get_connection(target))
