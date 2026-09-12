"""Protected objects ranked by backup storage consumed (read-only).

The fleet view of :mod:`footprint`: which machines are the most expensive to
protect. Each backup file is charged to its single listed owner and one machine
is merged across backups by identity. Files listing several owners, files whose
owner cannot be resolved, and files without a size are reported as totals —
never charged to anyone, never counted as zero.

No restore-point data is read here (that would be one query per machine), so
under the single-``objectId`` shape a shared file is charged to its listed
owner; the caveat says so, and ``backup_object_storage_usage`` on the machine
itself can tell shared files apart.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.connection import VeeamApiError, _seg
from veeam_aiops.governance import opt_str
from veeam_aiops.ops import _backup_files as bf
from veeam_aiops.ops._revision import negotiate

MAX_LIMIT = 500
MAX_BACKUPS = 1000


def _bounded(value: int, name: str, ceiling: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ValueError(f"{name} must be an integer between 1 and {ceiling}.")
    return value


def _new_row(owner: dict, key: str, basis: str) -> dict:
    return {
        "name": opt_str(owner.get("name"), 128), "identity": key, "identityBasis": basis,
        "platformName": opt_str(owner.get("platformName"), 64),
        "storedBytes": 0, "sourceDataBytes": 0, "files": 0, "unsizedFiles": 0,
        "dataSizeMissingFiles": 0, "backups": set(),
    }


def _add(bucket: dict, size: int | None) -> None:
    if size is None:
        bucket["unsized"] += 1
    else:
        bucket["bytes"] += size
    bucket["files"] += 1


def storage_ranking(conn: Any, limit: int = 20, max_backups: int = 100) -> dict:
    """[READ] Protected objects ranked by attributed backup storage, largest first.

    Scans up to ``max_backups`` backups, groups each backup file under its single
    owner, and merges an owner across backups by identity (vCenter path / moref
    for VMware, BIOS UUID for agents, name when the revision exposes neither).
    """
    limit = _bounded(limit, "limit", MAX_LIMIT)
    max_backups = _bounded(max_backups, "max_backups", MAX_BACKUPS)
    session = bf.Session(conn, negotiate(conn))
    backups = session.all("/api/v1/backups")
    scanned = backups[:max_backups]
    rows: dict[str, dict] = {}
    shared = {"files": 0, "bytes": 0, "unsized": 0}
    # Two buckets, not one: a file naming no owner is an ordinary per-job chain
    # file, while a file naming an owner its backup does not list is the
    # id-namespace signal that decides whether these totals can be billed.
    ownerless = {"files": 0, "bytes": 0, "unsized": 0}
    unmatched = {"files": 0, "bytes": 0, "unsized": 0}
    shapes: set[str] = set()
    unreadable: list[dict] = []
    for backup in scanned:
        bid = str(backup.get("id") or "")
        if not bid:
            continue
        label = opt_str(backup.get("name"), 128) or bid
        try:
            objects = session.all(f"/api/v1/backups/{_seg(bid)}/objects")
            files = session.files(bid)
        except VeeamApiError as exc:  # one unreadable backup must not blank the ranking
            unreadable.append({"backupId": opt_str(bid, 64), "backupName": label,
                               "error": str(exc)[:200]})
            continue
        by_id = {str(o.get("id")): o for o in objects if o.get("id")}
        for f in files:
            listed, shape = bf.owners(f)
            shapes.add(shape)
            size = bf.as_int(f.get("backupSize"))
            if len(listed) > 1:
                _add(shared, size)
                continue
            owner = by_id.get(listed[0]) if listed else None
            if owner is None:
                _add(unmatched if listed else ownerless, size)
                continue
            key, basis = bf.identity(owner)
            row = rows.setdefault(key, _new_row(owner, key, basis))
            row["files"] += 1
            row["backups"].add(label)
            if size is None:
                row["unsizedFiles"] += 1
            else:
                row["storedBytes"] += size
            raw = bf.as_int(f.get("dataSize"))
            if raw is None:
                row["dataSizeMissingFiles"] += 1
            else:
                row["sourceDataBytes"] += raw
    ordered = sorted(rows.values(), key=lambda r: (-r["storedBytes"], r["identity"]))
    ranked = [{**r, "backups": sorted(r["backups"]), "rank": i}
              for i, r in enumerate(ordered[:limit], 1)]
    caveats = [bf.BLOCK_CLONE_CAVEAT]
    if shared["files"]:
        caveats.append(bf.SHARED_CAVEAT)
    if "objectId" in shapes:
        caveats.append(bf.SINGLE_OWNER_CAVEAT)
    if any(r["identityBasis"] == "name" for r in ordered):
        caveats.append(bf.NAME_IDENTITY_CAVEAT)
    if unreadable:
        caveats.append(bf.UNREADABLE_CAVEAT)
    if unmatched["files"]:
        caveats.append(bf.UNMATCHED_OWNER_CAVEAT)
    return {
        "objects": ranked,
        "returned": len(ranked),
        "limit": limit,
        "truncated": len(ordered) > limit,
        "objectsTotal": len(ordered),
        "backupsTotal": len(backups),
        "backupsScanned": len(scanned),
        "backupsTruncated": len(backups) > len(scanned),
        "unreadableBackups": unreadable,
        "sharedFiles": shared["files"],
        "sharedStoredBytes": shared["bytes"],
        "sharedUnsizedFiles": shared["unsized"],
        "unresolvedFiles": ownerless["files"] + unmatched["files"],
        "unresolvedStoredBytes": ownerless["bytes"] + unmatched["bytes"],
        "unresolvedUnsizedFiles": ownerless["unsized"] + unmatched["unsized"],
        "ownerlessFiles": ownerless["files"],
        "ownerlessStoredBytes": ownerless["bytes"],
        "ownerlessUnsizedFiles": ownerless["unsized"],
        "unmatchedOwnerFiles": unmatched["files"],
        "unmatchedOwnerStoredBytes": unmatched["bytes"],
        "unmatchedOwnerUnsizedFiles": unmatched["unsized"],
        "apiRevision": session.negotiated,
        "caveats": caveats,
    }
