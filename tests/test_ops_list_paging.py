"""List reads page past the server's first page, or say plainly that they stopped.

From REST revision 1.3 the VBR server applies ``limit=200`` when the caller
sends none (Veeam's published OpenAPI spec), so a read that takes only the
first response silently drops item 201 onward. The route fake serves list
routes exactly like that: without an explicit ``limit`` it hands back 200.

Two kinds of read:
  * **whole collections** (backups, objects, jobs, repositories, repository
    states, infrastructure) are read to the end — they are inventories, and a
    missing repository is a capacity check that never ran;
  * **histories that can be huge** (restore points, sessions) return the
    line's envelope — newest first, ``limit`` items, and ``truncated`` measured
    by asking for one more.
"""

from __future__ import annotations

import pytest
from footprint_fixtures import RouteFake, page

from veeam_aiops.ops import (
    _paging,
    backups,
    infrastructure,
    jobs,
    overview,
    repositories,
    restore,
    sessions,
)

N = 450  # more than two default pages


def _rows(prefix: str, n: int = N, **extra) -> list[dict]:
    return [{"id": f"{prefix}{i}", "name": f"{prefix}-{i}", **extra} for i in range(n)]


def _fake(routes: dict) -> RouteFake:
    return RouteFake(routes, build=None)


# ─── whole collections ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(("path", "call"), [
    ("/api/v1/backups", backups.list_backups),
    ("/api/v1/jobs", jobs.list_jobs),
    ("/api/v1/backupInfrastructure/repositories", repositories.list_repositories),
    ("/api/v1/backupInfrastructure/repositories/states", repositories.repository_state),
    ("/api/v1/backupInfrastructure/managedServers", infrastructure.list_managed_servers),
    ("/api/v1/backupInfrastructure/proxies", infrastructure.list_proxies),
])
def test_whole_collection_reads_past_the_first_page(path, call):
    fake = _fake({path: _rows("x")})
    assert len(call(fake)) == N


@pytest.mark.unit
def test_backup_objects_page_only_once_the_server_shows_it_is_paging():
    """The pinned revision (1.1-rev1) declares no skip/limit on this endpoint,
    so the first request sends none; paging parameters follow only when the
    server's own pagination block says there is more."""
    fake = _fake({"/api/v1/backups/b1/objects": _rows("o")})
    assert len(backups.list_backup_objects(fake, "b1")) == N
    calls = fake.calls_to("/api/v1/backups/b1/objects")
    assert "skip" not in calls[0][2] and "limit" not in calls[0][2]
    assert [c[2].get("skip") for c in calls[1:]] == [200, 400]


@pytest.mark.unit
def test_backup_objects_single_page_server_is_read_once():
    fake = _fake({"/api/v1/backups/b1/objects": {"data": _rows("o", 3)}})
    assert len(backups.list_backup_objects(fake, "b1")) == 3
    assert len(fake.calls_to("/api/v1/backups/b1/objects")) == 1


@pytest.mark.unit
def test_repository_detail_finds_a_state_row_past_the_first_page():
    rows = [{"id": f"r{i}", "name": f"repo-{i}", "capacityGB": 1000, "freeGB": 100}
            for i in range(N)]
    fake = _fake({"/api/v1/backupInfrastructure/repositories/r300": {"id": "r300", "name": "x"},
                  "/api/v1/backupInfrastructure/repositories/states": rows})
    assert repositories.get_repository(fake, "r300")["capacity"] == 1000


@pytest.mark.unit
def test_capacity_rca_input_includes_every_repository():
    rows = [{"id": f"r{i}", "name": f"repo-{i}", "capacityGB": 1000,
             "freeGB": 50 if i == 350 else 900} for i in range(N)]
    fake = _fake({"/api/v1/backupInfrastructure/repositories/states": rows})
    state = repositories.repository_state(fake)
    assert any(r["name"] == "repo-350" and r["usedPercent"] == 95.0 for r in state)


# ─── histories: envelope, newest first ───────────────────────────────────────


def _points(n: int = N, backup: str = "b1") -> list[dict]:
    return [{"id": f"rp{i}", "name": "VM01", "backupId": backup, "platformName": "VMware",
             "creationTime": f"2026-09-01T00:00:{i:03d}Z"} for i in range(n)]


@pytest.mark.unit
def test_restore_points_envelope_measures_truncation():
    fake = _fake({"/api/v1/restorePoints": _points()})
    out = restore.list_restore_points(fake, limit=100)
    assert out["returned"] == 100 and out["limit"] == 100 and out["truncated"] is True
    assert len(out["restorePoints"]) == 100
    first = fake.calls_to("/api/v1/restorePoints")[0][2]
    assert first["orderColumn"] == "CreationTime" and first["orderAsc"] is False
    assert first["limit"] == 101  # one more than asked, to measure truncation


@pytest.mark.unit
def test_restore_points_limit_above_a_page_is_paged_not_capped():
    fake = _fake({"/api/v1/restorePoints": _points(250)})
    out = restore.list_restore_points(fake, limit=300)
    assert out["returned"] == 250 and out["truncated"] is False


@pytest.mark.unit
def test_restore_points_exact_fit_is_not_truncated():
    out = restore.list_restore_points(_fake({"/api/v1/restorePoints": _points(100)}), limit=100)
    assert out["returned"] == 100 and out["truncated"] is False


@pytest.mark.unit
def test_restore_points_refuse_a_server_that_ignores_the_backup_filter():
    rows = [*_points(3, backup="b1"), *_points(2, backup="b2")]
    with pytest.raises(ValueError, match="backupIdFilter"):
        restore.list_restore_points(_fake({"/api/v1/restorePoints": rows}), backup_id="b1")


def _sessions(n: int = N) -> list[dict]:
    return [{"id": f"s{i}", "name": f"job-{i}", "sessionType": "BackupJob",
             "state": "Working" if i == 0 else "Stopped", "result": {"result": "Success"},
             "creationTime": f"2026-09-01T00:00:{i:03d}Z"} for i in range(n)]


@pytest.mark.unit
def test_sessions_envelope_is_newest_first_and_measured():
    fake = _fake({"/api/v1/sessions": _sessions()})
    out = sessions.list_sessions(fake, limit=50)
    assert out["returned"] == 50 and out["truncated"] is True
    params = fake.calls_to("/api/v1/sessions")[0][2]
    assert params["orderColumn"] == "CreationTime" and params["orderAsc"] is False


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1, 1001, True])
def test_history_limits_are_bounded(bad):
    with pytest.raises(ValueError, match="limit"):
        sessions.list_sessions(_fake({"/api/v1/sessions": _sessions(3)}), limit=bad)


@pytest.mark.unit
def test_overview_states_its_session_window():
    fake = _fake({"/api/v1/sessions": _sessions(), "/api/v1/jobs": _rows("j"),
                  "/api/v1/backupInfrastructure/repositories/states": _rows("r")})
    out = overview.health_overview(fake)
    assert out["jobs"]["total"] == N
    assert out["repositories"]["total"] == N
    s = out["sessions"]
    assert s["recent"] == 100 and s["truncated"] is True and len(s["running"]) == 1


# ─── fetch_first ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fetch_first_reads_no_further_than_it_needs():
    fake = _fake({"/x": _rows("x")})
    got = _paging.fetch_first(fake, "/x", 5)
    assert len(got) == 5 and len(fake.calls_to("/x")) == 1


@pytest.mark.unit
def test_fetch_first_pages_a_server_that_caps_its_pages():
    items = _rows("x", 300)
    fake = _fake({"/x": lambda params, _h: page(items, {**params,
                                                       "limit": min(int(params["limit"]), 100)})})
    got = _paging.fetch_first(fake, "/x", 250)
    assert [i["id"] for i in got] == [f"x{i}" for i in range(250)]


@pytest.mark.unit
def test_fetch_first_refuses_a_server_that_ignores_skip():
    items = _rows("x", 300)
    fake = _fake({"/x": lambda params, _h: page(items, {**params, "skip": 0, "limit": 100})})
    with pytest.raises(_paging.IncompleteCollection, match="does not honour skip"):
        _paging.fetch_first(fake, "/x", 250)
