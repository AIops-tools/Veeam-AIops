"""``veeam-aiops diagnose ...`` sub-commands — read-only RCA over Veeam."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from veeam_aiops.cli._common import TargetOption, cli_errors, get_connection
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
_FAIL_STATUSES = {"failed", "warning", "error"}


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


def _failing_log_titles(conn: Any, session_id: str) -> list[str]:
    """Best-effort titles of the failing records in one session's log."""
    try:
        logs = session_ops.get_session_log(conn, session_id)
    except Exception:  # noqa: BLE001 — advisory context only
        return []
    return [
        rec["title"]
        for rec in logs
        if rec.get("title") and str(rec.get("status") or "").lower() in _FAIL_STATUSES
    ]


@diagnose_app.command("job-failures")
@cli_errors
def diagnose_job_failures(target: TargetOption = None) -> None:
    """Triage recent job sessions: flag Failed/Warning runs and categorize why."""
    conn, _ = get_connection(target)
    session_rows = session_ops.list_sessions(conn)
    error_index: dict[str, list[str]] = {}
    for s in session_rows:
        if str(s.get("result") or "").lower() in _FAIL_RESULTS:
            sid = str(s.get("id") or "")
            if sid:
                error_index[sid] = _failing_log_titles(conn, sid)
    result = diag.job_failure_findings(session_rows, error_index)
    console.print(
        f"[bold]Analyzed {result['sessionsAnalyzed']} session(s); "
        f"{result['failures']} failing.[/]"
    )
    _print_findings(result["findings"])


@diagnose_app.command("repo-capacity")
@cli_errors
def diagnose_repo_capacity(target: TargetOption = None) -> None:
    """Flag repositories low on free space (<15% warn, <10% critical)."""
    conn, _ = get_connection(target)
    rows = repo_ops.repository_state(conn)
    result = diag.repository_capacity_findings(rows)
    console.print(f"[bold]Analyzed {result['repositoriesAnalyzed']} repository(ies).[/]")
    _print_findings(result["findings"])
