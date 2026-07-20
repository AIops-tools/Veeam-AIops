"""Restore MCP tools: list restore points, start a VM restore.

start_vm_restore is high-risk with NO undo token — restoring a VM overwrites
or creates one and cannot be reversed by the harness. Its dry-run resolves the
opaque restore-point id to the VM name and creation time, because an approver
gating an irreversible overwrite cannot judge a GUID; and it refuses outright
when that VM name matches the configured VBR host (see ops.restore).
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import restore as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def restore_list_points(
    backup_id: Optional[str] = None, target: Optional[str] = None
) -> list:
    """[READ] List restore points (id, name, creationTime, type).

    Pass backup_id to filter to one backup's restore points (preview — server
    filter support varies by Veeam version).

    Args:
        backup_id: Optional Veeam backup id (see backup_list) to filter by.
        target: Veeam target name from config; omit to use the default.
    """
    return ops.list_restore_points(_get_connection(target), backup_id)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def start_vm_restore(
    restore_point_id: str, dry_run: bool = False, target: Optional[str] = None
) -> dict:
    """[WRITE] Start a VM restore from a restore point. IRREVERSIBLE — no undo token.

    Overwrites or creates a VM; confirm with the user before calling. Runs as an
    async session — poll with session_list / session_get. Pass dry_run=True to
    preview: the preview resolves the opaque restore-point id to the VM name and
    creation time it would restore, so there is something real to confirm.

    Refuses when that VM name matches the configured VBR host — with no target
    mapping this is a restore-to-original, so it would overwrite the Veeam
    server serving this API. The name check is a safety net, not a proof (a VM
    display name is not a hostname) and it fails open when the restore point
    cannot be resolved; read the preview before approving.

    Args:
        restore_point_id: Restore point id (see restore_list_points).
        dry_run: If True, preview without restoring.
        target: Veeam target name from config.
    """
    conn = _get_connection(target)
    if dry_run:
        return {
            "dryRun": True,
            "wouldRestore": ops.preview_vm_restore(conn, restore_point_id),
        }
    return ops.start_vm_restore(conn, restore_point_id)
