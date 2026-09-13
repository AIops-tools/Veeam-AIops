"""Shared fakes and fixtures for the backup storage footprint tests.

Imported by bare name (``from footprint_fixtures import ...``): pytest's default
prepend import mode puts ``tests/`` itself on ``sys.path``. Fixtures follow
Veeam's published OpenAPI shapes — revision 1.2 reports one ``objectId`` per
backup file, 1.3-rev2 reports ``objectIds``; sizes are int64.

The fake behaves like a well-behaved server by default (it honours ``skip``,
``limit`` and ``backupObjectIdFilter``). A test that needs a misbehaving server
installs a callable route, which receives ``(params, headers)`` and returns the
raw response — so capped pages, ignored ``skip`` or an ignored filter are
exercised explicitly rather than never.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.connection import VeeamApiError

GiB = 1024**3


class RouteFake:
    """Exact-path routes; list values are served paged like the VBR API."""

    def __init__(self, routes: dict[str, Any], build: str | None = "13.1.0.411") -> None:
        self.routes = dict(routes)
        if build is not None and "/api/v1/serverInfo" not in self.routes:
            self.routes["/api/v1/serverInfo"] = {"buildVersion": build}
        self.calls: list[tuple[str, str, dict, dict]] = []

    def get(self, path: str, params: dict | None = None, headers: dict | None = None) -> Any:
        params, headers = dict(params or {}), dict(headers or {})
        self.calls.append(("GET", path, params, headers))
        value = self.routes.get(path)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(params, headers)
            if not isinstance(value, list):
                return value
        if isinstance(value, list):
            return page(value, params)
        return value if value is not None else {}

    def calls_to(self, path: str) -> list[tuple[str, str, dict, dict]]:
        return [c for c in self.calls if c[1] == path]


def page(items: list, params: dict) -> dict:
    skip = int(params.get("skip", 0))
    limit = int(params.get("limit", 200))
    chunk = items[skip:skip + limit]
    return {"data": chunk, "pagination": {"total": len(items), "count": len(chunk),
                                          "skip": skip, "limit": limit}}


def restore_points_by_object(points: dict[str, list[dict]]):
    """A /restorePoints route that honours backupObjectIdFilter like the server."""
    def route(params: dict, _headers: dict) -> list[dict]:
        wanted = params.get("backupObjectIdFilter")
        if wanted is None:
            return [rp for rows in points.values() for rp in rows]
        return list(points.get(str(wanted), []))
    return route


def rp(rid: str, backup: str, file_id: str | None, kind: str, when: str,
       name: str = "VM01") -> dict:
    return {"id": rid, "name": name, "backupId": backup, "backupFileId": file_id,
            "type": kind, "creationTime": when}


def bfile(fid: str, name: str, owners: list[str] | str | None, backup: int | None,
          data: int | None, when: str, gfs: list[str] | None = None,
          points: list[str] | None = None) -> dict:
    if isinstance(owners, list):
        owned: dict = {"objectIds": owners}
    elif owners:
        owned = {"objectId": owners}
    else:
        owned = {}
    if points is not None:
        owned["restorePointIds"] = points
    return {"id": fid, "name": name, "backupId": "b1", **owned, "backupSize": backup,
            "dataSize": data, "dedupRatio": 1, "compressRatio": 2, "creationTime": when,
            "gfsPeriods": gfs or ["None"]}


VM01_POINTS = [
    rp("rp1", "b1", "f1", "Full", "2026-09-01T01:00:00Z"),
    rp("rp2", "b1", "f2", "Increment", "2026-09-02T01:00:00Z"),
    rp("rp3", "b1", "f3", "Increment", "2026-09-03T01:00:00Z"),
]


def vm01_routes(files: list[dict], rps: list[dict] | None = None,
                others: dict[str, list[dict]] | None = None) -> dict:
    """VM01 (backup object o1) in backup b1, plus VM01-old as a name decoy."""
    points = {"o1": rps if rps is not None else VM01_POINTS, **(others or {})}
    return {
        "/api/v1/backupObjects": [
            {"id": "o1", "name": "VM01", "type": "VM", "platformName": "VMware",
             "objectId": "vm-101", "path": "vc01/DC/VM01", "restorePointsCount": 3,
             "size": 400 * GiB},
            {"id": "o9", "name": "VM01-old", "type": "VM", "platformName": "VMware",
             "objectId": "vm-9", "path": "vc01/DC/VM01-old"},
        ],
        "/api/v1/restorePoints": restore_points_by_object(points),
        "/api/v1/backups/b1": {"id": "b1", "name": "Daily Production", "jobId": "j1",
                               "repositoryId": "r1", "repositoryName": "REPO01"},
        "/api/v1/backups/b1/backupFiles": files,
        "/api/v1/jobs/j1": {"type": "VSphereBackup", "storage": {
            "retentionPolicy": {"type": "RestorePoints", "quantity": 31},
            "gfsPolicy": {"isEnabled": True}}},
    }


PER_MACHINE_FILES = [
    bfile("f1", "D:/Backups/Daily/VM01.vbk", ["o1"], 100 * GiB, 380 * GiB,
          "2026-09-01T01:00:00Z", gfs=["Weekly"]),
    bfile("f2", "D:/Backups/Daily/VM01.vib", ["o1"], 10 * GiB, 19 * GiB, "2026-09-02T01:00:00Z"),
    bfile("f3", "D:/Backups/Daily/VM01-2.vib", ["o1"], 20 * GiB, 38 * GiB, "2026-09-03T01:00:00Z"),
    bfile("f4", "D:/Backups/Daily/VM02.vbk", ["o2"], 999 * GiB, 999 * GiB, "2026-09-01T01:00:00Z"),
]


def ranking_routes() -> dict:
    return {
        "/api/v1/backups": [{"id": "b1", "name": "Daily"}, {"id": "b2", "name": "Copy"}],
        "/api/v1/backups/b1/objects": [
            {"id": "o1", "name": "VM01", "platformName": "VMware", "path": "vc01/VM01"},
            {"id": "o2", "name": "VM02", "platformName": "VMware", "path": "vc01/VM02"}],
        "/api/v1/backups/b2/objects": [
            {"id": "p1", "name": "VM01", "platformName": "VMware", "path": "vc01/VM01"}],
        "/api/v1/backups/b1/backupFiles": [
            bfile("f1", "VM01.vbk", ["o1"], 100 * GiB, 1, "t"),
            bfile("f2", "VM02.vbk", ["o2"], 150 * GiB, 1, "t"),
            bfile("f3", "Daily.vbk", ["o1", "o2"], 70 * GiB, 1, "t"),
            bfile("f4", "ghost.vbk", ["zz"], 5 * GiB, 1, "t")],
        "/api/v1/backups/b2/backupFiles": [bfile("c1", "VM01.vbk", ["p1"], 90 * GiB, 1, "t")],
        # A real server answers an unknown backup-object id with 404, not {}.
        "/api/v1/backupObjects/zz": VeeamApiError(
            "Resource not found (404) on /api/v1/backupObjects/zz.", status_code=404),
    }
