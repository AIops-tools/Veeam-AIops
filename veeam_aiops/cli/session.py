"""``veeam-aiops session ...`` sub-commands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import TargetOption, cli_errors, get_connection
from veeam_aiops.ops import sessions

session_app = typer.Typer(help="Session (async job/restore progress) operations.",
                          no_args_is_help=True)
console = Console()


@session_app.command("list")
@cli_errors
def session_list(target: TargetOption = None) -> None:
    """List recent sessions (id, name, type, state, result)."""
    conn, _ = get_connection(target)
    rows = sessions.list_sessions(conn)
    table = Table(title="Veeam Sessions")
    for col in ("id", "name", "type", "state", "result"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["type"], r["state"], r["result"])
    console.print(table)


@session_app.command("get")
@cli_errors
def session_get(session_id: str, target: TargetOption = None) -> None:
    """Poll one session to check job/restore progress."""
    conn, _ = get_connection(target)
    for k, v in sessions.get_session(conn, session_id).items():
        console.print(f"  [cyan]{k}:[/] {v}")
