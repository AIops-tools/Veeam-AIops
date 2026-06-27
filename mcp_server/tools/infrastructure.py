"""Backup infrastructure inventory MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import infrastructure as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def managed_server_list(target: Optional[str] = None) -> list:
    """[READ] List managed servers (id, name, type, description).

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_managed_servers(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def proxy_list(target: Optional[str] = None) -> list:
    """[READ] List backup proxies (id, name, type, server).

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_proxies(_get_connection(target))
