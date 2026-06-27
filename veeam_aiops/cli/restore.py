"""``veeam-aiops restore ...`` sub-commands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    double_confirm,
    dry_run_print,
    get_connection,
)
from veeam_aiops.ops import restore

restore_app = typer.Typer(help="Restore operations.", no_args_is_help=True)
console = Console()


@restore_app.command("list-points")
@cli_errors
def restore_list_points(
    target: TargetOption = None,
    backup_id: str = typer.Option(None, "--backup-id", help="Filter to one backup's points"),
) -> None:
    """List available restore points (optionally filtered by backup)."""
    conn, _ = get_connection(target)
    rows = restore.list_restore_points(conn, backup_id)
    table = Table(title="Veeam Restore Points")
    for col in ("id", "name", "creationTime", "type"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["creationTime"], r["type"])
    console.print(table)


@restore_app.command("start")
@cli_errors
def restore_start(
    restore_point_id: str = typer.Option(..., "--restore-point-id", help="Restore point id"),
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Start a VM restore (IRREVERSIBLE — double confirm)."""
    if dry_run:
        dry_run_print(
            operation="start_vm_restore",
            api_call="POST /api/v1/restore/vm",
            parameters={"restorePointId": restore_point_id},
        )
        return
    double_confirm("start VM restore (overwrites/creates a VM)", restore_point_id)
    conn, _ = get_connection(target)
    restore.start_vm_restore(conn, restore_point_id)
    console.print(f"[green]Restore started from {restore_point_id}[/] (poll with 'session list')")
