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


# ─── review round: guards need an accepting side, order must really be applied ──


def _sorting_sessions(rows: list[dict]):
    """A /sessions route that applies orderColumn/orderAsc and stateFilter the way
    the server does, so newest-first behaviour is exercised, not just requested."""
    def route(params: dict, _headers: dict) -> list[dict]:
        out = list(rows)
        if params.get("stateFilter"):
            out = [r for r in out if r.get("state") == params["stateFilter"]]
        if params.get("createdAfterFilter"):
            out = [r for r in out if r["creationTime"] > params["createdAfterFilter"]]
        if params.get("orderColumn") == "CreationTime":
            out.sort(key=lambda r: r["creationTime"], reverse=not params.get("orderAsc", True))
        return out
    return route


def _estate_with_a_long_running_job() -> list[dict]:
    """449 finished sessions, plus one started before all of them and still running."""
    rows = [{"id": f"s{i}", "name": f"job-{i}", "state": "Stopped",
             "result": {"result": "Success"},
             "creationTime": f"2026-09-10T{i // 60:02d}:{i % 60:02d}:00Z"} for i in range(1, N)]
    return [{"id": "copy", "name": "backup-copy", "state": "Working", "result": None,
             "creationTime": "2026-09-01T00:00:00Z"}, *rows]


@pytest.mark.unit
def test_restore_points_accept_rows_that_match_the_backup_filter():
    """Review finding 1: only the refusal side was tested, so a flipped comparison
    that refused every correct query still passed the whole suite."""
    out = restore.list_restore_points(_fake({"/api/v1/restorePoints": _points(3)}), backup_id="b1")
    assert out["returned"] == 3


@pytest.mark.unit
def test_restore_points_backup_filter_ignores_uuid_case():
    rows = _points(2, backup="0f3c9a1e-aaaa-bbbb-cccc-1234567890ab")
    out = restore.list_restore_points(_fake({"/api/v1/restorePoints": rows}),
                                      backup_id="0F3C9A1E-AAAA-BBBB-CCCC-1234567890AB")
    assert out["returned"] == 2


@pytest.mark.unit
def test_overview_counts_a_long_running_job_outside_the_recent_window():
    """Review finding 2: 'running' came from the newest-100 window, so a job
    started long ago and still running read as nothing running."""
    fake = _fake({"/api/v1/sessions": _sorting_sessions(_estate_with_a_long_running_job()),
                  "/api/v1/jobs": [], "/api/v1/backupInfrastructure/repositories/states": []})
    s = overview.health_overview(fake)["sessions"]
    assert s["truncated"] is True
    assert [r["id"] for r in s["running"]] == ["copy"]
    assert s["running"][0]["state"] == "Working"
    assert s["stateQueryErrors"] == []


@pytest.mark.unit
def test_overview_reports_a_state_query_it_could_not_make():
    def route(params, _headers):
        if params.get("stateFilter") == "Postprocessing":
            raise RuntimeError("400 invalid stateFilter")
        return _sorting_sessions(_estate_with_a_long_running_job())(params, _headers)

    fake = _fake({"/api/v1/sessions": route, "/api/v1/jobs": [],
                  "/api/v1/backupInfrastructure/repositories/states": []})
    s = overview.health_overview(fake)["sessions"]
    assert [e["state"] for e in s["stateQueryErrors"]] == ["Postprocessing"]
    assert [r["id"] for r in s["running"]] == ["copy"]


@pytest.mark.unit
def test_sessions_can_be_windowed_by_time():
    """Review finding 3: 'last night's failures' should be a time window, not
    whatever fits in the newest 100 sessions of every type."""
    fake = _fake({"/api/v1/sessions": _sorting_sessions(_estate_with_a_long_running_job())})
    out = sessions.list_sessions(fake, limit=1000, since_hours=24)
    sent = fake.calls_to("/api/v1/sessions")[0][2]["createdAfterFilter"]
    assert sent.endswith("Z") and "T" in sent
    assert out["since"] == sent


@pytest.mark.unit
def test_fetch_first_budget_message_states_what_happened():
    items = _rows("x", 50)
    fake = _fake({"/x": lambda params, _h: page(items, {**params, "limit": 1})})
    with pytest.raises(_paging.IncompleteCollection, match=r"3 pages .*3 of 50"):
        _paging.fetch_first(fake, "/x", 10, max_pages=3)
