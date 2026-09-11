"""``veeam-aiops backup ...`` sub-commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import TargetOption, cli_errors, get_connection
from veeam_aiops.ops import backups, footprint, ranking

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


@backup_app.command("objects")
@cli_errors
def backup_objects(backup_id: str, target: TargetOption = None) -> None:
    """List the protected objects (VMs/agents) inside a stored backup."""
    conn, _ = get_connection(target)
    rows = backups.list_backup_objects(conn, backup_id)
    table = Table(title=f"Backup objects ({backup_id})")
    for col in ("id", "name", "type", "objectId"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["name"], r["type"], r["objectId"])
    console.print(table)


def _gib(value: int | None) -> str:
    return "-" if value is None else f"{value / 1024**3:,.1f}"


@backup_app.command("usage")
@cli_errors
def backup_usage(
    name: str = typer.Argument(..., help="Protected object name as Veeam shows it."),
    as_json: bool = typer.Option(False, "--json", help="Print the full payload as JSON."),
    target: TargetOption = None,
) -> None:
    """Backup storage one VM/agent consumes, per backup (needs VBR 12.3+)."""
    conn, _ = get_connection(target)
    out = footprint.object_storage_usage(conn, name)
    if as_json:
        console.print_json(json.dumps(out))
        return
    if not out["found"]:
        console.print(f"[yellow]No protected object named '{name}'.[/] "
                      f"Candidates: {', '.join(out['candidates']) or 'none'}")
        raise typer.Exit(1)
    table = Table(title=f"Backup storage for '{name}' (GiB)")
    for col in ("machine", "backup", "repository", "points", "stored", "full", "incr",
                "shared", "unattr.", "retention"):
        table.add_column(col, overflow="fold")
    for m in out["machines"]:
        for b in m["backups"]:
            ret = b["retention"]
            table.add_row(
                m["identity"], b["backupName"] or b["backupId"],
                b["repositoryName"] or b["repositoryId"] or "-", str(b["restorePoints"]),
                _gib(b["storedBytes"]), _gib(b["fullBytes"]), _gib(b["incrementalBytes"]),
                _gib(b["sharedStoredBytes"]), _gib(b["unattributedStoredBytes"]),
                f"{ret['quantity']} {ret['type']}" if ret["quantity"] is not None else "-",
            )
        for bad in m["unreadableBackups"]:
            console.print(f"[yellow]Unreadable backup {bad['backupId']}: {bad['error']}[/]")
    console.print(table)
    if out["totals"] is None:
        console.print("[yellow]Totals withheld — restore points could not be attributed "
                      "to one machine (see caveats).[/]")
    else:
        console.print(f"Total stored: {_gib(out['totals']['storedBytes'])} GiB "
                      f"(REST {out['apiRevision']['revision']})")
    for caveat in out["caveats"]:
        console.print(f"[dim]• {caveat}[/]")


@backup_app.command("ranking")
@cli_errors
def backup_ranking(
    limit: int = typer.Option(20, "--limit", help="Rows to show (1-500)."),
    max_backups: int = typer.Option(100, "--max-backups", help="Backups to scan (1-1000)."),
    as_json: bool = typer.Option(False, "--json", help="Print the full payload as JSON."),
    target: TargetOption = None,
) -> None:
    """Protected objects ranked by backup storage consumed, largest first."""
    conn, _ = get_connection(target)
    out = ranking.storage_ranking(conn, limit=limit, max_backups=max_backups)
    if as_json:
        console.print_json(json.dumps(out))
        return
    table = Table(title="Backup storage ranking (GiB)")
    for col in ("rank", "name", "stored", "files", "backups"):
        table.add_column(col, overflow="fold")
    for row in out["objects"]:
        table.add_row(str(row["rank"]), row["name"] or row["identity"], _gib(row["storedBytes"]),
                      str(row["files"]), ", ".join(row["backups"]))
    console.print(table)
    console.print(
        f"{out['returned']} of {out['objectsTotal']} objects; scanned "
        f"{out['backupsScanned']} of {out['backupsTotal']} backups. Shared: "
        f"{_gib(out['sharedStoredBytes'])} GiB, unresolved: "
        f"{_gib(out['unresolvedStoredBytes'])} GiB (not charged)."
    )
    for bad in out["unreadableBackups"]:
        console.print(f"[yellow]Unreadable backup {bad['backupName']}: {bad['error']}[/]")
    for caveat in out["caveats"]:
        console.print(f"[dim]• {caveat}[/]")
