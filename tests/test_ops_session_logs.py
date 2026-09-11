"""Session logs in the shape Veeam actually returns.

Veeam's published spec (every revision 1.1-rev0 to 1.3-rev2) answers
``GET /api/v1/sessions/{id}/logs`` with ``SessionLogResult`` =
``{"totalRecords": int, "records": [SessionLogRecordModel]}``, each record
``{id, status, startTime, updateTime, title, description}``. The reader looked
for ``data`` instead, so on a real server it iterated the dict's *keys* and
crashed — ``session_log`` failed outright, and ``job_failure_rca`` swallowed the
crash and reported every failure as "not auto-classified".
"""

from __future__ import annotations

import pytest
from footprint_fixtures import RouteFake

from veeam_aiops.ops import diagnostics, sessions

SPEC_LOG = {
    "totalRecords": 3,
    "records": [
        {"id": 1, "status": "Succeeded", "startTime": "2026-09-11T01:00:00Z",
         "updateTime": "2026-09-11T01:00:05Z", "title": "Job started"},
        {"id": 2, "status": "Failed", "startTime": "2026-09-11T01:10:00Z",
         "updateTime": "2026-09-11T01:12:00Z", "title": "Processing VM01",
         "description": "Error: There is not enough space on the repository"},
        {"id": 3, "status": "Warning", "startTime": "2026-09-11T01:13:00Z",
         "updateTime": "2026-09-11T01:13:01Z", "title": "Job finished with warnings"},
    ],
}


def _fake(log) -> RouteFake:
    return RouteFake({"/api/v1/sessions/s1/logs": log}, build=None)


@pytest.mark.unit
def test_session_log_reads_the_spec_shape():
    recs = sessions.get_session_log(_fake(SPEC_LOG), "s1")
    assert [r["title"] for r in recs] == ["Job started", "Processing VM01",
                                           "Job finished with warnings"]
    assert recs[1]["description"] == "Error: There is not enough space on the repository"
    assert recs[1]["updateTime"] == "2026-09-11T01:12:00Z"
    assert recs[0]["description"] is None  # absent, not ""


@pytest.mark.unit
def test_failing_lines_include_the_description():
    lines, error = sessions.failing_log_lines(_fake(SPEC_LOG), "s1")
    assert error is None
    assert lines == ["Processing VM01: Error: There is not enough space on the repository",
                     "Job finished with warnings"]


@pytest.mark.unit
def test_unreadable_log_is_reported_not_read_as_empty():
    lines, error = sessions.failing_log_lines(
        RouteFake({"/api/v1/sessions/s1/logs": RuntimeError("500 boom")}, build=None), "s1")
    assert lines == [] and "boom" in error


@pytest.mark.unit
def test_rca_classifies_the_cause_from_a_spec_shaped_log():
    lines, _ = sessions.failing_log_lines(_fake(SPEC_LOG), "s1")
    out = diagnostics.job_failure_findings(
        [{"id": "s1", "name": "Daily", "result": "Failed"}], {"s1": lines})
    assert out["findings"][0]["cause"].startswith("The target repository ran out of space")
    assert "not enough space" in out["findings"][0]["detail"]


@pytest.mark.unit
def test_rca_names_the_sessions_whose_logs_it_could_not_read():
    out = diagnostics.job_failure_findings(
        [{"id": "s1", "name": "Daily", "result": "Failed"}], {"s1": []},
        logs_unreadable=["s1"])
    assert out["logsUnreadable"] == ["s1"]
