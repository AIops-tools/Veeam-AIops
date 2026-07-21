"""CLI write-command coverage beyond ``job stop`` (already in test_cli_writes).

Covers the un-confirmed governed writes (``job start/retry/enable/disable``),
the dry-run + double-confirm writes (``session stop``, ``restore start``), and
the ``_common`` helpers (real ``get_connection`` wiring, dry-run param preview).
Confirmed writes are driven PAST the prompts and asserted to reach the governed
twin (audit row on disk + correct outbound REST path).
"""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

import veeam_aiops.governance.audit as audit_mod
import veeam_aiops.governance.policy as policy_mod
import veeam_aiops.governance.undo as undo_mod

runner = CliRunner()


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _audit_tools(db_path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


# ─── un-confirmed governed job writes ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "cmd, endpoint, tool",
    [
        ("start", "/api/v1/jobs/job-1/start", "job_start"),
        ("retry", "/api/v1/jobs/job-1/retry", "job_retry"),
        ("enable", "/api/v1/jobs/job-1/enable", "job_enable"),
        ("disable", "/api/v1/jobs/job-1/disable", "job_disable"),
    ],
)
def test_cli_job_write_goes_through_governed_twin(
    gov_home, monkeypatch, fake_veeam, cmd, endpoint, tool
):
    import mcp_server.tools.jobs as gov_jobs
    from veeam_aiops.cli import app

    fake = fake_veeam(responses={"/api/v1/jobs/job-1": {"status": "Idle"}})
    monkeypatch.setattr(gov_jobs, "_get_connection", lambda target=None: fake)
    result = runner.invoke(app, ["job", cmd, "job-1"])
    assert result.exit_code == 0, result.output
    assert endpoint in fake.paths("POST")
    assert _audit_tools(gov_home / "audit.db") == [tool]


# ─── session stop: dry-run vs confirmed ──────────────────────────────────────


@pytest.mark.unit
def test_cli_session_stop_dry_run_writes_nothing_but_is_still_audited(
    gov_home, monkeypatch, fake_veeam
):
    """A dry_run MAY read; it must never write. It is audited like any other
    governed call — the MCP path always was, the CLI was the inconsistency."""
    import mcp_server.tools.sessions as gov_sessions
    from veeam_aiops.cli import app

    fake = fake_veeam()
    monkeypatch.setattr(gov_sessions, "_get_connection", lambda target=None: fake)
    result = runner.invoke(app, ["session", "stop", "sess-1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert "/api/v1/sessions/sess-1/stop" in result.output
    assert fake.paths("POST") == [] and fake.paths("DELETE") == []
    assert _audit_tools(gov_home / "audit.db") == ["session_stop"]


@pytest.mark.unit
def test_cli_session_stop_confirmed_goes_through_governance(gov_home, monkeypatch, fake_veeam):
    import mcp_server.tools.sessions as gov_sessions
    from veeam_aiops.cli import app

    fake = fake_veeam(responses={"/api/v1/sessions/sess-1": {}})
    monkeypatch.setattr(gov_sessions, "_get_connection", lambda target=None: fake)
    result = runner.invoke(app, ["session", "stop", "sess-1"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert "/api/v1/sessions/sess-1/stop" in fake.paths("POST")
    assert _audit_tools(gov_home / "audit.db") == ["session_stop"]


@pytest.mark.unit
def test_cli_session_stop_aborts_without_second_confirm(gov_home, monkeypatch, fake_veeam):
    import mcp_server.tools.sessions as gov_sessions
    from veeam_aiops.cli import app

    fake = fake_veeam()
    monkeypatch.setattr(gov_sessions, "_get_connection", lambda target=None: fake)
    result = runner.invoke(app, ["session", "stop", "sess-1"], input="y\nn\n")
    assert result.exit_code != 0
    assert fake.calls == []


# ─── restore start: dry-run vs confirmed (high risk, no undo) ─────────────────


@pytest.mark.unit
def test_cli_restore_start_dry_run_names_the_vm_it_would_overwrite(
    gov_home, monkeypatch, fake_veeam
):
    """Dry-run must show what a GUID actually is: nobody can approve an
    irreversible in-place restore from an opaque id."""
    import mcp_server.tools.restore as gov_restore
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/restorePoints/rp-7": {"id": "rp-7", "name": "sql-01",
                                           "creationTime": "2026-07-19T02:00:00Z"}
        }
    )
    # The dry-run runs through the governed twin, so patch the twin's connection.
    monkeypatch.setattr(gov_restore, "_get_connection", lambda target=None: fake)
    result = runner.invoke(
        app, ["restore", "start", "--restore-point-id", "rp-7", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert "rp-7" in result.output  # the restorePointId parameter printed
    assert "sql-01" in result.output  # ...and the VM it would overwrite
    assert "2026-07-19" in result.output
    # It reads (that is how it resolved the name) but writes nothing, and is
    # audited like any other governed call.
    assert fake.mutating() == []
    assert fake.paths("GET"), "the preview is expected to read, and does"
    assert _audit_tools(gov_home / "audit.db") == ["start_vm_restore"]


@pytest.mark.unit
def test_cli_restore_start_confirmed_posts_and_audits(gov_home, monkeypatch, fake_veeam):
    import mcp_server.tools.restore as gov_restore
    from veeam_aiops.cli import app

    fake = fake_veeam(responses={"/api/v1/restore/vm": {}})
    monkeypatch.setattr(gov_restore, "_get_connection", lambda target=None: fake)
    result = runner.invoke(
        app, ["restore", "start", "--restore-point-id", "rp-7"], input="y\ny\n"
    )
    assert result.exit_code == 0, result.output
    assert "/api/v1/restore/vm" in fake.paths("POST")
    _, _, kwargs = [c for c in fake.calls if c[0] == "POST"][0]
    assert kwargs["json"] == {"restorePointId": "rp-7"}
    assert _audit_tools(gov_home / "audit.db") == ["start_vm_restore"]


# ─── _common.get_connection real wiring ──────────────────────────────────────


@pytest.mark.unit
def test_common_get_connection_builds_manager(tmp_path, monkeypatch):
    """get_connection loads config from a real yaml and returns (conn, cfg),
    with the manager's connect() honoured."""
    from veeam_aiops.cli import _common

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "targets:\n"
        "  - name: lab\n"
        "    host: vbr.local\n"
        "    username: admin\n"
        "    verify_ssl: false\n"
    )

    sentinel = object()

    class _FakeMgr:
        def __init__(self, cfg):
            self.cfg = cfg

        def connect(self, target):
            return sentinel

    monkeypatch.setattr("veeam_aiops.connection.ConnectionManager", _FakeMgr)
    conn, cfg = _common.get_connection(None, config_path=cfg_file)
    assert conn is sentinel
    assert cfg.targets[0].name == "lab"
