"""Stored-backup MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import backups as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def backup_list(target: Optional[str] = None) -> list:
    """[READ] List stored backups with id, name, type, creationTime.

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_backups(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def backup_object_list(backup_id: str, target: Optional[str] = None) -> list:
    """[READ] List the protected objects (VMs/agents) inside a stored backup.

    Args:
        backup_id: Veeam backup id (see backup_list).
        target: Veeam target name from config.
    """
    return ops.list_backup_objects(_get_connection(target), backup_id)
