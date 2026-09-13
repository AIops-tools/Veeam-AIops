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

Built for estates where a complete scan is slow. On issue #2 a 123-backup VBR
13.1 estate took 21 minutes with 4 s of local CPU, so the cost is the server:

  * the scan can be scoped to named backups (a backup is named after its job)
    or one repository — filtered locally, because ``/backups`` has no
    repository filter and a scope must not depend on a server honouring one;
  * backups can be read with bounded concurrency (opt-in: it is unmeasured on
    a real VBR, whose server is already the bottleneck), results folded in
    scan order so the payload does not depend on which read finished first;
  * a file naming an owner absent from its own backup's listing is looked up
    once per id through ``/backupObjects/{id}``, largest ids first and within a
    budget — a hit proves the id is a backup-object id (a moved or removed
    machine) and its bytes are charged;
  * a backup that timed out is flagged, because the remedy (a longer per-target
    timeout) differs from every other unreadable backup's.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from veeam_aiops.connection import VeeamApiError, _seg
from veeam_aiops.governance import opt_str
from veeam_aiops.ops import _backup_files as bf
from veeam_aiops.ops._revision import negotiate

MAX_LIMIT = 500
MAX_BACKUPS = 1000
MAX_CONCURRENCY = 8
DEFAULT_CONCURRENCY = 1
MAX_OWNER_LOOKUPS = 200  # distinct unmatched owner ids resolved per ranking
MAX_OWNER_DETAILS = 50   # unmatchedOwners entries returned (largest first)
ERROR_MAX = 200

_log = logging.getLogger(__name__)

Progress = Callable[[int, int, str], None]


def _bounded(value: int, name: str, ceiling: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ValueError(f"{name} must be an integer between 1 and {ceiling}.")
    return value


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _clip(text: str, limit: int = ERROR_MAX) -> str:
    """Cut to ``limit`` characters, saying so — a silent cut can drop the remedy."""
    return text if len(text) <= limit else text[:limit] + "…"


def _bucket() -> dict:
    return {"files": 0, "bytes": 0, "unsized": 0}


def _add(bucket: dict, size: int | None) -> None:
    if size is None:
        bucket["unsized"] += 1
    else:
        bucket["bytes"] += size
    bucket["files"] += 1


# ─── scope ───────────────────────────────────────────────────────────────────


def _select(session: bf.Session, backups: list[dict], wanted: Sequence[str] | None,
            repository: str | None) -> list[dict]:
    """The backups in scope, in server order; an empty selection is an error."""
    chosen = backups
    if wanted is not None:
        keys = {_norm(w) for w in wanted if _norm(w)}
        if not keys:
            raise ValueError("backups must name at least one backup id or name.")
        chosen = [b for b in chosen if keys & {_norm(b.get("id")), _norm(b.get("name"))}]
    if repository is not None:
        repo = _norm(repository)
        if not repo:
            raise ValueError("repository must be a repository id or name.")
        chosen = [b for b in chosen if repo in _repository_keys(session, b)]
    if not chosen:
        hint = f" Repository names could not be read: {session.repository_error}" \
            if session.repository_error else ""
        raise ValueError(
            f"The scope (backups={list(wanted) if wanted is not None else None}, "
            f"repository={repository!r}) matched no backup among the "
            f"{len(backups)} on this server. A backup is named after its job; "
            f"list them with backup_list.{hint}"
        )
    return chosen


def _repository_keys(session: bf.Session, backup: dict) -> set[str]:
    repo_id = backup.get("repositoryId")
    name = backup.get("repositoryName") or session.repository_name(repo_id)
    return {k for k in (_norm(repo_id), _norm(name)) if k}


# ─── scan ────────────────────────────────────────────────────────────────────


def _read(session: bf.Session, backup: dict, stop: threading.Event) -> dict:
    bid = str(backup.get("id") or "")
    label = opt_str(backup.get("name"), 128) or "(unnamed)"
    if not bid:
        return {"id": bid, "label": label, "error": ValueError(
            "the server listed this backup without an id, so it cannot be read")}
    try:
        if stop.is_set():
            raise RuntimeError("scan aborted")
        objects = session.all(f"/api/v1/backups/{_seg(bid)}/objects")
        if stop.is_set():
            raise RuntimeError("scan aborted")
        files = session.files(bid)
    except VeeamApiError as exc:  # one unreadable backup must not blank the ranking
        return {"id": bid, "label": label, "error": exc}
    return {"id": bid, "label": label, "objects": objects, "files": files}


def _scan(session: bf.Session, scanned: list[dict], concurrency: int,
          progress: Progress | None) -> list[dict]:
    """Read every scanned backup; results come back in scan order.

    A hard error in one read (or Ctrl-C) returns at once: queued reads are
    cancelled, running ones stop at their next collection boundary, and the
    pool is not joined — a context manager would wait out every in-flight read,
    each of which may page ``backupFiles`` at a 300 s budget.
    """
    results: list[dict] = [{}] * len(scanned)
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(scanned))))
    futures = {pool.submit(_read, session, b, stop): i for i, b in enumerate(scanned)}
    try:
        for done, future in enumerate(as_completed(futures), 1):
            index = futures[future]
            results[index] = future.result()
            progress = _report(progress, done, len(scanned), results[index]["label"])
    except BaseException:
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return results


def _report(progress: Progress | None, done: int, total: int, label: str) -> Progress | None:
    """Call the progress callback; a failing one is dropped, never fatal.

    Progress is cosmetic. A closed stderr twenty minutes into a scan must not
    throw away the readings, so the failure is logged and progress stops.
    """
    if progress is None:
        return None
    try:
        progress(done, total, label)
    except Exception as exc:  # noqa: BLE001 — see docstring
        _log.warning("ranking progress callback failed; progress disabled: %s", exc)
        return None
    return progress


# ─── attribution ─────────────────────────────────────────────────────────────


class _Tally:
    """Accumulates one ranking; local to a single call."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.shared, self.ownerless = _bucket(), _bucket()
        self.unmatched, self.recovered = _bucket(), _bucket()
        self.shapes: set[str] = set()
        self.unreadable: list[dict] = []
        # owner id -> [(backup id, backup label, file)], in scan order
        self.strangers: dict[str, list[tuple[str, str, dict]]] = {}

    def fold(self, result: dict) -> None:
        if "error" in result:
            exc = result["error"]
            self.unreadable.append({
                "backupId": opt_str(result["id"], 64), "backupName": result["label"],
                "error": _clip(str(exc)), "timedOut": bool(getattr(exc, "timed_out", False))})
            return
        by_id = {str(o.get("id")): o for o in result["objects"] if o.get("id")}
        for f in result["files"]:
            listed, shape = bf.owners(f)
            self.shapes.add(shape)
            if len(listed) > 1:
                _add(self.shared, bf.as_int(f.get("backupSize")))
            elif not listed:
                _add(self.ownerless, bf.as_int(f.get("backupSize")))
            elif listed[0] in by_id:
                self.charge(by_id[listed[0]], result["label"], f)
            else:
                self.strangers.setdefault(listed[0], []).append((result["id"], result["label"], f))

    def charge(self, owner: dict, label: str, f: dict) -> None:
        key, basis = bf.identity(owner)
        row = self.rows.setdefault(key, _new_row(owner, key, basis))
        row["files"] += 1
        row["backups"].add(label)
        size = bf.as_int(f.get("backupSize"))
        if size is None:
            row["unsizedFiles"] += 1
        else:
            row["storedBytes"] += size
        raw = bf.as_int(f.get("dataSize"))
        if raw is None:
            row["dataSizeMissingFiles"] += 1
        else:
            row["sourceDataBytes"] += raw


def _new_row(owner: dict, key: str, basis: str) -> dict:
    return {
        "name": opt_str(owner.get("name"), 128), "identity": key, "identityBasis": basis,
        "platformName": opt_str(owner.get("platformName"), 64),
        "storedBytes": 0, "sourceDataBytes": 0, "files": 0, "unsizedFiles": 0,
        "dataSizeMissingFiles": 0, "backups": set(),
    }


def _lookup(session: bf.Session, owner_id: str,
            backup_ids: set[str]) -> tuple[dict | None, str, str | None]:
    """``(object, resolution, error)`` for an owner id its backup did not list."""
    try:
        obj = session.get(f"/api/v1/backupObjects/{_seg(owner_id)}")
    except VeeamApiError as exc:
        if exc.status_code == 404:
            return None, "notFound", None
        return None, "lookupFailed", _clip(str(exc))
    if not isinstance(obj, dict) or not obj.get("id"):
        return None, "lookupFailed", "the server answered without a backup object"
    home = str(obj.get("backupId") or "")
    if not home:
        return obj, "resolved", None
    return obj, "sameBackup" if home in backup_ids else "otherBackup", None


def _stranger_bytes(entries: list[tuple[str, str, dict]]) -> int:
    return sum(bf.as_int(f.get("backupSize")) or 0 for _, _, f in entries)


def _resolve_strangers(session: bf.Session, tally: _Tally) -> tuple[list[dict], int]:
    """Look up unknown owners largest first; every id gets a detail, in that order.

    Largest first because the lookup budget is finite: on a build where the id
    namespaces genuinely differ every file is a stranger, and the ids worth
    checking are the ones carrying the bytes.
    """
    details: list[dict] = []
    skipped = 0
    order = sorted(tally.strangers.items(), key=lambda kv: (-_stranger_bytes(kv[1]), kv[0]))
    for n, (owner_id, entries) in enumerate(order):
        if n < MAX_OWNER_LOOKUPS:
            obj, resolution, error = _lookup(session, owner_id, {e[0] for e in entries})
        else:
            obj, resolution, error = None, "skipped", None
            skipped += 1
        sizes = [bf.as_int(f.get("backupSize")) for _, _, f in entries]
        for (_, label, f), size in zip(entries, sizes, strict=True):
            if obj is not None:
                tally.charge(obj, label, f)
                _add(tally.recovered, size)
            else:
                _add(tally.unmatched, size)
        details.append({
            "ownerId": opt_str(owner_id, 64), "resolution": resolution,
            "objectName": opt_str(obj.get("name"), 128) if obj else None,
            "objectBackupId": opt_str(obj.get("backupId"), 64) if obj else None,
            "backupNames": sorted({label for _, label, _ in entries}),
            "files": len(entries), "storedBytes": sum(s for s in sizes if s is not None),
            "unsizedFiles": sum(1 for s in sizes if s is None), "error": error,
        })
    return details, skipped


# ─── entry point ─────────────────────────────────────────────────────────────


def storage_ranking(
    conn: Any,
    limit: int = 20,
    max_backups: int = 100,
    backups: Sequence[str] | None = None,
    repository: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress: Progress | None = None,
) -> dict:
    """[READ] Protected objects ranked by attributed backup storage, largest first.

    Scans up to ``max_backups`` of the backups in scope (all of them, or those
    named in ``backups`` by id or name, and/or stored in ``repository``), reading
    ``concurrency`` backups at a time. ``progress(done, total, backup_name)`` is
    called as each backup finishes.
    """
    limit = _bounded(limit, "limit", MAX_LIMIT)
    max_backups = _bounded(max_backups, "max_backups", MAX_BACKUPS)
    concurrency = _bounded(concurrency, "concurrency", MAX_CONCURRENCY)
    if isinstance(backups, str):
        backups = [backups]
    session = bf.Session(conn, negotiate(conn))
    every = session.all("/api/v1/backups")
    in_scope = _select(session, every, backups, repository)
    scanned = in_scope[:max_backups]
    tally = _Tally()
    for result in _scan(session, scanned, concurrency, progress):
        tally.fold(result)
    owners, skipped = _resolve_strangers(session, tally)
    ordered = sorted(tally.rows.values(), key=lambda r: (-r["storedBytes"], r["identity"]))
    ranked = [{**r, "backups": sorted(r["backups"]), "rank": i}
              for i, r in enumerate(ordered[:limit], 1)]
    scoped = backups is not None or repository is not None
    return {
        "objects": ranked,
        "returned": len(ranked),
        "limit": limit,
        "truncated": len(ordered) > limit,
        "objectsTotal": len(ordered),
        "backupsTotal": len(every),
        "backupsInScope": len(in_scope),
        "backupsScanned": len(scanned),
        "backupsTruncated": len(in_scope) > len(scanned),
        "scoped": scoped,
        "scope": {"backups": list(backups) if backups is not None else None,
                  "repository": repository},
        "unreadableBackups": tally.unreadable,
        **_totals(tally),
        "unmatchedOwners": owners[:MAX_OWNER_DETAILS],
        "unmatchedOwnersTotal": len(owners),
        "unmatchedOwnersTruncated": len(owners) > MAX_OWNER_DETAILS,
        "ownerLookupsSkipped": skipped,
        "apiRevision": session.negotiated,
        "caveats": _caveats(tally, ordered, scoped, owners),
    }


def _totals(tally: _Tally) -> dict:
    out: dict[str, int] = {}
    for prefix, bucket in (("shared", tally.shared), ("ownerless", tally.ownerless),
                           ("unmatchedOwner", tally.unmatched),
                           ("recoveredOwner", tally.recovered)):
        out[f"{prefix}Files"] = bucket["files"]
        out[f"{prefix}StoredBytes"] = bucket["bytes"]
        out[f"{prefix}UnsizedFiles"] = bucket["unsized"]
    # Still-uncharged bytes only: recovered files are in a machine's storedBytes.
    for suffix, key in (("Files", "files"), ("StoredBytes", "bytes"),
                        ("UnsizedFiles", "unsized")):
        out[f"unresolved{suffix}"] = tally.ownerless[key] + tally.unmatched[key]
    return out


def _caveats(tally: _Tally, ordered: list[dict], scoped: bool,
             owners: list[dict]) -> list[str]:
    caveats = [bf.BLOCK_CLONE_CAVEAT]
    if scoped:
        caveats.append(bf.SCOPED_CAVEAT)
    if tally.shared["files"]:
        caveats.append(bf.SHARED_CAVEAT)
    if "objectId" in tally.shapes:
        caveats.append(bf.SINGLE_OWNER_CAVEAT)
    if any(r["identityBasis"] == "name" for r in ordered):
        caveats.append(bf.NAME_IDENTITY_CAVEAT)
    if tally.unreadable:
        caveats.append(bf.UNREADABLE_CAVEAT)
    if any(u["timedOut"] for u in tally.unreadable):
        caveats.append(bf.TIMEOUT_CAVEAT)
    # The namespace alarm is for ids the server was asked about and could not
    # resolve; an id past the lookup budget was never checked.
    if any(o["resolution"] in ("notFound", "lookupFailed") for o in owners):
        caveats.append(bf.UNMATCHED_OWNER_CAVEAT)
    if any(o["resolution"] == "skipped" for o in owners):
        caveats.append(bf.LOOKUP_SKIPPED_CAVEAT)
    if tally.recovered["files"]:
        caveats.append(bf.RECOVERED_OWNER_CAVEAT)
    return caveats
