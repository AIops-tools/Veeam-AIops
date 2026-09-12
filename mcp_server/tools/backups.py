"""Stored-backup MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from veeam_aiops.governance import governed_tool
from veeam_aiops.ops import backups as ops
from veeam_aiops.ops import footprint, ranking


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


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def backup_object_storage_usage(name: str, target: Optional[str] = None) -> dict:
    """[READ] Backup storage one VM/agent consumes, per backup (showback input).

    Finds the protected object by exact name and sums Veeam's own per-file sizes
    (backupSize = on disk after compression/dedup, dataSize = before) across
    every backup holding it — primary job and backup copies reported separately
    with repository, restore-point count, full vs incremental bytes, GFS files,
    job retention and an increment-vs-full change rate. Needs VBR 12.3+.

    Read before charging: files shared by several machines are reported in
    sharedStoredBytes and never included in storedBytes; `caveats` says when
    storedBytes is an upper bound (block-clone repositories) or when the server
    cannot express shared files. found=false returns name candidates, not a guess.
    No pricing — apply your own rates to the bytes.

    Args:
        name: Protected object name as Veeam shows it (case-insensitive, exact).
        target: Veeam target name from config; omit to use the default.
    """
    return footprint.object_storage_usage(_get_connection(target), name)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def backup_storage_ranking(
    limit: int = 20, max_backups: int = 100, target: Optional[str] = None
) -> dict:
    """[READ] Protected objects ranked by backup storage consumed, largest first.

    Answers "which machines are the most expensive to protect". Each backup file
    is charged to its single owner and one machine is merged across backups
    (vCenter path / moref, agent BIOS UUID), each row carrying an explicit rank.
    Shared and unattributable files are totals, never charged to anyone, split
    into `ownerlessStoredBytes` (per-job chain files naming no owner — ordinary,
    often large) and `unmatchedOwnerStoredBytes` (a file naming an owner its own
    backup does not list). Judge whether these numbers can be billed on
    `unmatchedOwnerFiles` being 0, never on the `unresolved*` sum.
    `truncated` / `backupsTruncated` say when more objects or backups exist than
    were returned or scanned; a ranking with `backupsTruncated` true ranks only
    the scanned subset and must not be reported as an estate-wide ranking.
    Needs VBR 12.3+.

    Args:
        limit: Rows to return, 1-500 (default 20).
        max_backups: Backups to scan, 1-1000 (default 100); raise for full coverage.
        target: Veeam target name from config; omit to use the default.
    """
    return ranking.storage_ranking(_get_connection(target), limit=limit, max_backups=max_backups)
