"""Ranking at estate scale: scope, unmatched owners, concurrency, progress, timeouts.

Driven by the issue #2 rerun on VBR 13.1.1.18: a complete 123-backup ranking
took 21 minutes (local CPU 4 s — the time is the server), one ``/backupFiles``
page needed more than the 30 s default timeout, and exactly one 5 MiB file named
an owner its own backup did not list. The fake behaves like a correct server by
default; each misbehaviour is installed explicitly.
"""

from __future__ import annotations

import threading
import time

import pytest
from footprint_fixtures import GiB, RouteFake, bfile, ranking_routes

from veeam_aiops.connection import VeeamApiError
from veeam_aiops.ops import _backup_files as bf
from veeam_aiops.ops import ranking


def _rank(routes: dict, **kwargs) -> dict:
    kwargs.setdefault("limit", 10)
    return ranking.storage_ranking(RouteFake(routes), **kwargs)


def _scoped_routes() -> dict:
    routes = ranking_routes()
    routes["/api/v1/backups"] = [
        {"id": "b1", "name": "Daily", "repositoryId": "r1", "repositoryName": "REPO-A"},
        {"id": "b2", "name": "Copy", "repositoryId": "r2", "repositoryName": "DR-S3"},
    ]
    return routes


def _files_called(fake: RouteFake, backup: str) -> int:
    return len(fake.calls_to(f"/api/v1/backups/{backup}/backupFiles"))


# ─── (a) scope ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_scope_by_backup_id_reads_only_that_backup():
    fake = RouteFake(_scoped_routes())
    out = ranking.storage_ranking(fake, limit=10, backups=["b2"])
    assert [(o["name"], o["storedBytes"]) for o in out["objects"]] == [("VM01", 90 * GiB)]
    assert _files_called(fake, "b1") == 0, "an out-of-scope backup must not be read at all"
    assert out["backupsTotal"] == 2 and out["backupsInScope"] == 1
    assert out["backupsScanned"] == 1 and out["backupsTruncated"] is False
    assert out["scoped"] is True
    assert out["scope"] == {"backups": ["b2"], "repository": None}


@pytest.mark.unit
def test_scope_by_backup_name_is_case_insensitive():
    """A backup is named after its job, so this is also the per-job filter."""
    out = _rank(_scoped_routes(), backups=["daily"])
    assert {o["name"] for o in out["objects"]} == {"VM01", "VM02"}
    assert out["backupsInScope"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("repository", ["dr-s3", "r2"])
def test_scope_by_repository_name_or_id(repository):
    out = _rank(_scoped_routes(), repository=repository)
    assert [o["storedBytes"] for o in out["objects"]] == [90 * GiB]
    assert out["backupsInScope"] == 1


@pytest.mark.unit
def test_repository_name_is_looked_up_when_the_backup_row_omits_it():
    """Older revisions give only repositoryId on a backup."""
    routes = ranking_routes()
    routes["/api/v1/backups"] = [{"id": "b1", "name": "Daily", "repositoryId": "r1"},
                                 {"id": "b2", "name": "Copy", "repositoryId": "r2"}]
    routes["/api/v1/backupInfrastructure/repositories"] = [
        {"id": "r1", "name": "REPO-A"}, {"id": "r2", "name": "DR-S3"}]
    out = _rank(routes, repository="DR-S3")
    assert out["backupsInScope"] == 1 and out["objects"][0]["storedBytes"] == 90 * GiB


@pytest.mark.unit
@pytest.mark.parametrize("kwargs", [{"backups": ["nope"]}, {"repository": "nowhere"},
                                    {"backups": ["b1"], "repository": "DR-S3"}])
def test_a_scope_matching_nothing_is_an_error_not_an_empty_ranking(kwargs):
    """An empty ranking reads as "nothing consumes storage here" — a typo must not."""
    with pytest.raises(ValueError, match="matched no backup"):
        _rank(_scoped_routes(), **kwargs)


@pytest.mark.unit
def test_partial_is_measured_against_the_scope_not_the_server():
    out = _rank(_scoped_routes(), backups=["b1", "b2"], max_backups=1)
    assert out["backupsInScope"] == 2 and out["backupsScanned"] == 1
    assert out["backupsTruncated"] is True


@pytest.mark.unit
def test_an_unscoped_ranking_is_not_marked_scoped():
    out = _rank(_scoped_routes())
    assert out["scoped"] is False and out["backupsInScope"] == out["backupsTotal"] == 2


# ─── (b) owners missing from their own backup's listing ──────────────────────


@pytest.mark.unit
def test_an_owner_the_server_does_not_know_stays_unmatched_and_is_explained():
    """Default fixture: ghost.vbk names "zz", and /backupObjects/zz is a 404."""
    out = _rank(ranking_routes())
    assert out["unmatchedOwnerFiles"] == 1 and out["unmatchedOwnerStoredBytes"] == 5 * GiB
    assert out["recoveredOwnerFiles"] == 0
    [detail] = out["unmatchedOwners"]
    assert detail["ownerId"] == "zz" and detail["resolution"] == "notFound"
    assert detail["files"] == 1 and detail["storedBytes"] == 5 * GiB
    assert detail["backupNames"] == ["Daily"]
    assert any("do not match" in c for c in out["caveats"])


@pytest.mark.unit
def test_an_owner_moved_to_another_backup_is_charged_to_that_machine():
    """The issue #2 pattern: a file left behind when its VM changed jobs.

    Resolving the id through /backupObjects/{id} proves it IS a backup-object id,
    so the namespace alarm must not fire, and the bytes belong to that machine.
    """
    routes = ranking_routes()
    routes["/api/v1/backupObjects/zz"] = {"id": "zz", "name": "VM03", "platformName": "VMware",
                                          "path": "vc01/VM03", "backupId": "b9"}
    out = _rank(routes)
    vm03 = next(o for o in out["objects"] if o["name"] == "VM03")
    assert vm03["storedBytes"] == 5 * GiB and vm03["backups"] == ["Daily"]
    assert out["unmatchedOwnerFiles"] == 0
    assert out["recoveredOwnerFiles"] == 1 and out["recoveredOwnerStoredBytes"] == 5 * GiB
    [detail] = out["unmatchedOwners"]
    assert detail["resolution"] == "otherBackup" and detail["objectBackupId"] == "b9"
    assert detail["objectName"] == "VM03"
    assert not any("do not match" in c for c in out["caveats"])
    assert bf.RECOVERED_OWNER_CAVEAT in out["caveats"]
    assert out["unresolvedFiles"] == 0, "recovered bytes are charged, so not unresolved"


@pytest.mark.unit
def test_a_recovered_owner_merges_with_the_same_machine_elsewhere():
    routes = ranking_routes()
    routes["/api/v1/backupObjects/zz"] = {"id": "zz", "name": "VM01", "platformName": "VMware",
                                          "path": "vc01/VM01", "backupId": "b1"}
    out = _rank(routes)
    vm01 = next(o for o in out["objects"] if o["name"] == "VM01")
    assert vm01["storedBytes"] == 195 * GiB
    assert out["unmatchedOwners"][0]["resolution"] == "sameBackup"


@pytest.mark.unit
@pytest.mark.parametrize("answer", [
    VeeamApiError("Veeam API error (500) on /api/v1/backupObjects/zz.", status_code=500),
    {},                              # a body with no id is not an object
])
def test_a_failed_owner_lookup_is_not_read_as_an_answer(answer):
    routes = ranking_routes()
    routes["/api/v1/backupObjects/zz"] = answer
    out = _rank(routes)
    [detail] = out["unmatchedOwners"]
    assert detail["resolution"] == "lookupFailed" and detail["error"]
    assert out["unmatchedOwnerFiles"] == 1 and out["recoveredOwnerFiles"] == 0
    assert any("do not match" in c for c in out["caveats"])


@pytest.mark.unit
def test_each_distinct_owner_is_looked_up_once():
    routes = ranking_routes()
    routes["/api/v1/backups/b1/backupFiles"] = [
        *routes["/api/v1/backups/b1/backupFiles"],
        bfile("f6", "ghost-2.vib", ["zz"], 1 * GiB, 1, "t")]
    fake = RouteFake(routes)
    out = ranking.storage_ranking(fake, limit=10)
    assert len(fake.calls_to("/api/v1/backupObjects/zz")) == 1
    assert out["unmatchedOwners"][0]["files"] == 2
    assert out["unmatchedOwners"][0]["storedBytes"] == 6 * GiB


@pytest.mark.unit
def test_no_lookup_is_made_when_every_owner_matched():
    """Positive control: the lookup is for the residue, not a tax on every file."""
    routes = ranking_routes()
    routes["/api/v1/backups/b1/backupFiles"] = routes["/api/v1/backups/b1/backupFiles"][:3]
    fake = RouteFake(routes)
    out = ranking.storage_ranking(fake, limit=10)
    assert not [c for c in fake.calls if c[1].startswith("/api/v1/backupObjects/")]
    assert out["unmatchedOwners"] == [] and out["ownerLookupsSkipped"] == 0


@pytest.mark.unit
def test_lookups_are_capped_and_the_rest_are_reported_skipped(monkeypatch):
    monkeypatch.setattr(ranking, "MAX_OWNER_LOOKUPS", 1)
    routes = ranking_routes()
    routes["/api/v1/backups/b1/backupFiles"] = [
        *routes["/api/v1/backups/b1/backupFiles"],
        bfile("f7", "ghost-3.vbk", ["yy"], 2 * GiB, 1, "t")]
    out = _rank(routes)
    assert out["ownerLookupsSkipped"] == 1
    assert sorted(d["resolution"] for d in out["unmatchedOwners"]) == ["notFound", "skipped"]
    assert out["unmatchedOwnerFiles"] == 2


# ─── (c) bounded concurrency ─────────────────────────────────────────────────


@pytest.mark.unit
def test_concurrency_does_not_change_the_result():
    sequential = _rank(ranking_routes(), concurrency=1)
    parallel = _rank(ranking_routes(), concurrency=4)
    assert sequential == parallel


@pytest.mark.unit
def test_backups_are_actually_read_in_parallel():
    """Both backupFiles reads must be in flight at once, or the barrier breaks."""
    barrier = threading.Barrier(2, timeout=5)
    routes = ranking_routes()
    for backup in ("b1", "b2"):
        rows = routes[f"/api/v1/backups/{backup}/backupFiles"]

        def route(params, _headers, rows=rows):
            if params.get("skip", 0) == 0:
                barrier.wait()
            return rows
        routes[f"/api/v1/backups/{backup}/backupFiles"] = route
    out = _rank(routes, concurrency=2)
    assert [o["name"] for o in out["objects"]] == ["VM01", "VM02"]


@pytest.mark.unit
def test_unreadable_backups_keep_scan_order_whatever_finishes_first():
    routes = ranking_routes()

    def slow_failure(_params, _headers):
        time.sleep(0.2)
        raise VeeamApiError("Veeam API error (500) on b1.", status_code=500)
    routes["/api/v1/backups/b1/backupFiles"] = slow_failure
    routes["/api/v1/backups/b2/backupFiles"] = VeeamApiError("404 on b2.", status_code=404)
    out = _rank(routes, concurrency=2)
    assert [u["backupName"] for u in out["unreadableBackups"]] == ["Daily", "Copy"]


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, 9, True])
def test_concurrency_is_bounded(value):
    with pytest.raises(ValueError, match="concurrency"):
        _rank(ranking_routes(), concurrency=value)


# ─── (d) progress ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_progress_reports_every_scanned_backup_once():
    seen: list[tuple[int, int, str]] = []
    _rank(ranking_routes(), concurrency=2,
          progress=lambda done, total, label: seen.append((done, total, label)))
    assert [s[0] for s in seen] == [1, 2]
    assert {s[1] for s in seen} == {2}
    assert sorted(s[2] for s in seen) == ["Copy", "Daily"]


# ─── (e) timeouts ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_a_timed_out_backup_names_the_timeout_as_the_remedy():
    """Issue #2: one /backupFiles page exceeded 30 s; 300 s read all 123 backups."""
    routes = ranking_routes()
    routes["/api/v1/backups/b2/backupFiles"] = VeeamApiError(
        "GET /api/v1/backups/b2/backupFiles timed out after 30s.", timed_out=True)
    out = _rank(routes)
    [bad] = out["unreadableBackups"]
    assert bad["timedOut"] is True
    assert bf.TIMEOUT_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_an_unreadable_backup_that_did_not_time_out_gets_no_timeout_advice():
    """Positive control: raising a timeout does not fix a 404."""
    routes = ranking_routes()
    routes["/api/v1/backups/b2/backupFiles"] = VeeamApiError("404 on b2.", status_code=404)
    out = _rank(routes)
    assert out["unreadableBackups"][0]["timedOut"] is False
    assert bf.TIMEOUT_CAVEAT not in out["caveats"]


# ─── review follow-ups ───────────────────────────────────────────────────────


def _ghost_routes(owners: dict[str, int]) -> dict:
    """b1 holds one resolvable file plus one file per unknown owner (all 404)."""
    routes = ranking_routes()
    routes.pop("/api/v1/backupObjects/zz")
    routes["/api/v1/backups/b1/backupFiles"] = [
        bfile("f1", "VM01.vbk", ["o1"], 100 * GiB, 1, "t"),
        *[bfile(f"g-{oid}", f"{oid}.vbk", [oid], size, 1, "t") for oid, size in owners.items()]]
    for oid in owners:
        routes[f"/api/v1/backupObjects/{oid}"] = VeeamApiError("404", status_code=404)
    return routes


@pytest.mark.unit
def test_owner_lookups_go_largest_first_and_details_are_capped(monkeypatch):
    """A namespace mismatch makes every file an unknown owner; the payload must stay bounded
    and the lookup budget must go to the ids that carry the most bytes."""
    monkeypatch.setattr(ranking, "MAX_OWNER_LOOKUPS", 1)
    monkeypatch.setattr(ranking, "MAX_OWNER_DETAILS", 2)
    fake = RouteFake(_ghost_routes({"g1": 1 * GiB, "g2": 3 * GiB, "g3": 2 * GiB}))
    out = ranking.storage_ranking(fake, limit=10)
    assert len(fake.calls_to("/api/v1/backupObjects/g2")) == 1
    assert not fake.calls_to("/api/v1/backupObjects/g1")
    assert not fake.calls_to("/api/v1/backupObjects/g3")
    assert [d["ownerId"] for d in out["unmatchedOwners"]] == ["g2", "g3"]
    assert out["unmatchedOwnersTotal"] == 3 and out["unmatchedOwnersTruncated"] is True
    assert out["ownerLookupsSkipped"] == 2
    assert out["unmatchedOwnerStoredBytes"] == 6 * GiB


@pytest.mark.unit
def test_owners_never_looked_up_do_not_raise_the_namespace_alarm(monkeypatch):
    """Skipped ids were not checked; saying the server "could not resolve" them is false."""
    monkeypatch.setattr(ranking, "MAX_OWNER_LOOKUPS", 0)
    out = _rank(_ghost_routes({"g1": 1 * GiB}))
    assert out["unmatchedOwnerFiles"] == 1
    assert bf.UNMATCHED_OWNER_CAVEAT not in out["caveats"]
    assert bf.LOOKUP_SKIPPED_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_a_failing_read_does_not_wait_for_the_slow_ones():
    """One worker's hard error must surface now, not after every other read finishes."""
    routes = ranking_routes()

    def slow(_params, _headers):
        time.sleep(3)
        return []
    routes["/api/v1/backups/b1/backupFiles"] = slow
    routes["/api/v1/backups/b2/backupFiles"] = lambda _p, _h: (_ for _ in ()).throw(
        ValueError("boom"))
    started = time.monotonic()
    with pytest.raises(ValueError, match="boom"):
        _rank(routes, concurrency=2)
    assert time.monotonic() - started < 2.0


@pytest.mark.unit
def test_reads_are_sequential_unless_asked():
    """Parallel reads are unmeasured on a real VBR; the one live estate already needed
    a 300 s budget sequentially, so parallelism is opt-in."""
    import inspect

    assert ranking.DEFAULT_CONCURRENCY == 1
    assert inspect.signature(ranking.storage_ranking).parameters["concurrency"].default == 1
    assert "concurrency" in bf.TIMEOUT_CAVEAT


@pytest.mark.unit
def test_scale_out_repository_names_are_matched():
    routes = ranking_routes()
    routes["/api/v1/backups"] = [{"id": "b1", "name": "Daily", "repositoryId": "s1"},
                                 {"id": "b2", "name": "Copy", "repositoryId": "r2"}]
    routes["/api/v1/backupInfrastructure/repositories"] = [{"id": "r2", "name": "DR-S3"}]
    routes["/api/v1/backupInfrastructure/scaleOutRepositories"] = [
        {"id": "s1", "name": "SOBR-Main"}]
    out = _rank(routes, repository="sobr-main")
    assert out["backupsInScope"] == 1 and {o["name"] for o in out["objects"]} == {"VM01", "VM02"}


@pytest.mark.unit
def test_a_backup_the_server_lists_without_an_id_is_reported():
    routes = ranking_routes()
    routes["/api/v1/backups"] = [{"name": "NoId"}, {"id": "b2", "name": "Copy"}]
    out = _rank(routes, backups=["NoId"])
    assert out["objects"] == []
    [bad] = out["unreadableBackups"]
    assert bad["backupName"] == "NoId" and "without an id" in bad["error"]


@pytest.mark.unit
def test_a_broken_progress_callback_does_not_discard_the_scan():
    def broken(*_args):
        raise BrokenPipeError("stderr closed")
    out = _rank(ranking_routes(), concurrency=2, progress=broken)
    assert [o["name"] for o in out["objects"]] == ["VM01", "VM02"]


@pytest.mark.unit
def test_the_payload_is_independent_of_completion_order():
    import random

    rng = random.Random(7)
    routes: dict = {"/api/v1/backups": [{"id": f"b{i}", "name": f"Job{i}"} for i in range(10)]}
    for i in range(10):
        routes[f"/api/v1/backups/b{i}/objects"] = [
            {"id": f"o{i}", "name": f"VM{i % 4}", "platformName": "VMware",
             "path": f"vc/VM{i % 4}"}]
        rows = [bfile(f"f{i}", "x.vbk", [f"o{i}"], (i + 1) * GiB, 1, "t"),
                bfile(f"c{i}", "chain.vbk", None, GiB, 1, "t"),
                bfile(f"u{i}", "left.vbk", [f"ghost{i % 3}"], GiB, 1, "t")]
        delay = rng.uniform(0, 0.05)

        def route(_params, _headers, rows=rows, delay=delay):
            time.sleep(delay)
            return rows
        routes[f"/api/v1/backups/b{i}/backupFiles"] = route
    for g in range(3):
        routes[f"/api/v1/backupObjects/ghost{g}"] = VeeamApiError("404", status_code=404)
    assert _rank(routes, concurrency=1) == _rank(routes, concurrency=8)


@pytest.mark.unit
def test_a_long_unreadable_error_says_it_was_cut():
    routes = ranking_routes()
    routes["/api/v1/backups/b2/backupFiles"] = VeeamApiError("x" * 500, status_code=500)
    [bad] = _rank(routes)["unreadableBackups"]
    assert bad["error"].endswith("…") and len(bad["error"]) <= 201
