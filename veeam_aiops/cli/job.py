"""``veeam-aiops job ...`` sub-commands."""

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
from veeam_aiops.ops import jobs

job_app = typer.Typer(help="Backup job operations.", no_args_is_help=True)
console = Console()


@job_app.command("list")
@cli_errors
def job_list(target: TargetOption = None) -> None:
    """List backup jobs (id, name, type, status, lastResult)."""
    conn, _ = get_connection(target)
    rows = jobs.list_jobs(conn)
    table = Table(title="Veeam Backup Jobs")
    for col in ("id", "name", "type", "status", "lastResult"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["type"], r["status"], r["lastResult"])
    console.print(table)


@job_app.command("get")
@cli_errors
def job_get(job_id: str, target: TargetOption = None) -> None:
    """Show detail for one backup job."""
    conn, _ = get_connection(target)
    for k, v in jobs.get_job(conn, job_id).items():
        console.print(f"  [cyan]{k}:[/] {v}")


@job_app.command("start")
@cli_errors
def job_start(job_id: str, target: TargetOption = None) -> None:
    """Start a backup job."""
    conn, _ = get_connection(target)
    jobs.start_job(conn, job_id)
    console.print(f"[green]Started job {job_id}[/] (poll with 'session list')")


@job_app.command("stop")
@cli_errors
def job_stop(
    job_id: str, target: TargetOption = None, dry_run: DryRunOption = False
) -> None:
    """Stop a running backup job (destructive — double confirm)."""
    if dry_run:
        dry_run_print(operation="stop_job", api_call=f"POST /api/v1/jobs/{job_id}/stop")
        return
    double_confirm("stop", f"job {job_id}")
    conn, _ = get_connection(target)
    jobs.stop_job(conn, job_id)
    console.print(f"[green]Stopped job {job_id}[/]")


@job_app.command("enable")
@cli_errors
def job_enable(job_id: str, target: TargetOption = None) -> None:
    """Enable a backup job."""
    conn, _ = get_connection(target)
    jobs.enable_job(conn, job_id)
    console.print(f"[green]Enabled job {job_id}[/]")


@job_app.command("disable")
@cli_errors
def job_disable(job_id: str, target: TargetOption = None) -> None:
    """Disable a backup job (skips scheduled runs)."""
    conn, _ = get_connection(target)
    jobs.disable_job(conn, job_id)
    console.print(f"[green]Disabled job {job_id}[/]")
