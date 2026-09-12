"""Backup storage footprint: paging, revision negotiation, attribution.

Fixtures follow Veeam's published OpenAPI shapes rather than an idealised one:
revision 1.2-rev0 reports a single ``objectId`` per backup file, revision
1.3-rev2 reports ``objectIds`` (one file can hold several machines), sizes are
int64. The route fake behaves like a correct server by default; every way a
real one could misbehave (capped pages, ignored ``skip``, ignored filter) has
its own test instead of being assumed away.
"""

from __future__ import annotations

import pytest
from footprint_fixtures import (
    PER_MACHINE_FILES,
    VM01_POINTS,
    GiB,
    RouteFake,
    bfile,
    page,
    ranking_routes,
    restore_points_by_object,
    rp,
    vm01_routes,
)

from veeam_aiops.connection import VeeamApiError
from veeam_aiops.ops import _paging, _revision, footprint, ranking


def _usage(routes: dict, build: str | None = "13.1.0.411", name: str = "VM01") -> dict:
    return footprint.object_storage_usage(RouteFake(routes, build=build), name)


def _backup(out: dict, machine: int = 0, index: int = 0) -> dict:
    return out["machines"][machine]["backups"][index]


# ─── paging ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fetch_all_follows_pagination_total():
    fake = RouteFake({"/x": [{"id": n} for n in range(450)]}, build=None)
    assert len(_paging.fetch_all(fake, "/x")) == 450
    assert [c[2]["skip"] for c in fake.calls_to("/x")] == [0, 200, 400]


@pytest.mark.unit
def test_fetch_all_without_pagination_block_is_one_page():
    fake = RouteFake({"/x": {"data": [{"id": 1}, {"id": 2}]}}, build=None)
    assert len(_paging.fetch_all(fake, "/x")) == 2
    assert len(fake.calls_to("/x")) == 1


@pytest.mark.unit
def test_server_capping_the_page_size_still_pages_without_gaps():
    items = [{"id": n} for n in range(450)]

    def capped(params, _headers):
        return page(items, {**params, "limit": min(int(params["limit"]), 100)})

    fake = RouteFake({"/x": capped}, build=None)
    got = _paging.fetch_all(fake, "/x")
    assert [i["id"] for i in got] == list(range(450))
    assert [c[2]["skip"] for c in fake.calls_to("/x")] == [0, 100, 200, 300, 400]


@pytest.mark.unit
def test_server_ignoring_skip_is_refused_not_double_counted():
    items = [{"id": n} for n in range(450)]
    fake = RouteFake({"/x": lambda params, _h: page(items, {**params, "skip": 0})}, build=None)
    with pytest.raises(_paging.IncompleteCollection, match="does not honour skip"):
        _paging.fetch_all(fake, "/x")


@pytest.mark.unit
def test_server_stopping_short_of_its_total_is_refused():
    def short(params, _headers):
        body = page([{"id": n} for n in range(200)], params)
        body["pagination"]["total"] = 450
        return body

    with pytest.raises(_paging.IncompleteCollection, match="stopped returning"):
        _paging.fetch_all(RouteFake({"/x": short}, build=None), "/x")


@pytest.mark.unit
def test_page_budget_is_refused_not_truncated():
    fake = RouteFake({"/x": [{"id": n} for n in range(50)]}, build=None)
    with pytest.raises(_paging.IncompleteCollection, match="refusing to return a partial"):
        _paging.fetch_all(fake, "/x", page_size=10, max_pages=2)


# ─── revision negotiation ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(("build", "revision"), [
    ("12.3.0.310", "1.2-rev0"),
    ("12.3.1.1139", "1.2-rev1"),
    ("12.3.2.3617", "1.2-rev1"),
    ("13.0.0.4967", "1.3-rev0"),
    ("13.0.1.180", "1.3-rev1"),
    ("13.1", "1.3-rev1"),          # shorter than the table's build: conservative
    ("13.1.0.411", "1.3-rev2"),
    ("13.2.0.1", "1.3-rev2"),
])
def test_revision_follows_veeams_published_table(build, revision):
    assert _revision.negotiate(RouteFake({}, build=build)) == {
        "revision": revision, "buildVersion": build, "basis": "serverInfo", "buildError": None,
        "probeRefusals": []}


@pytest.mark.unit
def test_server_older_than_12_3_is_refused_with_the_reason():
    with pytest.raises(_revision.UnsupportedServer, match=r"12\.3\.0\.310"):
        _revision.negotiate(RouteFake({}, build="12.2.0.334"))


def _viewer_routes(accepted: set[str]) -> dict:
    """serverInfo is Backup Administrator only; /backups answers some revisions."""
    def backups(_params, headers):
        if headers.get("x-api-version") in accepted:
            return {"data": [], "pagination": {"total": 0, "count": 0}}
        raise VeeamApiError("Veeam API error (400)", status_code=400)

    return {"/api/v1/serverInfo": VeeamApiError("forbidden (403)", status_code=403),
            "/api/v1/backups": backups}


@pytest.mark.unit
def test_viewer_account_probes_for_the_newest_accepted_revision():
    got = _revision.negotiate(RouteFake(_viewer_routes({"1.2-rev1", "1.2-rev0"}), build=None))
    assert got["revision"] == "1.2-rev1" and got["basis"] == "probe"
    assert "403" in got["buildError"]
    assert [r["revision"] for r in got["probeRefusals"]] == ["1.3-rev2", "1.3-rev1", "1.3-rev0"]


@pytest.mark.unit
def test_probe_stops_on_an_auth_failure_and_assumes_the_minimum():
    routes = {"/api/v1/serverInfo": RuntimeError("down"),
              "/api/v1/backups": VeeamApiError("unauthorized (401)", status_code=401)}
    fake = RouteFake(routes, build=None)
    got = _revision.negotiate(fake)
    assert got["revision"] == "1.2-rev0" and got["basis"] == "assumed"
    assert len(fake.calls_to("/api/v1/backups")) == 1


@pytest.mark.unit
def test_backup_files_404_under_a_probed_revision_is_about_the_backup():
    """Second review, finding 3: the server accepted the revision, so a 404 on one
    backup's files proves nothing about the build."""
    routes = {**vm01_routes(PER_MACHINE_FILES), **_viewer_routes({"1.3-rev2"})}
    routes["/api/v1/backups/b1/backupFiles"] = VeeamApiError("not found (404)", status_code=404)
    out = _usage(routes, build=None)
    m = out["machines"][0]
    assert m["backups"] == [] and m["unreadableBackups"][0]["backupId"] == "b1"
    assert footprint.UNREADABLE_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_backup_files_missing_on_an_unproven_server_is_explained():
    routes = {**vm01_routes(PER_MACHINE_FILES), **_viewer_routes(set())}
    routes["/api/v1/backups/b1/backupFiles"] = VeeamApiError("not found (404)", status_code=404)
    with pytest.raises(_revision.UnsupportedServer, match="VBR 12.3"):
        _usage(routes, build=None)


# ─── per-object usage ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_usage_sums_only_the_objects_own_files():
    out = _usage(vm01_routes(PER_MACHINE_FILES), name="vm01")
    assert out["found"] is True and len(out["machines"]) == 1
    assert out["restorePointFilter"] == "honoured"
    b = _backup(out)
    assert b["storedBytes"] == 130 * GiB          # VM02's 999 GiB file is excluded
    assert b["fullBytes"] == 100 * GiB
    assert b["incrementalBytes"] == 30 * GiB
    assert b["restorePoints"] == 3
    assert b["kindBasis"] == {"restorePointType": 3}
    assert b["gfsFiles"] == {"Weekly": 1}
    assert b["repositoryName"] == "REPO01" and b["backupName"] == "Daily Production"
    assert b["retention"] == {"jobType": "VSphereBackup", "type": "RestorePoints",
                              "quantity": 31, "gfsEnabled": True, "error": None}
    assert out["machines"][0]["approxSourceBytes"] == 400 * GiB
    assert out["totals"]["storedBytes"] == 130 * GiB


@pytest.mark.unit
def test_sizes_are_ints_not_floats():
    """Bug class #2: an equality assert would pass on 130.0; assert the type."""
    b = _backup(_usage(vm01_routes(PER_MACHINE_FILES)))
    for key in ("storedBytes", "fullBytes", "incrementalBytes", "sourceDataBytes"):
        assert type(b[key]) is int, key


@pytest.mark.unit
def test_change_rate_is_mean_increment_over_latest_full():
    rate = _backup(_usage(vm01_routes(PER_MACHINE_FILES)))["changeRate"]
    assert rate["latestFullDataBytes"] == 380 * GiB
    assert rate["avgIncrementDataBytes"] == round(57 * GiB / 2)
    assert rate["changeRatePctPerIncrement"] == 7.5


@pytest.mark.unit
def test_size_reads_send_the_negotiated_revision():
    fake = RouteFake(vm01_routes(PER_MACHINE_FILES), build="12.3.1.1139")
    footprint.object_storage_usage(fake, "VM01")
    sized = fake.calls_to("/api/v1/backups/b1/backupFiles")
    assert sized and all(c[3] == {"x-api-version": "1.2-rev1"} for c in sized)


@pytest.mark.unit
def test_shared_file_is_reported_but_never_charged():
    files = [*PER_MACHINE_FILES[:3],
             bfile("f5", "Daily.vbk", ["o1", "o2"], 500 * GiB, 900 * GiB, "2026-08-01T01:00:00Z")]
    rps = [*VM01_POINTS, rp("rp0", "b1", "f5", "Full", "2026-08-01T01:00:00Z")]
    out = _usage(vm01_routes(files, rps))
    b = _backup(out)
    assert b["storedBytes"] == 130 * GiB
    assert b["sharedFiles"] == 1 and b["sharedStoredBytes"] == 500 * GiB
    assert footprint.SHARED_CAVEAT in out["caveats"]


_PER_JOB_POINTS = [rp("rp1", "b1", "f1", "Full", "2026-09-01T01:00:00Z"),
                   rp("rp0", "b1", "f5", "Full", "2026-08-01T01:00:00Z")]


def _per_job(owner, points) -> dict:
    return vm01_routes([
        bfile("f1", "VM01.vbk", "o1", 100 * GiB, 380 * GiB, "2026-09-01T01:00:00Z"),
        bfile("f5", "Daily.vbk", owner, 500 * GiB, 900 * GiB, "2026-08-01T01:00:00Z",
              points=points)], _PER_JOB_POINTS)


@pytest.mark.unit
@pytest.mark.parametrize("owner", ["o2", "o1", None])
def test_per_job_file_is_shared_whatever_single_owner_it_names(owner):
    """Review findings 1 (both rounds): under ``objectId`` a per-job file was
    charged in full to whichever VM it named, or to every VM when it named none.
    Its own restore-point list — rp0 is VM01's, rpB is VM02's — settles it."""
    out = _usage(_per_job(owner, ["rp0", "rpB"]), build="13.0.1.180")
    b = _backup(out)
    assert b["storedBytes"] == 100 * GiB
    assert b["sharedFiles"] == 1 and b["sharedStoredBytes"] == 500 * GiB
    assert footprint.SINGLE_OWNER_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_single_foreign_owner_without_evidence_is_unattributed_not_shared():
    """Second review, finding 2: a lone foreign owner with only this VM's points
    is a contradiction (e.g. an id-namespace mismatch), not sharing."""
    out = _usage(_per_job("o2", ["rp0"]), build="13.0.1.180")
    b = _backup(out)
    assert b["storedBytes"] == 100 * GiB and b["sharedFiles"] == 0
    assert b["unattributedFiles"] == 1 and b["unattributedStoredBytes"] == 500 * GiB
    assert footprint.UNATTRIBUTED_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_owner_id_namespace_mismatch_is_visible_not_silent():
    files = [bfile(f, n, ["vmid-XYZ"], s, 1, "t", points=[p]) for f, n, s, p in (
        ("f1", "VM01.vbk", 100 * GiB, "rp1"), ("f2", "VM01.vib", 10 * GiB, "rp2"),
        ("f3", "VM01-2.vib", 20 * GiB, "rp3"))]
    out = _usage(vm01_routes(files))
    b = _backup(out)
    assert b["storedBytes"] == 0 and b["unattributedStoredBytes"] == 130 * GiB
    assert footprint.UNATTRIBUTED_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_file_listing_only_this_machines_points_is_its_own():
    files = [bfile("f1", "VM01.vbk", None, 100 * GiB, 380 * GiB, "2026-09-01T01:00:00Z",
                   points=["rp1"])]
    rps = [rp("rp1", "b1", "f1", "Full", "2026-09-01T01:00:00Z")]
    b = _backup(_usage(vm01_routes(files, rps)))
    assert b["storedBytes"] == 100 * GiB and b["sharedFiles"] == b["unattributedFiles"] == 0


@pytest.mark.unit
def test_file_with_no_owner_and_no_point_list_is_not_charged():
    files = [bfile("f1", "VM01.vbk", None, 100 * GiB, 380 * GiB, "2026-09-01T01:00:00Z")]
    rps = [rp("rp1", "b1", "f1", "Full", "2026-09-01T01:00:00Z")]
    b = _backup(_usage(vm01_routes(files, rps)))
    assert b["storedBytes"] == 0 and b["unattributedStoredBytes"] == 100 * GiB


@pytest.mark.unit
def test_singular_restore_point_id_spelling_is_read():
    """The spec lists ``restorePointId`` as required while naming the property
    ``restorePointIds`` (1.2-rev0 to 1.3-rev0); both are read."""
    routes = _per_job("o1", None)
    routes["/api/v1/backups/b1/backupFiles"][1]["restorePointId"] = "rpB"
    assert _backup(_usage(routes, build="12.3.0.310"))["sharedFiles"] == 1


@pytest.mark.unit
def test_missing_size_is_counted_not_zeroed():
    files = [*PER_MACHINE_FILES[:3],
             bfile("f6", "VM01-3.vib", ["o1"], None, None, "2026-09-04T01:00:00Z")]
    rps = [*VM01_POINTS, rp("rp6", "b1", "f6", "Increment", "2026-09-04T01:00:00Z")]
    b = _backup(_usage(vm01_routes(files, rps)))
    assert b["unsizedFiles"] == 1 and b["dataSizeMissingFiles"] == 1
    assert b["storedBytes"] == 130 * GiB


@pytest.mark.unit
def test_extension_is_only_a_fallback_and_says_so():
    rps = [rp("rp1", "b1", None, "Full", "2026-09-01T01:00:00Z")]  # no backupFileId
    b = _backup(_usage(vm01_routes(PER_MACHINE_FILES, rps)))
    assert b["kindBasis"] == {"fileExtension": 3}
    assert b["fullBytes"] == 100 * GiB and b["incrementalBytes"] == 30 * GiB


@pytest.mark.unit
def test_not_found_offers_candidates_instead_of_guessing():
    out = _usage(vm01_routes(PER_MACHINE_FILES), name="VM0")
    assert out["found"] is False and out["totals"] is None
    assert out["candidates"] == ["VM01", "VM01-old"]
    assert out["candidatesTruncated"] is False


@pytest.mark.unit
def test_candidate_list_says_when_it_is_cut():
    routes = vm01_routes(PER_MACHINE_FILES)
    routes["/api/v1/backupObjects"] = [{"id": f"o{i}", "name": f"VM{i:02d}x"} for i in range(30)]
    out = _usage(routes, name="VM")
    assert len(out["candidates"]) == 20 and out["candidatesTruncated"] is True


def _two_vcenters(points) -> dict:
    routes = vm01_routes([*PER_MACHINE_FILES,
                          bfile("f7", "VM01b.vbk", ["o7"], 40 * GiB, 1, "2026-09-01T01:00:00Z")])
    routes["/api/v1/backupObjects"] = [
        {"id": "o1", "name": "VM01", "platformName": "VMware", "path": "vc01/DC/VM01"},
        {"id": "o7", "name": "VM01", "platformName": "VMware", "path": "vc02/DC/VM01"},
    ]
    routes["/api/v1/restorePoints"] = points
    return routes


@pytest.mark.unit
def test_same_name_on_two_vcenters_stays_two_machines_and_totals_add_up():
    """Review finding 4: the old fake ignored the filter, so both machines got the
    same points (260 GiB) while the test only checked identities."""
    points = restore_points_by_object({
        "o1": VM01_POINTS, "o7": [rp("rp7", "b1", "f7", "Full", "2026-09-01T01:00:00Z")]})
    out = _usage(_two_vcenters(points))
    assert [m["identity"] for m in out["machines"]] == ["VMware:vc01/DC/VM01",
                                                         "VMware:vc02/DC/VM01"]
    assert [_backup(out, i)["storedBytes"] for i in (0, 1)] == [130 * GiB, 40 * GiB]
    assert out["totals"]["storedBytes"] == 170 * GiB
    assert any("2 distinct machines" in c for c in out["caveats"])


@pytest.mark.unit
def test_ignored_filter_with_same_named_machines_withholds_totals():
    every_point = [*VM01_POINTS, rp("rp7", "b1", "f7", "Full", "2026-09-01T01:00:00Z")]
    out = _usage(_two_vcenters(every_point))
    assert out["restorePointFilter"] == "ignored"
    assert out["totals"] is None
    assert [m["attributable"] for m in out["machines"]] == [False, False]
    assert footprint.OVERLAP_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_unverifiable_filter_sets_points_aside_and_says_so():
    """Second review, finding 4: the negative control failing left renamed-VM
    points dropped with no caveat."""
    points = [rp("rp1", "b1", "f1", "Full", "2026-09-01T01:00:00Z", name="VM01-legacy"),
              *VM01_POINTS[1:]]

    def route(params, _headers):
        if params.get("backupObjectIdFilter") != "o1":
            raise VeeamApiError("not found (404)", status_code=404)
        return points

    routes = vm01_routes(PER_MACHINE_FILES)
    routes["/api/v1/restorePoints"] = route
    out = _usage(routes)
    assert out["restorePointFilter"] == "unknown"
    assert out["machines"][0]["restorePointsSetAside"] == 1
    assert footprint.FILTER_UNVERIFIED_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_ignored_filter_falls_back_to_name_and_says_so():
    routes = vm01_routes(PER_MACHINE_FILES)
    routes["/api/v1/restorePoints"] = [
        *VM01_POINTS, rp("rpX", "b1", "f4", "Full", "2026-09-01T01:00:00Z", name="VM02")]
    out = _usage(routes)
    assert out["restorePointFilter"] == "ignored"
    assert _backup(out)["storedBytes"] == 130 * GiB  # VM02's file not pulled in
    assert footprint.FILTER_IGNORED_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_renamed_machine_keeps_its_old_points_when_the_filter_is_honoured():
    """Review finding 5: points under an older name were dropped, under-billing."""
    rps = [rp("rp1", "b1", "f1", "Full", "2026-09-01T01:00:00Z", name="VM01-legacy"),
           *VM01_POINTS[1:]]
    out = _usage(vm01_routes(PER_MACHINE_FILES, rps))
    assert _backup(out)["restorePoints"] == 3
    assert _backup(out)["storedBytes"] == 130 * GiB
    assert out["machines"][0]["restorePointNamesSeen"] == ["VM01-legacy"]


@pytest.mark.unit
def test_nameless_restore_point_is_kept():
    rps = [{"id": "rp1", "backupId": "b1", "backupFileId": "f1", "type": "Full",
            "creationTime": "2026-09-01T01:00:00Z"}]
    assert _backup(_usage(vm01_routes(PER_MACHINE_FILES, rps)))["restorePoints"] == 1


@pytest.mark.unit
def test_machine_without_inventory_id_is_one_machine_across_backups():
    """Review finding 6: Hyper-V/agents before 1.3-rev2 carry no path/moref."""
    routes = vm01_routes(PER_MACHINE_FILES)
    routes["/api/v1/backupObjects"] = [
        {"id": "o1", "name": "VM01", "platformName": "HyperV"},
        {"id": "p1", "name": "VM01", "platformName": "HyperV"}]
    routes["/api/v1/restorePoints"] = restore_points_by_object({
        "o1": VM01_POINTS, "p1": [rp("rc1", "b2", "c1", "Full", "2026-09-01T05:00:00Z")]})
    routes["/api/v1/backups/b2"] = {"id": "b2", "name": "Copy", "jobId": None}
    routes["/api/v1/backups/b2/backupFiles"] = [
        bfile("c1", "VM01.vbk", ["p1"], 90 * GiB, 1, "2026-09-01T05:00:00Z")]
    out = _usage(routes, build="12.3.0.310")
    assert len(out["machines"]) == 1 and out["machines"][0]["identityBasis"] == "name"
    assert out["totals"]["storedBytes"] == 220 * GiB
    assert footprint.NAME_IDENTITY_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_primary_and_copy_backups_are_reported_separately():
    routes = vm01_routes(PER_MACHINE_FILES, [
        rp("rp1", "b1", "f1", "Full", "2026-09-01T01:00:00Z"),
        rp("rc1", "b2", "c1", "Full", "2026-09-01T05:00:00Z")])
    routes["/api/v1/backups/b2"] = {"id": "b2", "name": "Copy to DR", "jobId": None,
                                    "repositoryId": "r2"}
    routes["/api/v1/backups/b2/backupFiles"] = [
        bfile("c1", "VM01.vbk", ["o1"], 90 * GiB, 380 * GiB, "2026-09-01T05:00:00Z")]
    routes["/api/v1/backupInfrastructure/repositories"] = [{"id": "r2", "name": "DR-S3"}]
    out = _usage(routes)
    backups = out["machines"][0]["backups"]
    assert [b["backupName"] for b in backups] == ["Daily Production", "Copy to DR"]
    assert backups[1]["repositoryName"] == "DR-S3"
    assert backups[1]["retention"]["error"].startswith("backup has no job id")
    assert out["totals"]["backups"] == 2


@pytest.mark.unit
def test_repository_lookup_failure_is_reported():
    routes = vm01_routes(PER_MACHINE_FILES)
    routes["/api/v1/backups/b1"] = {"id": "b1", "name": "Daily", "jobId": "j1",
                                    "repositoryId": "r1"}
    routes["/api/v1/backupInfrastructure/repositories"] = RuntimeError("403 Forbidden")
    b = _backup(_usage(routes))
    assert b["repositoryName"] is None and "403" in b["repositoryLookupError"]


@pytest.mark.unit
def test_unreadable_job_is_reported_not_raised():
    routes = vm01_routes(PER_MACHINE_FILES)
    routes["/api/v1/jobs/j1"] = RuntimeError("403 Forbidden")
    b = _backup(_usage(routes))
    assert b["retention"]["quantity"] is None and "403" in b["retention"]["error"]
    assert b["storedBytes"] == 130 * GiB


@pytest.mark.unit
def test_restore_points_are_paged_past_200():
    rps = [rp(f"rp{i}", "b1", "f1" if i == 0 else "f2", "Full" if i == 0 else "Increment",
              f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z") for i in range(250)]
    assert _backup(_usage(vm01_routes(PER_MACHINE_FILES, rps)))["restorePoints"] == 250


@pytest.mark.unit
def test_empty_name_is_rejected():
    with pytest.raises(ValueError, match="name is required"):
        footprint.object_storage_usage(RouteFake({}), "  ")


# ─── ranking ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ranking_merges_one_vm_across_backups_and_ranks_worst_first():
    out = ranking.storage_ranking(RouteFake(ranking_routes()), limit=10)
    assert [(o["name"], o["storedBytes"], o["rank"]) for o in out["objects"]] == [
        ("VM01", 190 * GiB, 1), ("VM02", 150 * GiB, 2)]
    assert out["objects"][0]["backups"] == ["Copy", "Daily"]
    assert out["sharedFiles"] == 1 and out["sharedStoredBytes"] == 70 * GiB
    assert out["unresolvedFiles"] == 1 and out["unresolvedStoredBytes"] == 5 * GiB
    assert out["truncated"] is False and out["objectsTotal"] == 2


@pytest.mark.unit
def test_ranking_counts_unsized_files_instead_of_zeroing_them():
    """Review finding 3."""
    routes = ranking_routes()
    routes["/api/v1/backups/b1/backupFiles"] = [
        *routes["/api/v1/backups/b1/backupFiles"],
        bfile("f9", "VM02.vib", ["o2"], None, None, "t"),
        bfile("f8", "Daily.vib", ["o1", "o2"], None, 1, "t")]
    out = ranking.storage_ranking(RouteFake(routes), limit=10)
    vm02 = next(o for o in out["objects"] if o["name"] == "VM02")
    assert vm02["unsizedFiles"] == 1 and vm02["dataSizeMissingFiles"] == 1
    assert vm02["storedBytes"] == 150 * GiB
    assert out["sharedUnsizedFiles"] == 1


@pytest.mark.unit
def test_ranking_survives_one_unreadable_backup():
    """Second review, finding 3: one 404 used to abort the whole ranking."""
    routes = ranking_routes()
    routes["/api/v1/backups/b2/backupFiles"] = VeeamApiError("not found (404)", status_code=404)
    out = ranking.storage_ranking(RouteFake(routes), limit=10)
    assert [o["name"] for o in out["objects"]] == ["VM02", "VM01"]
    assert out["unreadableBackups"][0]["backupName"] == "Copy"
    assert footprint.UNREADABLE_CAVEAT in out["caveats"]


@pytest.mark.unit
def test_ranking_truncation_is_measured_and_disclosed():
    out = ranking.storage_ranking(RouteFake(ranking_routes()), limit=1, max_backups=1)
    assert out["returned"] == 1 and out["truncated"] is True
    assert out["backupsTotal"] == 2 and out["backupsScanned"] == 1 and out["backupsTruncated"]


@pytest.mark.unit
@pytest.mark.parametrize(("kwargs", "field"), [
    ({"limit": 0}, "limit"), ({"limit": True}, "limit"), ({"max_backups": 5000}, "max_backups")])
def test_ranking_rejects_bad_bounds(kwargs, field):
    with pytest.raises(ValueError, match=field):
        ranking.storage_ranking(RouteFake(ranking_routes()), **kwargs)


@pytest.mark.unit
def test_ranking_separates_a_file_with_no_owner_from_one_naming_a_stranger():
    """Two unrelated faults must not share one number.

    A file that names no owner at all is ordinary — a per-job chain file. A file
    that names an owner id absent from its own backup's object listing is the
    signal that backup-file owner ids and backup-object ids are different
    namespaces, which is the single assumption Veeam's spec never pins down and
    the thing that decides whether a chargeback total can be trusted. Rolled
    into one ``unresolved`` figure, neither can be acted on: the first is noise
    to be ignored, the second is a reason to stop billing.

    Field report on issue #2 (VBR 13.1.1.18) put 27,962.7 GiB in that one
    bucket, which is why this split exists.
    """
    routes = ranking_routes()
    routes["/api/v1/backups/b1/backupFiles"] = [
        *routes["/api/v1/backups/b1/backupFiles"],
        bfile("f5", "Daily-chain.vbk", None, 9 * GiB, 1, "t")]
    out = ranking.storage_ranking(RouteFake(routes), limit=10)

    assert out["ownerlessFiles"] == 1
    assert out["ownerlessStoredBytes"] == 9 * GiB
    # ghost.vbk names owner "zz", which b1's object listing does not contain.
    assert out["unmatchedOwnerFiles"] == 1
    assert out["unmatchedOwnerStoredBytes"] == 5 * GiB
    # The roll-up still reconciles, so an existing consumer keeps working.
    assert out["unresolvedFiles"] == 2
    assert out["unresolvedStoredBytes"] == 14 * GiB


@pytest.mark.unit
def test_ranking_warns_only_when_an_owner_id_does_not_match_its_backup():
    """The caveat has to be specific to the namespace signal.

    Positive control included: a ranking whose only unresolved bytes are
    ownerless files must NOT raise the alarm, or the warning becomes background
    noise operators learn to scroll past.
    """
    out = ranking.storage_ranking(RouteFake(ranking_routes()), limit=10)
    assert any("do not match" in c for c in out["caveats"])

    routes = ranking_routes()
    routes["/api/v1/backups/b1/backupFiles"] = [
        bfile("f1", "VM01.vbk", ["o1"], 100 * GiB, 1, "t"),
        bfile("f5", "Daily-chain.vbk", None, 9 * GiB, 1, "t")]
    clean = ranking.storage_ranking(RouteFake(routes), limit=10)
    assert clean["unmatchedOwnerFiles"] == 0
    assert not any("do not match" in c for c in clean["caveats"])
