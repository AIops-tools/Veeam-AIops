"""Restore MCP tools: list restore points, start a VM restore.

start_vm_restore is high-risk with NO undo token — restoring a VM overwrites
or creates one and cannot be reversed by the harness.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import restore as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def restore_list_points(target: Optional[str] = None) -> list:
    """[READ] List available restore points (id, name, creationTime, type).

    Args:
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_restore_points(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def start_vm_restore(restore_point_id: str, target: Optional[str] = None) -> dict:
    """[WRITE] Start a VM restore from a restore point. IRREVERSIBLE — no undo token.

    Overwrites or creates a VM; confirm with the user before calling. Runs as an
    async session — poll with session_list / session_get.

    Args:
        restore_point_id: Restore point id (see restore_list_points).
        target: Veeam target name from config.
    """
    return ops.start_vm_restore(_get_connection(target), restore_point_id)
