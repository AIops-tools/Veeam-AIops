"""Configuration management for Veeam AIops.

Loads connection targets and settings from a YAML config file. The secret (the
Veeam login password) is NEVER stored in the config file and never on disk in
plaintext: it lives in the encrypted store ``~/.veeam-aiops/secrets.enc`` (see
:mod:`veeam_aiops.secretstore`). For backward compatibility a legacy plaintext
env var (``VEEAM_<TARGET>_PASSWORD``) is still honoured as a fallback, with a
warning nudging migration to the encrypted store.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from veeam_aiops.governance.paths import ops_home
from veeam_aiops.secretstore import SecretStoreError, get_secret, has_store

CONFIG_DIR = ops_home()
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

# Legacy env-var prefix/suffix; also used by the migration helper.
SECRET_ENV_PREFIX = "VEEAM_"
SECRET_ENV_SUFFIX = "_PASSWORD"

_log = logging.getLogger("veeam-aiops.config")


def _secret_env_key(name: str) -> str:
    """Legacy per-target password env var name, e.g. VEEAM_VBR_LAB_PASSWORD."""
    return f"{SECRET_ENV_PREFIX}{name.upper().replace('-', '_')}{SECRET_ENV_SUFFIX}"


def _resolve_secret(name: str) -> str:
    """Return a target's password: encrypted store first, then legacy env var."""
    if has_store():
        try:
            return get_secret(name)
        except SecretStoreError:
            pass  # fall through to legacy env var
    legacy = os.environ.get(_secret_env_key(name))
    if legacy:
        _log.warning(
            "Using plaintext env var %s. Migrate to the encrypted store with "
            "'veeam-aiops secret migrate'.",
            _secret_env_key(name),
        )
        return legacy
    raise OSError(
        f"No password for target '{name}'. Add one with "
        f"'veeam-aiops secret set {name}' (stored encrypted), or run "
        f"'veeam-aiops init'."
    )


@dataclass(frozen=True)
class TargetConfig:
    """A Veeam Backup & Replication REST API connection target.

    The password is sourced from the encrypted secret store (see ``password``),
    never the config file. ``host`` is the VBR server; ``port`` defaults to the
    Veeam REST API port 9419.
    """

    name: str
    host: str
    username: str
    port: int = 9419
    verify_ssl: bool = True

    @property
    def password(self) -> str:
        return _resolve_secret(self.name)

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config."""

    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets) or "(none)"
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load config from YAML; the password comes from the encrypted store."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Run 'veeam-aiops init' to set up a target and store its password "
            f"encrypted, or create {CONFIG_FILE} with a 'targets' list."
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    targets = tuple(
        TargetConfig(
            name=t["name"],
            host=t["host"],
            username=t["username"],
            port=t.get("port", 9419),
            verify_ssl=t.get("verify_ssl", True),
        )
        for t in raw.get("targets", [])
    )

    return AppConfig(targets=targets)
