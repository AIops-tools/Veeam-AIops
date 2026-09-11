"""Backup storage footprint of one protected object (read-only).

Answers "how much backup storage does this VM consume?" from Veeam's own
per-file accounting — ``GET /api/v1/backups/{id}/backupFiles`` returns, for
every .vbk/.vib/.vrb, ``backupSize`` ("actual, physical amount of data ...
stored in the repository after compression and deduplication") and
``dataSize`` (before compression). Everything here is a sum over those
numbers; nothing is estimated from repository totals.

Rules that keep the numbers honest enough to bill against:

  * **A file shared by several machines is never charged to one of them** —
    see :func:`_backup_files.classify`. Shared bytes are reported beside the
    machine's total (``sharedStoredBytes``), never inside it.
  * **Full vs incremental comes from Veeam, not a guess.** Each restore point
    carries its ``type`` and the ``backupFileId`` it lives in (revision 1.2+);
    the file extension is only a fallback, and ``kindBasis`` says which was used.
  * **A missing size is not zero.** Files without a size are counted and left
    out of the sums.
  * **The restore-point filter is checked, not trusted.** A query for an object
    id that cannot exist must come back empty; if it does not, the server is
    ignoring ``backupObjectIdFilter`` and the result says so.
  * **Every collection is paged to completion** (see ``_paging``).

Pricing is deliberately absent: storage cost models are organisation-specific.
"""

from __future__ import annotations

import uuid
from typing import Any

from veeam_aiops.connection import VeeamApiError, _seg
from veeam_aiops.governance import opt_str
from veeam_aiops.ops import _backup_files as bf
from veeam_aiops.ops._paging import _items
from veeam_aiops.ops._revision import negotiate

MAX_CANDIDATES = 20

# Re-exported for callers and tests that quote the caveat text.
BLOCK_CLONE_CAVEAT = bf.BLOCK_CLONE_CAVEAT
SHARED_CAVEAT = bf.SHARED_CAVEAT
SINGLE_OWNER_CAVEAT = bf.SINGLE_OWNER_CAVEAT
NAME_IDENTITY_CAVEAT = bf.NAME_IDENTITY_CAVEAT
UNATTRIBUTED_CAVEAT = bf.UNATTRIBUTED_CAVEAT
UNREADABLE_CAVEAT = bf.UNREADABLE_CAVEAT
FILTER_UNVERIFIED_CAVEAT = (
    "Whether this server honours backupObjectIdFilter could not be verified, so "
    "only restore points carrying this exact name were kept; points under another "
    "name (a renamed machine) were set aside — see restorePointsSetAside."
)
OVERLAP_CAVEAT = (
    "The same restore points came back for two different machines, so the server "
    "is not filtering by object and nothing here can be attributed; totals are "
    "withheld. Report this with the VBR build."
)
FILTER_IGNORED_CAVEAT = (
    "This server ignores backupObjectIdFilter on /api/v1/restorePoints; restore "
    "points were matched by name instead, so a renamed machine loses points taken "
    "under its old name."
)


def _tally(files: list[dict], rp_kinds: dict[str, str]) -> dict:
    """Sum sizes by kind over files already attributed to one machine."""
    by_kind = dict.fromkeys(bf.KINDS, 0)
    basis: dict[str, int] = {}
    gfs: dict[str, int] = {}
    stored = data = unsized = data_missing = 0
    for f in files:
        kind, how = bf.file_kind(f, rp_kinds)
        basis[how] = basis.get(how, 0) + 1
        for period in f.get("gfsPeriods") or []:
            if period and period != "None":
                gfs[period] = gfs.get(period, 0) + 1
        size = bf.as_int(f.get("backupSize"))
        if size is None:
            unsized += 1
        else:
            stored += size
            by_kind[kind] += size
        raw = bf.as_int(f.get("dataSize"))
        if raw is None:
            data_missing += 1
        else:
            data += raw
    return {
        "storedBytes": stored,
        "fullBytes": by_kind["full"],
        "incrementalBytes": by_kind["incremental"],
        "rollbackBytes": by_kind["rollback"],
        "otherBytes": by_kind["other"],
        "sourceDataBytes": data,
        "files": len(files),
        "unsizedFiles": unsized,
        "dataSizeMissingFiles": data_missing,
        "kindBasis": basis,
        "gfsFiles": gfs,
    }


def _change_rate(files: list[dict], rp_kinds: dict[str, str]) -> dict:
    """Average increment size relative to the latest full (pre-compression)."""
    fulls: list[tuple[str, int]] = []
    increments: list[int] = []
    for f in files:
        kind, _ = bf.file_kind(f, rp_kinds)
        size = bf.as_int(f.get("dataSize"))
        if size is None:
            continue
        if kind == "full":
            fulls.append((str(f.get("creationTime") or ""), size))
        elif kind == "incremental":
            increments.append(size)
    latest_full = max(fulls)[1] if fulls else None
    average = round(sum(increments) / len(increments)) if increments else None
    pct = None
    if latest_full and average is not None:
        pct = round(average / latest_full * 100, 2)
    return {
        "latestFullDataBytes": latest_full,
        "avgIncrementDataBytes": average,
        "changeRatePctPerIncrement": pct,
        "basis": "mean incremental dataSize / latest full dataSize (pre-compression)",
    }


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _retention(session: bf.Session, job_id: str | None) -> dict:
    """Retention settings of the job that writes this backup."""
    empty = {"jobType": None, "type": None, "quantity": None, "gfsEnabled": None}
    if not job_id:
        return {**empty, "error": "backup has no job id (imported or orphaned backup)"}
    try:
        job = _mapping(session.get(f"/api/v1/jobs/{_seg(job_id)}"))
    except Exception as exc:  # noqa: BLE001 — reported, not swallowed
        return {**empty, "error": str(exc)[:200]}
    storage = _mapping(job.get("storage"))
    policy = _mapping(storage.get("retentionPolicy"))
    enabled = _mapping(storage.get("gfsPolicy")).get("isEnabled")
    job_type = opt_str(job.get("type"), 64)
    return {
        "jobType": job_type,
        "type": opt_str(policy.get("type"), 32),
        "quantity": bf.as_int(policy.get("quantity")),
        "gfsEnabled": enabled if isinstance(enabled, bool) else None,
        "error": None if policy else f"job type {job_type} carries no storage.retentionPolicy",
    }


def _sizes(files: list[dict]) -> tuple[int, int]:
    """(bytes over files that report a size, count of files that do not)."""
    sizes = [bf.as_int(f.get("backupSize")) for f in files]
    return sum(s for s in sizes if s is not None), sum(1 for s in sizes if s is None)


def _backup_usage(session: bf.Session, backup_id: str, rps: list[dict],
                  object_ids: set[str], point_ids: set[str]) -> dict:
    meta = _mapping(session.get(f"/api/v1/backups/{_seg(backup_id)}"))
    rp_kinds = {str(rp["backupFileId"]): bf.rp_kind(rp) for rp in rps if rp.get("backupFileId")}
    buckets: dict[str, list[dict]] = {"mine": [], "shared": [], "unattributed": []}
    shapes: set[str] = set()
    for f in session.files(backup_id):
        verdict = bf.classify(f, object_ids, set(rp_kinds), point_ids)
        if verdict is None:
            continue
        shapes.add(bf.owners(f)[1])
        buckets[verdict].append(f)
    mine = buckets["mine"]
    times = sorted(str(rp.get("creationTime")) for rp in rps if rp.get("creationTime"))
    repository_id = opt_str(meta.get("repositoryId"), 64)
    repository_name = opt_str(meta.get("repositoryName"), 128) or session.repository_name(
        repository_id)
    shared_bytes, shared_unsized = _sizes(buckets["shared"])
    loose_bytes, loose_unsized = _sizes(buckets["unattributed"])
    return {
        "backupId": opt_str(backup_id, 64),
        "backupName": opt_str(meta.get("name"), 128),
        "jobId": opt_str(meta.get("jobId"), 64),
        "repositoryId": repository_id,
        "repositoryName": repository_name,
        "repositoryLookupError": None if repository_name else session.repository_error,
        "restorePoints": len(rps),
        "oldestRestorePoint": times[0] if times else None,
        "newestRestorePoint": times[-1] if times else None,
        **_tally(mine, rp_kinds),
        "sharedFiles": len(buckets["shared"]),
        "sharedStoredBytes": shared_bytes,
        "sharedUnsizedFiles": shared_unsized,
        "unattributedFiles": len(buckets["unattributed"]),
        "unattributedStoredBytes": loose_bytes,
        "unattributedUnsizedFiles": loose_unsized,
        "ownershipField": sorted(shapes),
        "changeRate": _change_rate(mine, rp_kinds),
        "retention": _retention(session, opt_str(meta.get("jobId"), 64)),
    }


def _filter_check(session: bf.Session) -> str:
    """``honoured`` / ``ignored`` / ``unknown`` — a negative control query."""
    try:
        data = session.get("/api/v1/restorePoints",
                           params={"backupObjectIdFilter": str(uuid.uuid4()), "skip": 0,
                                   "limit": 1})
    except Exception:  # noqa: BLE001 — cannot tell; treated as not proven honoured
        return "unknown"
    return "ignored" if _items(data) else "honoured"


def _restore_points(session: bf.Session, object_ids: set[str], name: str,
                    filter_state: str) -> tuple[list[dict], list[str], int]:
    """Restore points of these backup objects, other names seen, and points set aside.

    When the filter is proven honoured every returned point is kept — a point
    under another name is a rename, and dropping it would under-bill. Otherwise
    only points carrying this name are kept, and the rest are counted so the
    omission is visible rather than silent.
    """
    seen: dict[str, dict] = {}
    other_names: set[str] = set()
    set_aside: set[str] = set()
    for oid in sorted(object_ids):
        for rp in session.all("/api/v1/restorePoints", params={"backupObjectIdFilter": oid}):
            rp_name = str(rp.get("name") or "").strip()
            if rp_name and rp_name.lower() != name.lower():
                if filter_state != "honoured":
                    set_aside.add(str(rp.get("id")))
                    continue
                other_names.add(rp_name)
            seen[str(rp.get("id"))] = rp
    return list(seen.values()), sorted(other_names), len(set_aside)


def _machine_usage(session: bf.Session, key: str, basis: str, objects: list[dict], name: str,
                   filter_state: str) -> tuple[dict, set[str]]:
    """One machine's usage payload, plus its restore-point ids for the overlap check."""
    object_ids = {str(o.get("id")) for o in objects if o.get("id")}
    rps, other_names, set_aside = _restore_points(session, object_ids, name, filter_state)
    point_ids = {str(rp.get("id")) for rp in rps}
    by_backup: dict[str, list[dict]] = {}
    for rp in rps:
        if rp.get("backupId"):
            by_backup.setdefault(str(rp["backupId"]), []).append(rp)
    backups: list[dict] = []
    unreadable: list[dict] = []
    for bid in sorted(by_backup):
        try:
            backups.append(_backup_usage(session, bid, by_backup[bid], object_ids, point_ids))
        except VeeamApiError as exc:  # one unreadable backup must not blank the rest
            unreadable.append({"backupId": opt_str(bid, 64), "error": str(exc)[:200]})
    first = objects[0]
    sizes = [s for s in (bf.as_int(o.get("size")) for o in objects) if s is not None]
    payload = {
        "identity": key,
        "identityBasis": basis,
        "platformName": opt_str(first.get("platformName"), 64),
        "type": opt_str(first.get("type"), 64),
        "backupObjectIds": sorted(object_ids),
        # BackupObjectModel.size ("approximate size of the backed-up object") —
        # revision 1.3-rev2 only; null on older servers, not zero.
        "approxSourceBytes": max(sizes) if sizes else None,
        "restorePointNamesSeen": other_names,
        "restorePointsSetAside": set_aside,
        "attributable": True,
        "backups": backups,
        "unreadableBackups": unreadable,
    }
    return payload, point_ids


def _totals(machines: list[dict]) -> dict:
    keys = ("storedBytes", "fullBytes", "incrementalBytes", "rollbackBytes", "otherBytes",
            "sourceDataBytes", "restorePoints", "files", "unsizedFiles", "sharedFiles",
            "sharedStoredBytes", "sharedUnsizedFiles", "unattributedFiles",
            "unattributedStoredBytes", "unattributedUnsizedFiles")
    rows = [b for m in machines for b in m["backups"]]
    return {**{k: sum(b[k] for b in rows) for k in keys}, "backups": len(rows)}


def _overlapping(point_sets: list[set[str]]) -> bool:
    seen: set[str] = set()
    for ids in point_sets:
        if seen & ids:
            return True
        seen |= ids
    return False


def _caveats(machines: list[dict], filter_state: str, overlap: bool) -> list[str]:
    rows = [b for m in machines for b in m["backups"]]
    caveats = [BLOCK_CLONE_CAVEAT]
    if any(b["sharedFiles"] for b in rows):
        caveats.append(SHARED_CAVEAT)
    if any("objectId" in b["ownershipField"] for b in rows):
        caveats.append(SINGLE_OWNER_CAVEAT)
    if any(b["unattributedFiles"] for b in rows):
        caveats.append(UNATTRIBUTED_CAVEAT)
    if any(m["unreadableBackups"] for m in machines):
        caveats.append(UNREADABLE_CAVEAT)
    if any(m["identityBasis"] == "name" for m in machines):
        caveats.append(NAME_IDENTITY_CAVEAT)
    if filter_state == "ignored":
        caveats.append(FILTER_IGNORED_CAVEAT)
    elif filter_state == "unknown" and any(m["restorePointsSetAside"] for m in machines):
        caveats.append(FILTER_UNVERIFIED_CAVEAT)
    if overlap:
        caveats.append(OVERLAP_CAVEAT)
    elif len(machines) > 1:
        caveats.append(
            f"{len(machines)} distinct machines share this name (told apart by "
            f"identity). Totals add them together — check each before charging."
        )
    return caveats


def object_storage_usage(conn: Any, name: str) -> dict:
    """[READ] Backup storage consumed by one protected object, per backup.

    Finds the object by exact (case-insensitive) name, then sums Veeam's
    per-file sizes across every backup holding its restore points — primary
    jobs and backup copies alike, each reported separately with its repository,
    restore-point count, full/incremental split, GFS files, job retention and
    an increment-vs-full change rate. Requires VBR 12.3+ (REST 1.2-rev0).
    """
    wanted = (name or "").strip()
    if not wanted:
        raise ValueError("name is required: the protected object's name as Veeam shows it.")
    session = bf.Session(conn, negotiate(conn))
    found = session.all("/api/v1/backupObjects", params={"nameFilter": wanted})
    exact = [o for o in found if str(o.get("name") or "").strip().lower() == wanted.lower()]
    base = {"object": wanted, "apiRevision": session.negotiated}
    if not exact:
        names = sorted({str(o.get("name")) for o in found if o.get("name")})
        return {**base, "found": False, "candidates": names[:MAX_CANDIDATES],
                "candidatesTruncated": len(names) > MAX_CANDIDATES,
                "restorePointFilter": None, "machines": [], "totals": None, "caveats": []}
    grouped: dict[str, tuple[str, list[dict]]] = {}
    for obj in exact:
        key, basis = bf.identity(obj)
        grouped.setdefault(key, (basis, []))[1].append(obj)
    filter_state = _filter_check(session)
    results = [_machine_usage(session, key, basis, objs, wanted, filter_state)
               for key, (basis, objs) in sorted(grouped.items())]
    machines = [payload for payload, _ in results]
    overlap = _overlapping([points for _, points in results])
    if overlap:
        machines = [{**m, "attributable": False} for m in machines]
    return {
        **base,
        "found": True,
        "candidates": [],
        "candidatesTruncated": False,
        "restorePointFilter": filter_state,
        "machines": machines,
        "totals": None if overlap else _totals(machines),
        "caveats": _caveats(machines, filter_state, overlap),
    }
