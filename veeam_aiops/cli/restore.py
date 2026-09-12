"""``veeam-aiops restore ...`` sub-commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from mcp_server.tools import restore as gov
from veeam_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    double_confirm,
    dry_run_print,
    get_connection,
    governed,
)
from veeam_aiops.ops import restore

restore_app = typer.Typer(help="Restore operations.", no_args_is_help=True)
console = Console()


@restore_app.command("list-points")
@cli_errors
def restore_list_points(
    target: TargetOption = None,
    backup_id: str = typer.Option(None, "--backup-id", help="Filter to one backup's points"),
    limit: int = typer.Option(100, "--limit", help="Newest points to show (1-1000)."),
) -> None:
    """List the newest restore points (optionally filtered by backup)."""
    conn, _ = get_connection(target)
    out = restore.list_restore_points(conn, backup_id, limit=limit)
    table = Table(title="Veeam Restore Points (newest first)")
    for col in ("id", "name", "creationTime", "type"):
        table.add_column(col)
    for r in out["restorePoints"]:
        table.add_row(r["id"], r["name"], r["creationTime"], r["type"])
    console.print(table)
    if out["truncated"]:
        console.print(f"[yellow]Showing the newest {out['returned']}; older points "
                      f"exist (raise --limit or use --backup-id).[/]")


@restore_app.command("start")
@cli_errors
def restore_start(
    restore_point_id: str = typer.Option(..., "--restore-point-id", help="Restore point id"),
    target: TargetOption = None,
    dry_run: DryRunOption = False,
    acknowledge_unresolved: bool = typer.Option(
        False, "--acknowledge-unresolved",
        help="Restore even though the restore point could not be read and the "
             "target machine is therefore unknown.",
    ),
) -> None:
    """Start a VM restore (IRREVERSIBLE — double confirm).

    The dry-run resolves the restore point to the VM it would overwrite: a GUID
    is not something a human can approve an irreversible restore on. If it
    cannot be read at all the restore is refused, because then nothing can say
    which machine gets overwritten; --acknowledge-unresolved proceeds anyway.
    """
    if dry_run:
        # Through the governed twin, not around it: that is what applies the
        # self-target guard and the audit row to the preview as well.
        result = governed(
            gov.start_vm_restore(
                restore_point_id=restore_point_id, dry_run=True,
                acknowledge_unresolved=acknowledge_unresolved, target=target
            )
        )
        preview = result.get("wouldRestore", {})
        unresolved = "(could not resolve — check the Veeam console before restoring)"
        dry_run_print(
            operation="start_vm_restore",
            api_call="POST /api/v1/restore/vm",
            parameters={
                "restorePointId": restore_point_id,
                "vmName": preview.get("vmName") or unresolved,
                "creationTime": preview.get("creationTime") or unresolved,
            },
        )
        return
    double_confirm("start VM restore (overwrites/creates a VM)", restore_point_id)
    console.print_json(
        json.dumps(
            governed(gov.start_vm_restore(
                restore_point_id=restore_point_id,
                acknowledge_unresolved=acknowledge_unresolved, target=target))
        )
    )
