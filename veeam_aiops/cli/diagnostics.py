"""``veeam-aiops diagnose ...`` sub-commands — read-only RCA over Veeam."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import TargetOption, audited, cli_errors, get_connection
from veeam_aiops.ops import diagnostics as diag
from veeam_aiops.ops import repositories as repo_ops
from veeam_aiops.ops import sessions as session_ops

diagnose_app = typer.Typer(
    help="Read-only diagnostics / RCA over Veeam Backup & Replication.",
    no_args_is_help=True,
)
console = Console()

_SEVERITY_STYLE = {"critical": "red", "warning": "yellow", "info": "cyan"}
_FAIL_RESULTS = {"failed", "warning"}


def _print_findings(findings: list[dict]) -> None:
    """Render worst-first findings as a table, or a green all-clear line."""
    if not findings:
        console.print("[green]No findings — all measured values under threshold.[/]")
        return
    table = Table(title="Findings (worst first)")
    for col in ("severity", "resource", "signal", "detail", "action"):
        table.add_column(col, overflow="fold")
    for f in findings:
        style = _SEVERITY_STYLE.get(f["severity"], "white")
        table.add_row(
            f"[{style}]{f['severity']}[/]", f.get("resource", ""),
            f["signal"], f["detail"], f["action"],
        )
    console.print(table)


@diagnose_app.command("job-failures")
@cli_errors
@audited
def diagnose_job_failures(
    target: TargetOption = None,
    limit: int = typer.Option(100, "--limit", help="Newest sessions to analyse (1-1000)."),
    since_hours: int = typer.Option(None, "--since-hours",
                                    help="Only sessions created in the last N hours."),
) -> None:
    """Triage recent job sessions: flag Failed/Warning runs and categorize why."""
    conn, _ = get_connection(target)
    window = session_ops.list_sessions(conn, limit=limit, since_hours=since_hours)
    session_rows = window["sessions"]
    error_index, unreadable = session_ops.collect_failure_logs(conn, session_rows, _FAIL_RESULTS)
    result = diag.job_failure_findings(session_rows, error_index,
                                       sessions_truncated=window["truncated"],
                                       sessions_since=window["since"],
                                       logs_unreadable=unreadable)
    console.print(
        f"[bold]Analyzed the newest {result['sessionsAnalyzed']} session(s); "
        f"{result['failures']} failing.[/]"
    )
    if result["sessionsTruncated"]:
        console.print("[yellow]Older sessions were not analysed (raise --limit).[/]")
    if unreadable:
        console.print(f"[yellow]Could not read the log of {len(unreadable)} failed "
                      f"session(s): {', '.join(unreadable[:5])}.[/]")
    _print_findings(result["findings"])


@diagnose_app.command("repo-capacity")
@cli_errors
@audited
def diagnose_repo_capacity(target: TargetOption = None) -> None:
    """Flag repositories low on free space (<15% warn, <10% critical)."""
    conn, _ = get_connection(target)
    rows = repo_ops.repository_state(conn)
    result = diag.repository_capacity_findings(rows)
    console.print(f"[bold]Analyzed {result['repositoriesAnalyzed']} repository(ies).[/]")
    _print_findings(result["findings"])
