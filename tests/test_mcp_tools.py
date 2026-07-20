"""Functional coverage for ALL 21 governed MCP tools (previously 1 of 21).

Every tool is invoked through a mocked connection (``FakeVeeam`` in conftest —
records each outbound call, answers canned JSON per path-substring) and the
tests assert the real API contract: the endpoint path + params that leave the
tool. Writes are additionally checked for dry-run behavior (no API call, a
``dryRun`` preview, no undo recorded), risk tier, and — where undo exists —
that the inverse descriptor / prior state is captured, not guessed.
"""

from __future__ import annotations

import pytest

import veeam_aiops.governance.audit as audit_mod
import veeam_aiops.governance.policy as policy_mod
import veeam_aiops.governance.undo as undo_mod
from mcp_server.tools import backups as backups_tools
from mcp_server.tools import infrastructure as infra_tools
from mcp_server.tools import jobs as jobs_tools
from mcp_server.tools import overview as overview_tools
from mcp_server.tools import repositories as repo_tools
from mcp_server.tools import restore as restore_tools
from mcp_server.tools import sessions as sessions_tools


@pytest.fixture(autouse=True)
def _gov_home(tmp_path, monkeypatch):
    """Isolate harness state (audit/undo/rules) in a throwaway home."""
    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


@pytest.fixture
def undo_recorder(monkeypatch):
    """Capture undo descriptors the harness records."""
    recorded: list[dict] = []

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded.append(undo_descriptor)
            return f"undo-{len(recorded)}"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())
    return recorded


def _wire(monkeypatch, module, fake) -> None:
    monkeypatch.setattr(module, "_get_connection", lambda target=None: fake)


# ─── risk tiers of the write tools ───────────────────────────────────────────


@pytest.mark.unit
def test_write_tool_risk_tiers():
    expected = {
        jobs_tools.job_start: "medium",
        jobs_tools.job_stop: "medium",
        jobs_tools.job_retry: "medium",
        jobs_tools.job_enable: "medium",
        jobs_tools.job_disable: "medium",
        sessions_tools.session_stop: "medium",
        restore_tools.start_vm_restore: "high",  # overwrites/creates a VM, no undo
    }
    for tool, risk in expected.items():
        assert tool._is_governed_tool is True, tool.__name__
        assert tool._risk_level == risk, tool.__name__


# ─── overview ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_overview_fans_out_and_summarizes(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/api/v1/jobs": {
                "data": [
                    {"id": "j1", "name": "Daily", "lastResult": "Success"},
                    {"id": "j2", "name": "Weekly", "lastResult": "Failed", "isDisabled": True},
                ]
            },
            "repositories/states": {
                "data": [{"id": "r1", "name": "Main", "capacityGB": 100, "freeGB": 5}]
            },
            "/api/v1/sessions": {
                "data": [{"id": "s1", "name": "Daily run", "state": "Working"}]
            },
        }
    )
    _wire(monkeypatch, overview_tools, fake)
    out = overview_tools.overview()
    assert out["jobs"]["total"] == 2
    assert out["jobs"]["byLastResult"] == {"Success": 1, "Failed": 1}
    assert out["jobs"]["disabled"] == 1
    assert out["repositories"]["nearFull"] == [{"name": "Main", "usedPercent": 95.0}]
    assert out["sessions"]["running"] == [{"id": "s1", "name": "Daily run"}]
    assert set(fake.paths("GET")) == {
        "/api/v1/jobs",
        "/api/v1/backupInfrastructure/repositories/states",
        "/api/v1/sessions",
    }


# ─── jobs: reads ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_job_list_hits_jobs_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/api/v1/jobs": {
                "data": [{"id": "j1", "name": "Daily", "type": "Backup",
                          "status": "inactive", "lastResult": "Success"}]
            }
        }
    )
    _wire(monkeypatch, jobs_tools, fake)
    rows = jobs_tools.job_list()
    assert fake.paths("GET") == ["/api/v1/jobs"]
    assert rows[0]["id"] == "j1" and rows[0]["lastResult"] == "Success"


@pytest.mark.unit
def test_job_get_hits_job_detail_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/api/v1/jobs/j1": {
                "id": "j1", "name": "Daily", "description": "SQL nightly",
                "schedule": {"runAutomatically": True},
            }
        }
    )
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_get(job_id="j1")
    assert fake.paths("GET") == ["/api/v1/jobs/j1"]
    assert out["description"] == "SQL nightly"
    assert out["scheduleEnabled"] is True


@pytest.mark.unit
def test_job_get_url_encodes_hostile_id(monkeypatch, fake_veeam):
    """A traversal-shaped id must never appear raw in the request path."""
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    jobs_tools.job_get(job_id="../restore/vm")
    (path,) = fake.paths("GET")
    assert "../" not in path
    assert path == "/api/v1/jobs/..%2Frestore%2Fvm"


# ─── jobs: writes (dry-run / real POST / undo inverse / prior state) ─────────


@pytest.mark.unit
def test_job_start_dry_run_makes_no_api_call(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_start(job_id="j1", dry_run=True)
    assert out["dryRun"] is True and out["wouldStart"]["job_id"] == "j1"
    assert fake.calls == []
    assert undo_recorder == []  # a preview must not record an undo


@pytest.mark.unit
def test_job_start_posts_and_records_stop_inverse(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam(
        responses={"/api/v1/jobs/j1": {"status": "inactive", "lastResult": "Failed"}}
    )
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_start(job_id="j1")
    assert "error" not in out
    assert fake.paths("POST") == ["/api/v1/jobs/j1/start"]
    # Prior state captured from the API before the POST, not guessed.
    assert out["priorState"] == {"status": "inactive", "lastResult": "Failed"}
    assert fake.paths("GET") == ["/api/v1/jobs/j1"]
    assert undo_recorder[0]["tool"] == "job_stop"
    assert undo_recorder[0]["params"] == {"job_id": "j1"}
    assert out.get("_undo_id") == "undo-1"


@pytest.mark.unit
def test_job_stop_dry_run_makes_no_api_call(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_stop(job_id="j1", dry_run=True)
    assert out["dryRun"] is True and out["wouldStop"]["job_id"] == "j1"
    assert fake.calls == []
    assert undo_recorder == []


@pytest.mark.unit
def test_job_stop_posts_and_records_start_inverse(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam(
        responses={"/api/v1/jobs/j1": {"status": "Working", "lastResult": "None"}}
    )
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_stop(job_id="j1")
    assert "error" not in out
    assert fake.paths("POST") == ["/api/v1/jobs/j1/stop"]
    assert out["priorState"] == {"status": "Working", "lastResult": "None"}
    assert undo_recorder[0]["tool"] == "job_start"
    assert undo_recorder[0]["params"] == {"job_id": "j1"}


@pytest.mark.unit
def test_job_retry_posts_and_records_stop_inverse(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam(
        responses={"/api/v1/jobs/j1": {"status": "inactive", "lastResult": "Failed"}}
    )
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_retry(job_id="j1")
    assert "error" not in out
    assert fake.paths("POST") == ["/api/v1/jobs/j1/retry"]
    assert out["priorState"]["lastResult"] == "Failed"
    assert undo_recorder[0]["tool"] == "job_stop"


@pytest.mark.unit
def test_job_retry_dry_run_makes_no_api_call(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_retry(job_id="j1", dry_run=True)
    assert out["dryRun"] is True and out["wouldRetry"]["job_id"] == "j1"
    assert fake.calls == [] and undo_recorder == []


@pytest.mark.unit
def test_job_enable_posts_and_records_disable_inverse(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_enable(job_id="j1")
    assert "error" not in out
    assert fake.paths("POST") == ["/api/v1/jobs/j1/enable"]
    assert undo_recorder[0]["tool"] == "job_disable"
    assert undo_recorder[0]["params"] == {"job_id": "j1"}


@pytest.mark.unit
def test_job_enable_dry_run_makes_no_api_call(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_enable(job_id="j1", dry_run=True)
    assert out["dryRun"] is True and out["wouldEnable"]["job_id"] == "j1"
    assert fake.calls == [] and undo_recorder == []


@pytest.mark.unit
def test_job_disable_posts_and_records_enable_inverse(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_disable(job_id="j1")
    assert "error" not in out
    assert fake.paths("POST") == ["/api/v1/jobs/j1/disable"]
    assert undo_recorder[0]["tool"] == "job_enable"
    assert undo_recorder[0]["params"] == {"job_id": "j1"}


@pytest.mark.unit
def test_job_disable_dry_run_makes_no_api_call(monkeypatch, fake_veeam, undo_recorder):
    fake = fake_veeam()
    _wire(monkeypatch, jobs_tools, fake)
    out = jobs_tools.job_disable(job_id="j1", dry_run=True)
    assert out["dryRun"] is True and out["wouldDisable"]["job_id"] == "j1"
    assert fake.calls == [] and undo_recorder == []


# ─── sessions ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_session_list_hits_sessions_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/api/v1/sessions": {
                "data": [{"id": "s1", "name": "run", "sessionType": "BackupJob",
                          "state": "Working", "result": {"result": "None"}}]
            }
        }
    )
    _wire(monkeypatch, sessions_tools, fake)
    rows = sessions_tools.session_list()
    assert fake.paths("GET") == ["/api/v1/sessions"]
    assert rows[0]["id"] == "s1" and rows[0]["state"] == "Working"


@pytest.mark.unit
def test_session_get_hits_session_detail(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/api/v1/sessions/s1": {
                "id": "s1", "name": "run", "state": "Working",
                "progressPercent": 42, "creationTime": "2026-07-13T00:00:00Z",
            }
        }
    )
    _wire(monkeypatch, sessions_tools, fake)
    out = sessions_tools.session_get(session_id="s1")
    assert fake.paths("GET") == ["/api/v1/sessions/s1"]
    assert out["progressPercent"] == 42


@pytest.mark.unit
def test_session_log_hits_logs_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/logs": {"data": [{"title": "Queued for processing", "status": "Succeeded",
                                "startTime": "t0", "endTime": "t1"}]}
        }
    )
    _wire(monkeypatch, sessions_tools, fake)
    rows = sessions_tools.session_log(session_id="s1")
    assert fake.paths("GET") == ["/api/v1/sessions/s1/logs"]
    assert rows[0]["title"] == "Queued for processing"


@pytest.mark.unit
def test_session_stop_dry_run_and_real_post(monkeypatch, fake_veeam):
    fake = fake_veeam()
    _wire(monkeypatch, sessions_tools, fake)
    preview = sessions_tools.session_stop(session_id="s1", dry_run=True)
    assert preview["dryRun"] is True and preview["wouldStopSession"]["session_id"] == "s1"
    assert fake.calls == []

    out = sessions_tools.session_stop(session_id="s1")
    assert "error" not in out
    assert fake.paths("POST") == ["/api/v1/sessions/s1/stop"]


@pytest.mark.unit
def test_session_stop_url_encodes_hostile_id(monkeypatch, fake_veeam):
    fake = fake_veeam()
    _wire(monkeypatch, sessions_tools, fake)
    sessions_tools.session_stop(session_id="../jobs/j1")
    (path,) = fake.paths("POST")
    assert "../" not in path
    assert path == "/api/v1/sessions/..%2Fjobs%2Fj1/stop"


# ─── backups ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_backup_list_hits_backups_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/api/v1/backups": {
                "data": [{"id": "b1", "name": "Daily", "jobType": "Backup",
                          "creationTime": "2026-07-01T00:00:00Z"}]
            }
        }
    )
    _wire(monkeypatch, backups_tools, fake)
    rows = backups_tools.backup_list()
    assert fake.paths("GET") == ["/api/v1/backups"]
    assert rows[0]["id"] == "b1" and rows[0]["type"] == "Backup"


@pytest.mark.unit
def test_backup_object_list_hits_objects_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/objects": {"data": [{"id": "o1", "name": "sql-01", "type": "VM",
                                   "objectId": "vm-42"}]}
        }
    )
    _wire(monkeypatch, backups_tools, fake)
    rows = backups_tools.backup_object_list(backup_id="b1")
    assert fake.paths("GET") == ["/api/v1/backups/b1/objects"]
    assert rows[0]["objectId"] == "vm-42"


# ─── repositories ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_repository_list_hits_repositories_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/repositories": {
                "data": [{"id": "r1", "name": "Main", "type": "WinLocal", "path": "D:\\\\bk"}]
            }
        }
    )
    _wire(monkeypatch, repo_tools, fake)
    rows = repo_tools.repository_list()
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/repositories"]
    assert rows[0]["name"] == "Main"


@pytest.mark.unit
def test_repository_get_merges_capacity_state(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/repositories/states": {
                "data": [{"id": "r1", "capacityGB": 200, "freeGB": 50, "usedSpaceGB": 150}]
            },
            "/repositories/r1": {"id": "r1", "name": "Main", "type": "WinLocal",
                                 "path": "D:\\\\bk", "description": "primary"},
        }
    )
    _wire(monkeypatch, repo_tools, fake)
    out = repo_tools.repository_get(repository_id="r1")
    assert fake.paths("GET") == [
        "/api/v1/backupInfrastructure/repositories/r1",
        "/api/v1/backupInfrastructure/repositories/states",
    ]
    assert out["name"] == "Main"
    assert out["capacity"] == 200 and out["free"] == 50
    assert out["usedPercent"] == 75.0


@pytest.mark.unit
def test_repository_state_hits_states_endpoint(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/repositories/states": {
                "data": [{"id": "r1", "name": "Main", "type": "WinLocal",
                          "capacityGB": 100, "freeGB": 10}]
            }
        }
    )
    _wire(monkeypatch, repo_tools, fake)
    rows = repo_tools.repository_state()
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/repositories/states"]
    assert rows[0]["usedPercent"] == 90.0


# ─── restore ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_restore_list_points_passes_backup_filter_as_query_param(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/api/v1/restorePoints": {
                "data": [{"id": "rp1", "name": "sql-01", "creationTime": "t",
                          "platformName": "VMware"}]
            }
        }
    )
    _wire(monkeypatch, restore_tools, fake)
    rows = restore_tools.restore_list_points(backup_id="b1")
    method, path, kwargs = fake.calls[0]
    assert (method, path) == ("GET", "/api/v1/restorePoints")
    # Filter travels as an httpx query param (httpx handles its encoding).
    assert kwargs["params"] == {"backupIdFilter": "b1"}
    assert rows[0]["id"] == "rp1"


@pytest.mark.unit
def test_start_vm_restore_dry_run_makes_no_write_call(monkeypatch, fake_veeam, undo_recorder):
    """The preview reads (to name the VM) but must never write."""
    fake = fake_veeam()
    _wire(monkeypatch, restore_tools, fake)
    out = restore_tools.start_vm_restore(restore_point_id="rp1", dry_run=True)
    assert out["dryRun"] is True and out["wouldRestore"]["restore_point_id"] == "rp1"
    assert fake.paths("POST") == []
    assert undo_recorder == []


@pytest.mark.unit
def test_start_vm_restore_posts_restore_point_and_records_no_undo(
    monkeypatch, fake_veeam, undo_recorder
):
    fake = fake_veeam()
    _wire(monkeypatch, restore_tools, fake)
    out = restore_tools.start_vm_restore(restore_point_id="rp1")
    assert "error" not in out
    method, path, kwargs = [c for c in fake.calls if c[0] == "POST"][0]
    assert (method, path) == ("POST", "/api/v1/restore/vm")
    assert kwargs["json"] == {"restorePointId": "rp1"}
    assert out["action"] == "vm_restore_started"
    assert undo_recorder == []  # irreversible: must record no undo


# ─── infrastructure ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_managed_server_list_hits_managed_servers(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/managedServers": {
                "data": [{"id": "m1", "name": "esx-01", "type": "ViHost",
                          "description": "prod host"}]
            }
        }
    )
    _wire(monkeypatch, infra_tools, fake)
    rows = infra_tools.managed_server_list()
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/managedServers"]
    assert rows[0]["name"] == "esx-01"


@pytest.mark.unit
def test_proxy_list_hits_proxies(monkeypatch, fake_veeam):
    fake = fake_veeam(
        responses={
            "/proxies": {
                "data": [{"id": "p1", "name": "proxy-01", "type": "Vi",
                          "server": {"hostName": "px.local"}}]
            }
        }
    )
    _wire(monkeypatch, infra_tools, fake)
    rows = infra_tools.proxy_list()
    assert fake.paths("GET") == ["/api/v1/backupInfrastructure/proxies"]
    assert rows[0]["server"] == "px.local"
