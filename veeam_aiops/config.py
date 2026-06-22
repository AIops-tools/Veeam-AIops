"""Configuration management for Veeam AIops.

Loads connection targets and settings from a YAML config file plus
environment variables. Secrets (the login password) are NEVER stored in the
config file — always sourced from the ``.env`` file or environment, with .env
secret loading and chmod 600 checks.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

CONFIG_DIR = Path.home() / ".veeam-aiops"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"

_log = logging.getLogger("veeam-aiops.config")

# Load secrets from .env (if present) before any config access.
load_dotenv(ENV_FILE)


def _check_env_permissions() -> None:
    """Warn if the .env file is readable beyond the owner (should be 600)."""
    if not ENV_FILE.exists():
        return
    try:
        mode = ENV_FILE.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            _log.warning(
                "Security warning: %s has permissions %s (should be 600). "
                "Run: chmod 600 %s",
                ENV_FILE,
                oct(stat.S_IMODE(mode)),
                ENV_FILE,
            )
    except OSError:
        pass


_check_env_permissions()


def _secret_env_key(name: str) -> str:
    """Per-target password env var name, e.g. VEEAM_VBR_LAB_PASSWORD."""
    return f"VEEAM_{name.upper().replace('-', '_')}_PASSWORD"


@dataclass(frozen=True)
class TargetConfig:
    """A Veeam Backup & Replication REST API connection target.

    The password is sourced from the per-target env var (see ``password``),
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
        env_key = _secret_env_key(self.name)
        value = os.environ.get(env_key, "")
        if not value:
            raise OSError(
                f"Password not found. Set environment variable: {env_key} "
                f"(in {ENV_FILE}, chmod 600)."
            )
        return value

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
    """Load config from YAML; the password comes from env, never the file."""
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Create {CONFIG_FILE} with a 'targets' list and put passwords in "
            f"{ENV_FILE} (chmod 600)."
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
