"""CLI read-command coverage — table/detail rendering + teaching-error paths.

The read sub-commands (``session``/``repository``/``backup``/``infra``/
``restore``/``overview``/``job`` reads) go ``get_connection`` -> ops -> Rich
render. These drive each command with ``get_connection`` mocked to a
``FakeVeeam`` (conftest) so we assert the *real* REST contract each command
issues (endpoint path + params) and that canned realistic API rows normalise
onto the printed output, plus the ``@cli_errors`` teaching-error translation.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from veeam_aiops.connection import VeeamApiError

runner = CliRunner()


def _wire(monkeypatch, module_name: str, fake) -> None:
    """Point one CLI module's ``get_connection`` at a FakeVeeam double."""
    monkeypatch.setattr(
        f"veeam_aiops.cli.{module_name}.get_connection",
        lambda target=None, config_path=None: (fake, None),
    )


# ─── session ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_session_list_hits_endpoint_and_normalises(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/sessions": {
                "data": [
                    {
                        "id": "sess-1",
                        "name": "Daily-SQL Backup",
                        "sessionType": "BackupJob",
                        "state": "Working",
                        "result": {"result": "None"},
                    }
                ]
            }
        }
    )
    _wire(monkeypatch, "session", fake)
    result = runner.invoke(app, ["session", "list"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/sessions"]
    assert "sess-1" in result.output


@pytest.mark.unit
def test_session_get_prints_detail_and_progress(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/sessions/sess-9": {
                "id": "sess-9",
                "name": "Restore",
                "sessionType": "Restore",
                "state": "Working",
                "progressPercent": 42,
                "creationTime": "2026-07-13T10:00:00Z",
            }
        }
    )
    _wire(monkeypatch, "session", fake)
    result = runner.invoke(app, ["session", "get", "sess-9"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/sessions/sess-9"]
    assert "42" in result.output
    assert "sess-9" in result.output


@pytest.mark.unit
def test_session_log_renders_records(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/sessions/sess-1/logs": {
                "data": [
                    {
                        "title": "Starting backup",
                        "status": "Success",
                        "startTime": "2026-07-13T10:00:00Z",
                        "endTime": "2026-07-13T10:00:05Z",
                    }
                ]
            }
        }
    )
    _wire(monkeypatch, "session", fake)
    result = runner.invoke(app, ["session", "log", "sess-1"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/sessions/sess-1/logs"]
    assert "Success" in result.output


# ─── repository ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_repository_list_endpoint(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/backupInfrastructure/repositories": {
                "data": [
                    {"id": "repo-1", "name": "Main", "type": "WinLocal", "path": "D:\\vbr"}
                ]
            }
        }
    )
    _wire(monkeypatch, "repository", fake)
    result = runner.invoke(app, ["repository", "list"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/repositories"]
    assert "repo-1" in result.output


@pytest.mark.unit
def test_repository_get_merges_state(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/backupInfrastructure/repositories/repo-1": {
                "id": "repo-1",
                "name": "Main",
                "type": "WinLocal",
                "path": "D:\\vbr",
                "description": "primary",
            },
            "/api/v1/backupInfrastructure/repositories/states": {
                "data": [
                    {"id": "repo-1", "capacityGB": 1000, "freeGB": 100}
                ]
            },
        }
    )
    _wire(monkeypatch, "repository", fake)
    result = runner.invoke(app, ["repository", "get", "repo-1"])
    assert result.exit_code == 0, result.output
    # detail endpoint + states endpoint both consulted
    assert "/api/v1/backupInfrastructure/repositories/repo-1" in fake.paths("GET")
    assert "/api/v1/backupInfrastructure/repositories/states" in fake.paths("GET")
    assert "repo-1" in result.output


@pytest.mark.unit
def test_repository_state_computes_used_percent(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/backupInfrastructure/repositories/states": {
                "data": [
                    {"id": "repo-1", "name": "Main", "type": "WinLocal",
                     "capacityGB": 1000, "freeGB": 100},
                ]
            }
        }
    )
    _wire(monkeypatch, "repository", fake)
    result = runner.invoke(app, ["repository", "state"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/repositories/states"]
    # (1000-100)/1000 = 90.0% used
    assert "90.0" in result.output


# ─── backup ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_backup_list_endpoint(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/backups": {
                "data": [
                    {"id": "bk-1", "name": "SQL", "jobType": "Backup",
                     "creationTime": "2026-07-13T02:00:00Z"}
                ]
            }
        }
    )
    _wire(monkeypatch, "backup", fake)
    result = runner.invoke(app, ["backup", "list"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/backups"]
    assert "bk-1" in result.output


@pytest.mark.unit
def test_backup_objects_endpoint(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/backups/bk-1/objects": {
                "data": [
                    {"id": "obj-1", "name": "vm-db01", "type": "VM", "objectId": "vm-100"}
                ]
            }
        }
    )
    _wire(monkeypatch, "backup", fake)
    result = runner.invoke(app, ["backup", "objects", "bk-1"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/backups/bk-1/objects"]
    assert "obj-1" in result.output


# ─── infrastructure ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_infra_servers_endpoint(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/backupInfrastructure/managedServers": {
                "data": [
                    {"id": "srv-1", "name": "vc01", "type": "VirtualCenter",
                     "description": "prod vCenter"}
                ]
            }
        }
    )
    _wire(monkeypatch, "infrastructure", fake)
    result = runner.invoke(app, ["infra", "servers"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/managedServers"]
    assert "srv-1" in result.output


@pytest.mark.unit
def test_infra_proxies_endpoint_and_nested_server(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/backupInfrastructure/proxies": {
                "data": [
                    {"id": "px-1", "name": "proxy01", "type": "VmwareBackup",
                     "server": {"hostName": "esx01"}}
                ]
            }
        }
    )
    _wire(monkeypatch, "infrastructure", fake)
    result = runner.invoke(app, ["infra", "proxies"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/proxies"]
    assert "px-1" in result.output


# ─── restore (read) ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_restore_list_points_no_filter(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/restorePoints": {
                "data": [
                    {"id": "rp-1", "name": "vm-db01", "creationTime": "2026-07-13T02:00:00Z",
                     "platformName": "VMware"}
                ]
            }
        }
    )
    _wire(monkeypatch, "restore", fake)
    result = runner.invoke(app, ["restore", "list-points"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/restorePoints"]
    # no --backup-id -> params is None (not a backupIdFilter dict)
    _, _, kwargs = fake.calls[0]
    assert kwargs.get("params") is None
    assert "rp-1" in result.output


@pytest.mark.unit
def test_restore_list_points_with_backup_filter(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(responses={"/api/v1/restorePoints": {"data": []}})
    _wire(monkeypatch, "restore", fake)
    result = runner.invoke(app, ["restore", "list-points", "--backup-id", "bk-9"])
    assert result.exit_code == 0, result.output
    _, _, kwargs = fake.calls[0]
    assert kwargs["params"] == {"backupIdFilter": "bk-9"}


# ─── overview ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_overview_fans_out_and_flags_near_full(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/jobs": {"data": [
                {"id": "j1", "name": "A", "lastResult": "Success", "isDisabled": False},
                {"id": "j2", "name": "B", "lastResult": "Failed", "isDisabled": True},
            ]},
            "/api/v1/backupInfrastructure/repositories/states": {"data": [
                {"id": "r1", "name": "Full", "capacityGB": 1000, "freeGB": 50},   # 95% -> near full
                {"id": "r2", "name": "Ok", "capacityGB": 1000, "freeGB": 500},    # 50% -> not
            ]},
            "/api/v1/sessions": {"data": [
                {"id": "s1", "name": "run", "state": "Working"},
                {"id": "s2", "name": "done", "state": "Success"},
            ]},
        }
    )
    _wire(monkeypatch, "overview", fake)
    result = runner.invoke(app, ["overview"])
    assert result.exit_code == 0, result.output
    # near-full repo surfaced, healthy one not; running session counted
    assert "Full" in result.output
    assert "85" in result.output  # nearFullThresholdPercent


# ─── job (read) ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_job_list_endpoint(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/jobs": {"data": [
                {"id": "job-1", "name": "Daily", "type": "Backup",
                 "status": "Running", "lastResult": "Success"}
            ]}
        }
    )
    _wire(monkeypatch, "job", fake)
    result = runner.invoke(app, ["job", "list"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/jobs"]
    assert "job-1" in result.output


@pytest.mark.unit
def test_job_get_prints_schedule(monkeypatch, fake_veeam):
    from veeam_aiops.cli import app

    fake = fake_veeam(
        responses={
            "/api/v1/jobs/job-1": {
                "id": "job-1", "name": "Daily", "type": "Backup",
                "status": "Running", "lastResult": "Success",
                "description": "nightly", "schedule": {"runAutomatically": True},
            }
        }
    )
    _wire(monkeypatch, "job", fake)
    result = runner.invoke(app, ["job", "get", "job-1"])
    assert result.exit_code == 0, result.output
    assert fake.paths("GET") == ["/api/v1/jobs/job-1"]
    assert "job-1" in result.output


# ─── @cli_errors teaching-error translation ──────────────────────────────────


@pytest.mark.unit
def test_cli_errors_translates_api_error_to_one_line(monkeypatch, fake_veeam):
    """A VeeamApiError from the ops layer becomes a red 'Error:' line + exit 1,
    not a traceback."""
    from veeam_aiops.cli import app

    class _Boom:
        def get(self, *a, **k):
            raise VeeamApiError("Resource not found (404) on /api/v1/jobs", status_code=404)

    monkeypatch.setattr(
        "veeam_aiops.cli.job.get_connection",
        lambda target=None, config_path=None: (_Boom(), None),
    )
    result = runner.invoke(app, ["job", "list"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "404" in result.output


@pytest.mark.unit
def test_cli_errors_prefixes_keyerror(monkeypatch):
    """KeyError is reworded to a 'Missing required key or environment variable'
    teaching message."""
    from veeam_aiops.cli import app

    class _Boom:
        def get(self, *a, **k):
            raise KeyError("VEEAM_LAB_PASSWORD")

    monkeypatch.setattr(
        "veeam_aiops.cli.repository.get_connection",
        lambda target=None, config_path=None: (_Boom(), None),
    )
    result = runner.invoke(app, ["repository", "list"])
    assert result.exit_code == 1
    assert "Missing required key or environment variable" in result.output
