"""``veeam-aiops init`` — a friendly, interactive onboarding wizard.

Walks a new user through connecting their first Veeam Backup & Replication
target: collects the non-secret connection details into ``config.yaml`` and the
login password into the *encrypted* store (never plaintext on disk). Designed
to be run on a terminal; everything it needs is prompted with sensible
defaults.
"""

from __future__ import annotations

import getpass

import typer
import yaml

from veeam_aiops.cli._common import cli_errors, console
from veeam_aiops.config import CONFIG_DIR, CONFIG_FILE
from veeam_aiops.secretstore import SecretStore, resolve_master_password


def _load_existing_targets() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    raw = yaml.safe_load(CONFIG_FILE.read_text("utf-8")) or {}
    return list(raw.get("targets", []))


def _write_targets(targets: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    CONFIG_FILE.write_text(yaml.safe_dump({"targets": targets}, sort_keys=False), "utf-8")


@cli_errors
def init_cmd() -> None:
    """Interactively set up your first Veeam connection."""
    console.print("[bold cyan]Veeam AIops — setup wizard[/]")
    console.print(
        "This collects connection details (saved to config.yaml) and your Veeam "
        "login password (saved [bold]encrypted[/] to secrets.enc).\n"
    )

    console.print("[bold]Step 1 — master password[/]")
    console.print(
        "[dim]Encrypts secrets.enc. You'll set it via the "
        "VEEAM_AIOPS_MASTER_PASSWORD env var for non-interactive/MCP use.[/]"
    )
    password = resolve_master_password(confirm_if_new=True)
    store = SecretStore.unlock(password)

    targets = _load_existing_targets()
    existing_names = {t.get("name") for t in targets}

    while True:
        console.print("\n[bold]Step 2 — add a target[/]")
        name = typer.prompt("Target name (e.g. vbr-lab)").strip()
        if name in existing_names:
            if not typer.confirm(f"'{name}' already exists — overwrite?", default=False):
                continue
            targets = [t for t in targets if t.get("name") != name]

        host = typer.prompt("Host (IP or FQDN of the VBR server)").strip()
        console.print(
            "[dim]Username format: a Veeam account, e.g. DOMAIN\\user or a local "
            "Windows account on the VBR server.[/]"
        )
        username = typer.prompt("Username").strip()
        port = typer.prompt("REST API port", default=9419, type=int)
        verify_ssl = typer.confirm(
            "Verify TLS certificate? (No for self-signed lab certs)", default=False
        )

        secret = getpass.getpass(f"Login password for '{name}' (hidden): ")
        store = store.set(name, secret)

        entry = {
            "name": name,
            "host": host,
            "username": username,
            "port": port,
            "verify_ssl": verify_ssl,
        }
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        console.print(f"[green]✓ Saved target '{name}' (password stored encrypted).[/]")

        if not typer.confirm("\nAdd another target?", default=False):
            break

    console.print(f"\n[green]✓ Setup complete.[/] Config: {CONFIG_FILE}")
    console.print(
        "[dim]Tip: export VEEAM_AIOPS_MASTER_PASSWORD=... in your shell profile "
        "so the MCP server and CLI can unlock secrets non-interactively.[/]"
    )
    if typer.confirm("Run a connectivity check now (veeam-aiops doctor)?", default=True):
        from veeam_aiops.doctor import run_doctor

        raise typer.Exit(run_doctor())
