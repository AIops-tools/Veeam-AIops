"""Top-level Typer app: assembles sub-apps and top-level commands."""

from __future__ import annotations

import typer

from veeam_aiops.cli._common import cli_errors
from veeam_aiops.cli.backup import backup_app
from veeam_aiops.cli.doctor import doctor_cmd
from veeam_aiops.cli.infrastructure import infra_app
from veeam_aiops.cli.init import init_cmd
from veeam_aiops.cli.job import job_app
from veeam_aiops.cli.overview import overview_cmd
from veeam_aiops.cli.repository import repository_app
from veeam_aiops.cli.restore import restore_app
from veeam_aiops.cli.secret import secret_app
from veeam_aiops.cli.session import session_app
from veeam_aiops.cli.undo import undo_app

app = typer.Typer(
    name="veeam-aiops",
    help="Veeam Backup & Replication AI-powered backup operations.",
    no_args_is_help=True,
)

app.add_typer(job_app, name="job")
app.add_typer(restore_app, name="restore")
app.add_typer(repository_app, name="repository")
app.add_typer(session_app, name="session")
app.add_typer(backup_app, name="backup")
app.add_typer(infra_app, name="infra")
app.add_typer(secret_app, name="secret")
app.add_typer(undo_app, name="undo")
app.command("init")(init_cmd)
app.command("overview")(overview_cmd)
app.command("doctor")(doctor_cmd)


@app.command("mcp")
@cli_errors
def mcp_cmd() -> None:
    """Start the MCP server (stdio transport).

    Single-command entry point for MCP clients (does not go through uvx/PyPI
    resolution at launch):
        veeam-aiops mcp
    """
    import sys

    if sys.version_info < (3, 11):
        typer.echo(
            f"ERROR: veeam-aiops requires Python >= 3.11 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}).\n"
            f"Fix: uv python install 3.12 && "
            f"uv tool install --python 3.12 --force veeam-aiops",
            err=True,
        )
        raise typer.Exit(2)

    from mcp_server.server import main as _mcp_main

    _mcp_main()


if __name__ == "__main__":
    app()
