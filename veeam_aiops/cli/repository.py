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


@repository_app.command("get")
@cli_errors
def repository_get(repository_id: str, target: TargetOption = None) -> None:
    """Show detail for one repository (incl. capacity/free/used when known)."""
    conn, _ = get_connection(target)
    for k, v in repositories.get_repository(conn, repository_id).items():
        console.print(f"  [cyan]{k}:[/] {v}")


@repository_app.command("state")
@cli_errors
def repository_state(target: TargetOption = None) -> None:
    """Capacity summary for every repository (capacity/free/used/used%)."""
    conn, _ = get_connection(target)
    rows = repositories.repository_state(conn)
    table = Table(title="Veeam Repository Capacity")
    for col in ("id", "name", "capacity", "free", "used", "usedPercent"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["id"], r["name"], str(r.get("capacity", "")), str(r.get("free", "")),
            str(r.get("used", "")), str(r.get("usedPercent", "")),
        )
    console.print(table)
