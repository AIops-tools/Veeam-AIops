"""``veeam-aiops session ...`` sub-commands."""

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


@session_app.command("log")
@cli_errors
def session_log(session_id: str, target: TargetOption = None) -> None:
    """Show the log records (events) of one session."""
    conn, _ = get_connection(target)
    rows = sessions.get_session_log(conn, session_id)
    table = Table(title=f"Session log {session_id}")
    for col in ("title", "status", "startTime", "endTime"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["title"], r["status"], r["startTime"], r["endTime"])
    console.print(table)


@session_app.command("stop")
@cli_errors
def session_stop(
    session_id: str, target: TargetOption = None, dry_run: DryRunOption = False
) -> None:
    """Stop a running session (destructive — double confirm)."""
    if dry_run:
        dry_run_print(
            operation="stop_session",
            api_call=f"POST /api/v1/sessions/{session_id}/stop",
        )
        return
    double_confirm("stop", f"session {session_id}")
    conn, _ = get_connection(target)
    sessions.stop_session(conn, session_id)
    console.print(f"[green]Stopped session {session_id}[/]")
