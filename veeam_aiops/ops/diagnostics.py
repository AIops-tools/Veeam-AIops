"""Flagship signature analyses over Veeam telemetry (pure analysis).

Veeam-AIops was born read-heavy (jobs / sessions / repositories); these two
analyses give it the same *transparent* RCA the newer tools in the line ship:
every finding is reported with the measured signal that tripped it — a session
result, a matched error substring, a free-space percentage — so an operator
sees **why** something was flagged, never a black-box verdict.

  1. ``job_failure_findings`` — scan recent backup-job *sessions*, flag every
     Failed / Warning run, and categorize the likely cause (repository full,
     source/guest unreachable, credential/VSS failure, retry exhaustion) from
     the failing log records, citing the session result + matched substring.
  2. ``repository_capacity_findings`` — flag repositories whose free space is
     below threshold (<15% warn, <10% critical), citing free%/free bytes and a
     concrete extend/offload/retention action.

Both are pure functions (no I/O): the MCP / CLI layers collect the normalized
rows (``ops.sessions.list_sessions`` + per-session logs, ``ops.repositories.
repository_state``) and hand them in. Keeping the heuristics pure makes them
trivially unit-testable without a live VBR server.
"""

from __future__ import annotations

# Repository free-space thresholds (percent of capacity still free). Surfaced
# next to the measured value in every finding so the ranking is auditable.
REPO_FREE_WARN_PCT = 15.0
REPO_FREE_CRIT_PCT = 10.0

# Session results that count as a job-run failure worth flagging.
_FAIL_RESULTS = {"failed", "warning"}

# Severity ordering used to rank findings most-urgent first.
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

# Ordered (needles -> cause, action) rules mapping an error substring to a
# category. First match wins, so most-specific causes are listed first.
_CAUSE_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("not enough space", "insufficient space", "no space", "disk full",
         "repository is full", "out of space"),
        "The target repository ran out of space during the run.",
        "Extend the repository, offload/archive old restore points, or tighten "
        "retention; run diagnose repo-capacity to confirm.",
    ),
    (
        ("vss", "guest processing", "credential", "authentication failed",
         "access denied", "logon", "application-aware"),
        "Guest credentials or VSS / application-aware processing failed.",
        "Verify the job's guest credentials and VSS writers on the source; "
        "check the account's privileges, then retry_job.",
    ),
    (
        ("unreachable", "cannot connect", "no connection", "timed out",
         "timeout", "network path", "rpc", "host is down", "offline"),
        "The source host/guest was unreachable during the run.",
        "Confirm the source VM/host and network are up, then retry_job "
        "(retries only the failed objects).",
    ),
    (
        ("retry", "retries", "exhausted"),
        "The job exhausted its automatic retries without succeeding.",
        "Read session_log for the underlying error and fix the root cause "
        "before re-running.",
    ),
)


def _finding(
    severity: str, resource: str, signal: str, detail: str, cause: str, action: str
) -> dict:
    """Build one cited finding (immutable dict — callers never mutate it)."""
    return {
        "severity": severity,
        "resource": resource,
        "signal": signal,
        "detail": detail,
        "cause": cause,
        "action": action,
    }


def _rank(findings: list[dict]) -> list[dict]:
    """Return findings most-urgent first, each carrying its explicit 1-based rank.

    The priority is stated in the payload rather than left implicit in list
    order: a consumer — notably a smaller local model summarising the result —
    should never have to infer urgency from position. Returns new dicts; the
    inputs are not mutated.
    """
    ordered = sorted(findings, key=lambda f: _SEVERITY_RANK.get(f["severity"], 9))
    return [{**finding, "rank": i} for i, finding in enumerate(ordered, 1)]


def _classify_failure(errors: list[str | None]) -> tuple[str, str]:
    """Map failing log substrings to a (cause, action); generic when unmatched.

    Log titles are optional fields and may be ``None`` (the API did not supply
    one); those carry no signal and are skipped rather than stringified into a
    literal "None" that could match a needle by accident.
    """
    blob = " ".join(e for e in errors if e).lower()
    for needles, cause, action in _CAUSE_RULES:
        if any(n in blob for n in needles):
            return cause, action
    return (
        "Job run did not complete successfully; root cause not auto-classified.",
        "Open session_log for this session to read the failing step.",
    )


def _first_error(errors: list[str | None]) -> str:
    """First non-empty error line, truncated for a compact citation.

    ``None`` entries (absent log titles) are skipped, not rendered as "None".
    """
    for e in errors:
        if e is None:
            continue
        text = str(e).strip()
        if text:
            return text[:160]
    return ""


def job_failure_findings(
    session_rows: list[dict], error_index: dict[str, list[str | None]] | None = None
) -> dict:
    """[ANALYSIS] Flag Failed/Warning backup-job sessions and categorize the cause.

    Args:
        session_rows: recent sessions from ``ops.sessions.list_sessions`` — each
            with ``id``, ``name``, ``type``, ``state``, ``result``.
        error_index: optional map of ``session id -> [failing log titles]`` (from
            ``ops.sessions.get_session_log``) used to classify the cause and cite
            the measured error substring.

    Returns the worst-first ``findings`` list plus counts.
    """
    idx = error_index or {}
    findings: list[dict] = []
    for s in session_rows:
        result = str(s.get("result") or "").strip()
        if result.lower() not in _FAIL_RESULTS:
            continue
        name = str(s.get("name") or s.get("type") or s.get("id") or "?")
        errors = idx.get(str(s.get("id") or ""), [])
        cause, action = _classify_failure(errors)
        cited = _first_error(errors)
        detail = f"session '{name}' result={result}"
        if cited:
            detail += f'; log: "{cited}"'
        findings.append(_finding(
            "critical" if result.lower() == "failed" else "warning",
            name, f"job run {result.lower()}", detail, cause, action,
        ))
    return {
        "findings": _rank(findings),
        "sessionsAnalyzed": len(session_rows),
        "failures": len(findings),
    }


def _free_pct(row: dict) -> float | None:
    """Percent of capacity still free, or None when it can't be computed."""
    used = row.get("usedPercent")
    if isinstance(used, (int, float)):
        return round(100.0 - float(used), 1)
    capacity = row.get("capacity")
    free = row.get("free")
    try:
        cap = float(capacity)
        fr = float(free)
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    return round(fr / cap * 100.0, 1)


def repository_capacity_findings(repo_rows: list[dict]) -> dict:
    """[ANALYSIS] Flag repositories whose free space is below threshold.

    Args:
        repo_rows: capacity rows from ``ops.repositories.repository_state`` — each
            with ``id``, ``name``, ``capacity``, ``free``, ``used``, ``usedPercent``.

    Returns the worst-first ``findings`` list plus a per-repository ``summary`` of
    the measured free percentages. Warn at <15% free, critical at <10% free.
    """
    findings: list[dict] = []
    summary: list[dict] = []
    for r in repo_rows:
        name = str(r.get("name") or r.get("id") or "?")
        free_pct = _free_pct(r)
        summary.append({"name": name, "freePercent": free_pct, "free": r.get("free")})
        if free_pct is None or free_pct >= REPO_FREE_WARN_PCT:
            continue
        crit = free_pct < REPO_FREE_CRIT_PCT
        threshold = REPO_FREE_CRIT_PCT if crit else REPO_FREE_WARN_PCT
        findings.append(_finding(
            "critical" if crit else "warning",
            name, "repository low on space",
            f"free {free_pct}% < {threshold}% (free={r.get('free')}, "
            f"capacity={r.get('capacity')})",
            "The repository is running out of space; the next backup run may fail.",
            "Extend the repository, offload/archive old restore points, or reduce "
            "the job's retention.",
        ))
    return {
        "findings": _rank(findings),
        "summary": summary,
        "repositoriesAnalyzed": len(repo_rows),
    }
