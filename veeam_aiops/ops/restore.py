"""Restore operations for Veeam Backup & Replication.

Restore points are read-only; starting a VM restore is a high-risk write with
NO undo (it overwrites or creates a VM). The ``start_vm_restore`` body is a
documented skeleton: the exact restore endpoint and payload vary by restore
type (instant recovery, full VM restore, restore to new location), so this
keeps a minimal, clearly-marked call that callers should adapt to their
Veeam version and restore intent.

Because that payload carries **no target mapping**, the call is a
restore-to-original — an in-place overwrite. Veeam's own guidance is to back up
the VBR server itself, so a VBR restore point sits in the very list
``list_restore_points`` returns and nothing in the summary marks it as special.
Two things guard that here:

  * :func:`start_vm_restore` refuses when the restore point's VM name matches
    the configured VBR host (:class:`SelfLockout`) — a safety net, not a proof;
    see the docstring for exactly what it cannot catch.
  * :func:`preview_vm_restore` resolves the opaque restore-point GUID to the VM
    name and creation time, so the human asked to approve an irreversible
    overwrite has something real to judge instead of an id — and refuses on
    exactly the same condition, so a dry-run never green-lights a restore the
    real call will reject.

Both routes through :func:`_refuse_if_self_restore`, so the guard and its
fail-open behaviour cannot drift apart between the preview and the write.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.connection import _seg
from veeam_aiops.governance import opt_str, sanitize
from veeam_aiops.ops._paging import fetch_first, history_limit

DEFAULT_LIMIT = 100
# Newest first: both orderColumn/orderAsc exist from revision 1.1-rev1 on.
_NEWEST_FIRST = {"orderColumn": "CreationTime", "orderAsc": False}


class SelfLockout(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the operation would overwrite the VBR server running this tool."""


class UnresolvedTarget(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the restore point could not be read, so the target is unknown."""


def _refuse_if_unresolved(restore_point_id: str, rp: dict | None, acknowledged: bool) -> None:
    """Raise unless the restore point resolved, or the caller accepted the risk.

    This call sends no target mapping, so it is a restore-to-original with no
    undo. When the restore point cannot be read the tool cannot say which
    machine it is about to overwrite, and this line's rule for unreadable state
    is that it is never OK (bug class #13) — a preview showing ``vmName: null``
    is not consent, it is the absence of the one fact the decision needs.

    It refuses rather than removing the capability: a disaster is the worst
    moment to be blocked by a tool, so ``acknowledge_unresolved`` still gets the
    restore out. Erring this way is recoverable (use the Veeam console); erring
    the other way overwrites a machine nobody could name.
    """
    if rp or acknowledged:
        return
    raise UnresolvedTarget(
        f"Refusing to restore restore point '{restore_point_id}': it could not "
        f"be read, so this tool cannot say which machine the restore would "
        f"overwrite. The call carries no target mapping, so it is a "
        f"restore-to-original with no undo. Confirm the restore point in the "
        f"Veeam console, or re-run with acknowledge_unresolved=True "
        f"(CLI: --acknowledge-unresolved) to proceed without knowing the target."
    )


def _restore_point_summary(rp: dict) -> dict:
    return {
        "id": opt_str(rp.get("id"), 64),
        "name": opt_str(rp.get("name"), 128),
        "creationTime": opt_str(rp.get("creationTime"), 64),
        "type": opt_str(rp.get("platformName", rp.get("type")), 64),
    }


def _self_host_names(conn: Any) -> set[str]:
    """Lower-cased names for the VBR server this connection talks to.

    Returns the configured ``host`` and its short form (first DNS label).
    **Empty when the target cannot be read** — callers must then fail OPEN, so
    an unknown host is never mistaken for a match.
    """
    target = getattr(conn, "target", None)
    host = str(getattr(target, "host", "") or "").strip().lower()
    if not host:
        return set()
    return {host, host.split(".", 1)[0]}


def _resolve_restore_point(conn: Any, restore_point_id: str) -> dict | None:
    """Best-effort summary of one restore point; ``None`` when unresolvable.

    ``None`` rather than an empty dict on purpose: "could not resolve" must stay
    distinguishable from "resolved, but the name is blank". The self-target
    guard fails OPEN on the former and must never match on the latter.
    """
    try:
        rp = conn.get(f"/api/v1/restorePoints/{_seg(restore_point_id)}")
    except Exception:  # noqa: BLE001 — unresolvable is not fatal; the guard fails open
        return None
    if isinstance(rp, dict) and isinstance(rp.get("data"), dict):
        rp = rp["data"]
    if not isinstance(rp, dict) or not rp:
        return None
    return _restore_point_summary(rp)


def _self_target_match(conn: Any, vm_name: str | None) -> str | None:
    """The configured VBR host name that ``vm_name`` matches, else ``None``.

    Exact and case-insensitive, against the host and its short form. Exact by
    design: a substring test would refuse an unrelated 'vbr01-fileserver'.
    """
    candidate = str(vm_name or "").strip().lower()
    if not candidate:
        return None
    for name in _self_host_names(conn):
        if candidate == name:
            return name
    return None


def list_restore_points(
    conn: Any, backup_id: str | None = None, limit: int = DEFAULT_LIMIT
) -> dict:
    """[READ] The newest ``limit`` restore points (id, name, creation time, type).

    Returns ``{"restorePoints", "returned", "limit", "truncated", "order"}``;
    ``truncated`` is measured by asking for one more point than ``limit``. A
    whole estate's restore points are far too many to return at once, so this
    is a window, newest first, and says when there is more.

    When ``backup_id`` is given, the server filters by ``backupIdFilter``. A
    point from another backup coming back means the server ignored the filter,
    and that is refused rather than returned as if it belonged.
    """
    limit = history_limit(limit)
    params = dict(_NEWEST_FIRST)
    if backup_id:
        params["backupIdFilter"] = backup_id
    rows = fetch_first(conn, "/api/v1/restorePoints", limit + 1, params=params)
    if backup_id:
        wanted = str(backup_id).strip().lower()
        foreign = [r for r in rows
                   if r.get("backupId") and str(r["backupId"]).strip().lower() != wanted]
        if foreign:
            raise ValueError(
                f"The server returned restore points of another backup for "
                f"backupIdFilter={backup_id!r}, so it is not applying that filter; "
                f"refusing to present them as this backup's points."
            )
    return {
        "restorePoints": [_restore_point_summary(rp) for rp in rows[:limit]],
        "returned": min(len(rows), limit),
        "limit": limit,
        "truncated": len(rows) > limit,
        "order": "newest first (creationTime)",
    }


def _refuse_if_self_restore(conn: Any, restore_point_id: str, vm_name: str | None) -> None:
    """Raise :class:`SelfLockout` when ``vm_name`` is the configured VBR host.

    Shared by the preview and the real call so the two can never disagree. A
    dry-run that green-lights a restore the real call then refuses is the
    preview being wrong — and it is the specific failure this line designs
    against: a clean preview followed by a refusal reads to a model as a
    transient error worth retrying.
    """
    matched = _self_target_match(conn, vm_name)
    if not matched:
        return
    raise SelfLockout(
        f"Refusing to restore restore point '{restore_point_id}': its VM "
        f"'{vm_name}' matches the configured VBR host '{matched}'. This call "
        f"sends no target mapping, so it is a restore-to-original — it would "
        f"overwrite the Veeam server serving this API while the call is in "
        f"flight, and there is no undo. Restore the VBR server from the Veeam "
        f"console or a second VBR instead, or re-run against a different "
        f"target if this VM merely shares the VBR server's name."
    )


def preview_vm_restore(conn: Any, restore_point_id: str,
                      acknowledge_unresolved: bool = False) -> dict:
    """[READ] Resolve what a VM restore would overwrite, for the dry-run preview.

    A restore is irreversible, so the preview has to show whoever authorises it
    something they can judge — a bare restore-point GUID is not that. Resolves
    the id to the VM display name and the point's creation time.

    **Refuses exactly what the real restore refuses** (:class:`SelfLockout`),
    with identical fail-open behaviour, rather than reporting the match as a
    field the reader has to notice. A flag on a preview is something a hurried
    operator scrolls past; a refusal is not.

    An unreadable restore point is **refused** (:class:`UnresolvedTarget`) by
    the preview and the real call alike, because a preview reporting
    ``vmName: null`` is not something anyone can authorise. Pass
    ``acknowledge_unresolved=True`` to get the preview (and the restore) anyway;
    ``resolved: false`` then says the target is unknown.
    """
    rp = _resolve_restore_point(conn, restore_point_id)
    _refuse_if_unresolved(restore_point_id, rp, acknowledge_unresolved)
    _refuse_if_self_restore(conn, restore_point_id, (rp or {}).get("name"))
    return {
        "restore_point_id": sanitize(restore_point_id, 64),
        "vmName": (rp or {}).get("name"),
        "creationTime": (rp or {}).get("creationTime"),
        "resolved": rp is not None,
    }


def start_vm_restore(conn: Any, restore_point_id: str,
                    acknowledge_unresolved: bool = False) -> dict:
    """[WRITE] Start a VM restore from a restore point. IRREVERSIBLE — no undo.

    SKELETON: this issues a minimal full-VM-restore start against the restore
    point. Production callers should select the correct restore endpoint
    (instant recovery vs full restore vs restore-to-new-location) and supply
    the matching payload (target host/datastore/network mapping) for their
    Veeam version. Overwriting or creating a VM cannot be undone here.

    **Refuses when the restore point's VM name matches the configured VBR
    host.** With no target mapping in the payload this is a restore-to-original,
    so restoring the VBR server's own restore point overwrites the machine
    serving this API, mid-call, with no undo.

    That check is INCOMPLETE BY NATURE — a safety net, not a proof. **A VM
    display name is not a hostname**: a VBR server whose VM is named
    'Backup Server 01' while its host is 'vbr01.corp.example' is NOT caught, nor
    is one reached by IP or by a CNAME. Confirm the target machine yourself; the
    ``dry_run`` preview names it.

    **An unreadable restore point is refused** (:class:`UnresolvedTarget`),
    because this call cannot then name the machine it would overwrite. That is
    a separate judgement from the self-lockout guard above, which still treats
    an unknown name as "not the VBR server" rather than guessing. Pass
    ``acknowledge_unresolved=True`` to proceed without knowing the target.
    """
    rp = _resolve_restore_point(conn, restore_point_id)
    _refuse_if_unresolved(restore_point_id, rp, acknowledge_unresolved)
    vm_name = (rp or {}).get("name")
    _refuse_if_self_restore(conn, restore_point_id, vm_name)
    body = {"restorePointId": restore_point_id}
    conn.post("/api/v1/restore/vm", json=body)
    return {
        "restore_point_id": sanitize(restore_point_id, 64),
        "vmName": vm_name,
        "action": "vm_restore_started",
    }
