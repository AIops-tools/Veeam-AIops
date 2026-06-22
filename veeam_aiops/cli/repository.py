"""``veeam-aiops repository ...`` sub-commands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import TargetOption, cli_errors, get_connection
from veeam_aiops.ops import repositories

repository_app = typer.Typer(help="Backup repository operations.", no_args_is_help=True)
console = Console()


@repository_app.command("list")
@cli_errors
def repository_list(target: TargetOption = None) -> None:
    """List backup repositories (id, name, type, path)."""
    conn, _ = get_connection(target)
    rows = repositories.list_repositories(conn)
    table = Table(title="Veeam Repositories")
    for col in ("id", "name", "type", "path"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["type"], r["path"])
    console.print(table)
