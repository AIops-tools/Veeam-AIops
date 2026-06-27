"""``veeam-aiops infra ...`` sub-commands (backup infrastructure inventory)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import TargetOption, cli_errors, get_connection
from veeam_aiops.ops import infrastructure

infra_app = typer.Typer(help="Backup infrastructure inventory.", no_args_is_help=True)
console = Console()


@infra_app.command("servers")
@cli_errors
def managed_servers(target: TargetOption = None) -> None:
    """List managed servers (id, name, type, description)."""
    conn, _ = get_connection(target)
    rows = infrastructure.list_managed_servers(conn)
    table = Table(title="Veeam Managed Servers")
    for col in ("id", "name", "type", "description"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["type"], r["description"])
    console.print(table)


@infra_app.command("proxies")
@cli_errors
def proxies(target: TargetOption = None) -> None:
    """List backup proxies (id, name, type, server)."""
    conn, _ = get_connection(target)
    rows = infrastructure.list_proxies(conn)
    table = Table(title="Veeam Backup Proxies")
    for col in ("id", "name", "type", "server"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["type"], r["server"])
    console.print(table)
