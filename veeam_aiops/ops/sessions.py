"""Session operations for Veeam Backup & Replication (read-only).

Veeam jobs and restores execute as asynchronous *sessions*. After starting a
job or restore, poll its session to follow progress — the Veeam analog of a
task-status primitive. This lets an agent check a long-running operation once
instead of looping (which, with the veeam-aiops runaway breaker, is the
structural answer to the "poll a slow op, burn tokens" failure mode).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from veeam_aiops.connection import _seg
from veeam_aiops.governance import opt_str, sanitize
from veeam_aiops.ops._paging import fetch_all, fetch_first, history_limit

DEFAULT_LIMIT = 100
# Newest first: both orderColumn/orderAsc exist from revision 1.1-rev1 on.
_NEWEST_FIRST = {"orderColumn": "CreationTime", "orderAsc": False}
SINCE_HOURS_MAX = 24 * 366
# ESessionState values (revision 1.1-rev1) for a session that has not finished:
# everything except Stopped and Idle. One stateFilter query each.
ACTIVE_STATES = ("Starting", "Working", "Stopping", "Pausing", "Resuming", "Postprocessing",
                 "WaitingTape", "WaitingRepository", "WaitingSlot")


def _result_value(s: dict) -> object | None:
    """Unwrap a session result, which VBR reports either nested or flat.

    Returns None when the session has no result at all (a still-running session)
    so the caller can tell "no result yet" from "result was empty".
    """
    raw = s.get("result")
    if isinstance(raw, dict):
        return raw.get("result")
    return raw


def _session_summary(s: dict) -> dict:
    return {
        "id": opt_str(s.get("id"), 64),
        "name": opt_str(s.get("name"), 128),
        "type": opt_str(s.get("sessionType", s.get("type")), 64),
        "state": opt_str(s.get("state"), 32),
        "result": opt_str(_result_value(s), 32),
    }


def _since(hours: Any) -> str:
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= SINCE_HOURS_MAX:
        raise ValueError(f"since_hours must be an integer between 1 and {SINCE_HOURS_MAX}.")
    start = datetime.now(UTC) - timedelta(hours=hours)
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def list_sessions(conn: Any, limit: int = DEFAULT_LIMIT, since_hours: int | None = None) -> dict:
    """[READ] The newest ``limit`` sessions with id, name, type, state, result.

    Returns ``{"sessions", "returned", "limit", "truncated", "order", "since"}``.
    "Recent" is explicit: the server sorts by ``creationTime`` descending, and
    ``truncated`` (measured by asking for one more) says older sessions exist
    beyond the window. ``since_hours`` narrows the window to sessions created in
    the last N hours (``createdAfterFilter``); ``since`` echoes the cut-off sent.
    """
    limit = history_limit(limit)
    params = dict(_NEWEST_FIRST)
    since = _since(since_hours) if since_hours is not None else None
    if since:
        params["createdAfterFilter"] = since
    rows = fetch_first(conn, "/api/v1/sessions", limit + 1, params=params)
    return {
        "sessions": [_session_summary(s) for s in rows[:limit]],
        "returned": min(len(rows), limit),
        "limit": limit,
        "truncated": len(rows) > limit,
        "order": "newest first (creationTime)",
        "since": since,
    }


def active_sessions(conn: Any) -> dict:
    """[READ] Every unfinished session, however long ago it started.

    One ``stateFilter`` query per state in :data:`ACTIVE_STATES`, each read to
    the end — a backup-copy job started days ago is still found, which a
    newest-N window cannot promise. Rows are kept only if their state really is
    the one asked for. A server that ignores the filter returns the whole
    collection on the first query, so that query is filtered locally and the
    rest are skipped (``stateFilterIgnored``). A state the server refuses is
    listed in ``stateQueryErrors`` rather than silently treated as "none".
    """
    found: dict[str, dict] = {}
    errors: list[dict] = []
    ignored = False
    for state in ACTIVE_STATES:
        try:
            rows = fetch_all(conn, "/api/v1/sessions", params={"stateFilter": state})
        except Exception as exc:  # noqa: BLE001 — reported per state, never read as "none"
            errors.append({"state": state, "error": str(exc)[:200]})
            continue
        wanted = {s.lower() for s in ACTIVE_STATES} if any(
            str(r.get("state") or "").lower() != state.lower() for r in rows) else {state.lower()}
        for row in rows:
            if str(row.get("state") or "").lower() in wanted:
                found.setdefault(str(row.get("id")), _session_summary(row))
        if len(wanted) > 1:
            ignored = True
            break
    return {
        "sessions": sorted(found.values(), key=lambda s: str(s.get("id"))),
        "states": list(ACTIVE_STATES),
        "stateFilterIgnored": ignored,
        "stateQueryErrors": errors,
    }


def get_session(conn: Any, session_id: str) -> dict:
    """[READ] Poll one session by id to check job/restore progress.

    Use after start_job or start_vm_restore to follow the operation instead of
    re-issuing it.
    """
    s = conn.get(f"/api/v1/sessions/{_seg(session_id)}")
    summary = _session_summary(s)
    summary["progressPercent"] = s.get("progressPercent")
    summary["creationTime"] = opt_str(s.get("creationTime"), 64)
    return summary


def _log_records(data: Any) -> list:
    """The records of a ``SessionLogResult`` — ``{"totalRecords", "records"}``.

    Veeam's spec (every revision 1.1-rev0 to 1.3-rev2) names the list
    ``records``. ``data`` is still accepted in case a build wraps it like the
    collection endpoints; anything else is no records, never the dict's keys.
    """
    if isinstance(data, dict):
        for key in ("records", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def get_session_log(conn: Any, session_id: str) -> list[dict]:
    """[READ] Return the log records (events) of one session.

    Use to see *why* a session failed instead of re-running the job blind.
    Each record is reduced to its title, description (where Veeam puts the
    error detail), status, and timing (``startTime`` / ``updateTime``).
    """
    data = conn.get(f"/api/v1/sessions/{_seg(session_id)}/logs")
    return [
        {
            "title": opt_str(rec.get("title", rec.get("name")), 200),
            "description": opt_str(rec.get("description"), 400),
            "status": opt_str(rec.get("status"), 32),
            "startTime": opt_str(rec.get("startTime"), 64),
            "updateTime": opt_str(rec.get("updateTime"), 64),
        }
        for rec in _log_records(data)
        if isinstance(rec, dict)
    ]


_FAIL_LOG_STATUSES = {"failed", "warning", "error"}


def failing_log_lines(conn: Any, session_id: str) -> tuple[list[str], str | None]:
    """``(lines, error)``: the Failed/Warning records of one session's log.

    Each line is ``"title: description"`` so the error detail reaches the RCA
    classifier. A log that cannot be read returns its error instead of an empty
    list — "no failing records" and "could not look" must stay distinguishable.
    """
    try:
        records = get_session_log(conn, session_id)
    except Exception as exc:  # noqa: BLE001 — returned to the caller, not swallowed
        return [], str(exc)[:200]
    lines = []
    for rec in records:
        if str(rec.get("status") or "").lower() not in _FAIL_LOG_STATUSES:
            continue
        parts = [p for p in (rec.get("title"), rec.get("description")) if p]
        if parts:
            lines.append(": ".join(parts))
    return lines, None


def collect_failure_logs(conn: Any, session_rows: list[dict],
                         fail_results: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Failing log lines for every failed session, plus the ids whose log was unreadable."""
    index: dict[str, list[str]] = {}
    unreadable: list[str] = []
    for row in session_rows:
        sid = str(row.get("id") or "")
        if not sid or str(row.get("result") or "").lower() not in fail_results:
            continue
        lines, error = failing_log_lines(conn, sid)
        index[sid] = lines
        if error is not None:
            unreadable.append(sid)
    return index, unreadable


def stop_session(conn: Any, session_id: str) -> dict:
    """[WRITE] Stop a running session (cancels the underlying operation).

    No clean inverse — a stopped session must be re-issued via the originating
    job/restore, so this records no undo descriptor.
    """
    conn.post(f"/api/v1/sessions/{_seg(session_id)}/stop")
    return {"session_id": sanitize(session_id, 64), "action": "stop"}
