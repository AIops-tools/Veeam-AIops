"""Tests for the ``veeam-aiops init`` onboarding wizard.

Driven end-to-end through Typer's CliRunner against an isolated
``VEEAM_AIOPS_HOME`` — nothing touches the real ``~/.veeam-aiops`` and no
network connection is ever attempted (the closing doctor prompt is declined).
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

import veeam_aiops.cli.init as init_mod
import veeam_aiops.config as config_mod
import veeam_aiops.secretstore as ss
from veeam_aiops.cli._root import app

pytestmark = pytest.mark.unit

MASTER_PW = "wizard-master-pw"
runner = CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point every path constant the wizard touches at a throwaway home."""
    config_file = tmp_path / "config.yaml"
    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    monkeypatch.setenv(ss.MASTER_PASSWORD_ENV, MASTER_PW)

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_mod, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(init_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(init_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    return tmp_path


@pytest.fixture
def fake_getpass(monkeypatch):
    """The wizard reads the login password via getpass (bypasses stdin)."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "vbr-login-pw")


def _run_init(answers: list[str]):
    return runner.invoke(app, ["init"], input="".join(a + "\n" for a in answers))


# name, host, username, port, verify TLS, add another?, run doctor?
HAPPY_ANSWERS = ["vbr-lab", "192.0.2.10", "LAB\\backup-admin", "9419", "y", "n", "n"]


def test_wizard_writes_config_to_isolated_home(isolated_home, fake_getpass):
    result = _run_init(HAPPY_ANSWERS)
    assert result.exit_code == 0, result.output

    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"] == [
        {
            "name": "vbr-lab",
            "host": "192.0.2.10",
            "username": "LAB\\backup-admin",
            "port": 9419,
            "verify_ssl": True,
        }
    ]


def test_password_lands_encrypted_not_in_config(isolated_home, fake_getpass):
    _run_init(HAPPY_ANSWERS)

    config_text = (isolated_home / "config.yaml").read_text("utf-8")
    assert "vbr-login-pw" not in config_text
    secrets_blob = (isolated_home / "secrets.enc").read_text("utf-8")
    assert "vbr-login-pw" not in secrets_blob
    assert ss.SecretStore.unlock(MASTER_PW).get("vbr-lab") == "vbr-login-pw"


def test_init_writes_no_policy_rules(isolated_home, fake_getpass):
    """The skill no longer authorizes, so init seeds no rules.yaml — a fresh
    install delivers full functionality and leaves permission to the account."""
    result = _run_init(HAPPY_ANSWERS)
    assert result.exit_code == 0, result.output
    assert not (isolated_home / "rules.yaml").exists()


def test_verify_ssl_defaults_true_on_enter(isolated_home, fake_getpass):
    # Accept the TLS prompt with a bare Enter — secure default must be True.
    answers = ["vbr-lab", "192.0.2.10", "LAB\\backup-admin", "9419", "", "n", "n"]
    _run_init(answers)
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["verify_ssl"] is True


def test_verify_ssl_can_be_declined(isolated_home, fake_getpass):
    answers = ["vbr-lab", "192.0.2.10", "LAB\\backup-admin", "9419", "n", "n", "n"]
    _run_init(answers)
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["verify_ssl"] is False


def test_port_defaults_to_9419_on_enter(isolated_home, fake_getpass):
    answers = ["vbr-lab", "192.0.2.10", "LAB\\backup-admin", "", "y", "n", "n"]
    _run_init(answers)
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["port"] == 9419


def test_existing_target_kept_when_overwrite_declined(isolated_home, fake_getpass):
    _run_init(HAPPY_ANSWERS)
    # Re-add the same name, decline overwrite, then add a fresh target.
    answers = [
        "vbr-lab",  # duplicate name
        "n",  # overwrite? -> no, loop restarts
        "vbr-new",
        "192.0.2.30",
        "LAB\\backup-admin",
        "9419",
        "y",
        "n",  # add another?
        "n",  # doctor?
    ]
    result = _run_init(answers)
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    names = [t["name"] for t in raw["targets"]]
    assert names == ["vbr-lab", "vbr-new"]
    # Original target untouched.
    assert raw["targets"][0]["host"] == "192.0.2.10"


def test_declining_doctor_prompt_skips_connectivity(isolated_home, fake_getpass, monkeypatch):
    def _boom(*a, **k):  # pragma: no cover — must not be reached
        raise AssertionError("run_doctor must not run when declined")

    monkeypatch.setattr("veeam_aiops.doctor.run_doctor", _boom)
    result = _run_init(HAPPY_ANSWERS)
    assert result.exit_code == 0, result.output
