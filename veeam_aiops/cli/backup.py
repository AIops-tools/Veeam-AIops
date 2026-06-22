"""``veeam-aiops backup ...`` sub-commands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import TargetOption, cli_errors, get_connection
from veeam_aiops.ops import backups

backup_app = typer.Typer(help="Stored backup operations.", no_args_is_help=True)
console = Console()


@backup_app.command("list")
@cli_errors
def backup_list(target: TargetOption = None) -> None:
    """List stored backups (id, name, type, creationTime)."""
    conn, _ = get_connection(target)
    rows = backups.list_backups(conn)
    table = Table(title="Veeam Backups")
    for col in ("id", "name", "type", "creationTime"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["type"], r["creationTime"])
    console.print(table)
