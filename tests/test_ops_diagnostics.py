"""Diagnostics / RCA coverage: pure heuristics + the two governed MCP tools.

The pure functions in ``veeam_aiops.ops.diagnostics`` are exercised directly
(threshold trips, healthy = no findings, worst-first ranking, missing-field
robustness, cause classification). The MCP tools are then driven through a
mocked Veeam connection to prove they collect the right telemetry and carry the
governance harness marker.
"""

from __future__ import annotations

import pytest

import veeam_aiops.governance.audit as audit_mod
import veeam_aiops.governance.policy as policy_mod
import veeam_aiops.governance.undo as undo_mod
from mcp_server.tools import diagnostics as diag_tools
from veeam_aiops.ops import diagnostics as diag


@pytest.fixture(autouse=True)
def _gov_home(tmp_path, monkeypatch):
    """Isolate harness state so governed-tool calls don't touch the real home."""
    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


# ─── job_failure_findings (pure) ─────────────────────────────────────────────


@pytest.mark.unit
def test_job_failure_healthy_has_no_findings():
    rows = [
        {"id": "s1", "name": "Daily-SQL", "result": "Success"},
        {"id": "s2", "name": "Weekly-Files", "result": "Success"},
    ]
    out = diag.job_failure_findings(rows)
    assert out["findings"] == []
    assert out["failures"] == 0
    assert out["sessionsAnalyzed"] == 2


@pytest.mark.unit
def test_job_failure_flags_and_ranks_worst_first():
    rows = [
        {"id": "s1", "name": "Daily-SQL", "result": "Warning"},
        {"id": "s2", "name": "Weekly-Files", "result": "Failed"},
        {"id": "s3", "name": "Ok-Job", "result": "Success"},
    ]
    out = diag.job_failure_findings(rows)
    sev = [f["severity"] for f in out["findings"]]
    assert sev == ["critical", "warning"]  # Failed ranked before Warning
    assert out["failures"] == 2
    assert out["findings"][0]["resource"] == "Weekly-Files"


@pytest.mark.unit
@pytest.mark.parametrize(
    "error_text,expected_needle",
    [
        ("Not enough space on the target repository", "out of space"),
        ("VSS snapshot creation failed for guest", "VSS"),
        ("Cannot connect to host: network path timed out", "unreachable"),
        ("All retries have been exhausted", "retries"),
    ],
)
def test_job_failure_classifies_cause_from_log(error_text, expected_needle):
    rows = [{"id": "s1", "name": "J", "result": "Failed"}]
    out = diag.job_failure_findings(rows, {"s1": [error_text]})
    finding = out["findings"][0]
    assert expected_needle.lower() in finding["cause"].lower()
    # the measured error substring is cited in the detail
    assert error_text[:20] in finding["detail"]


@pytest.mark.unit
def test_job_failure_unclassified_and_missing_fields_are_robust():
    rows = [
        {"result": "Failed"},  # no id/name -> "?" resource, no log index entry
        {"id": "s2", "name": "X"},  # no result -> skipped
    ]
    out = diag.job_failure_findings(rows)
    assert out["failures"] == 1
    f = out["findings"][0]
    assert f["resource"] == "?"
    assert "not auto-classified" in f["cause"]


# ─── repository_capacity_findings (pure) ─────────────────────────────────────


@pytest.mark.unit
def test_repo_capacity_healthy_has_no_findings():
    rows = [{"id": "r1", "name": "Main", "usedPercent": 40.0, "free": 600, "capacity": 1000}]
    out = diag.repository_capacity_findings(rows)
    assert out["findings"] == []
    assert out["repositoriesAnalyzed"] == 1
    assert out["summary"][0]["freePercent"] == 60.0


@pytest.mark.unit
def test_repo_capacity_warn_and_critical_thresholds_and_ranking():
    rows = [
        {"id": "r1", "name": "Warn", "usedPercent": 88.0},  # 12% free -> warning
        {"id": "r2", "name": "Crit", "usedPercent": 95.0},  # 5% free -> critical
        {"id": "r3", "name": "Edge", "usedPercent": 85.0},  # exactly 15% free -> ok
    ]
    out = diag.repository_capacity_findings(rows)
    sev = [(f["severity"], f["resource"]) for f in out["findings"]]
    assert sev == [("critical", "Crit"), ("warning", "Warn")]  # worst-first, Edge excluded
    assert "free 5.0% < 10.0%" in out["findings"][0]["detail"]


@pytest.mark.unit
def test_repo_capacity_computes_free_pct_from_capacity_when_no_used_percent():
    rows = [{"id": "r1", "name": "Calc", "capacity": 1000, "free": 80}]  # 8% free -> critical
    out = diag.repository_capacity_findings(rows)
    assert out["findings"][0]["severity"] == "critical"
    assert out["summary"][0]["freePercent"] == 8.0


@pytest.mark.unit
def test_repo_capacity_missing_or_bad_fields_are_skipped():
    rows = [
        {"id": "r1", "name": "NoData"},  # nothing to compute
        {"id": "r2", "name": "Bad", "capacity": "x", "free": "y"},
        {"id": "r3", "name": "ZeroCap", "capacity": 0, "free": 0},
    ]
    out = diag.repository_capacity_findings(rows)
    assert out["findings"] == []  # none crash, none flagged
    assert all(s["freePercent"] is None for s in out["summary"])


# ─── governed MCP tools (mocked connection) ──────────────────────────────────


@pytest.mark.unit
def test_job_failure_rca_tool_collects_sessions_and_logs(monkeypatch, fake_veeam):
    conn = fake_veeam(
        {
            "/api/v1/sessions/sess-9/logs": {
                "data": [
                    {"title": "Not enough space on repository", "status": "Failed"},
                    {"title": "noise", "status": "Success"},
                ]
            },
            "/api/v1/sessions": {
                "data": [
                    {"id": "sess-9", "name": "Nightly", "result": {"result": "Failed"}},
                    {"id": "sess-1", "name": "Ok", "result": {"result": "Success"}},
                ]
            },
        }
    )
    monkeypatch.setattr(diag_tools, "_get_connection", lambda target=None: conn)

    assert diag_tools.job_failure_rca._is_governed_tool is True
    out = diag_tools.job_failure_rca()
    assert out["failures"] == 1
    finding = out["findings"][0]
    assert finding["severity"] == "critical"
    assert "out of space" in finding["cause"].lower()
    # only the failing session's log was fetched (not the healthy one)
    assert conn.paths("GET").count("/api/v1/sessions/sess-9/logs") == 1
    assert not any("sess-1/logs" in p for p in conn.paths("GET"))


@pytest.mark.unit
def test_repository_capacity_rca_tool_collects_state(monkeypatch, fake_veeam):
    conn = fake_veeam(
        {
            "/api/v1/backupInfrastructure/repositories/states": {
                "data": [
                    {"id": "r1", "name": "Full", "capacityGB": 1000, "freeGB": 50},  # 5% free
                    {"id": "r2", "name": "Ok", "capacityGB": 1000, "freeGB": 500},  # 50% free
                ]
            },
        }
    )
    monkeypatch.setattr(diag_tools, "_get_connection", lambda target=None: conn)

    assert diag_tools.repository_capacity_rca._is_governed_tool is True
    out = diag_tools.repository_capacity_rca()
    assert out["repositoriesAnalyzed"] == 2
    assert out["findings"][0]["resource"] == "Full"
    assert out["findings"][0]["severity"] == "critical"
    assert any("/repositories/states" in p for p in conn.paths("GET"))


@pytest.mark.unit
def test_rank_assigns_explicit_worst_first_rank():
    """Findings state their priority explicitly, not implicitly by list order.

    A consumer — notably a smaller local model summarising the result — must not
    have to infer urgency from a finding's position in the list.
    """
    from veeam_aiops.ops import diagnostics as _diag

    ranked = _diag._rank([{"severity": "info"}, {"severity": "critical"}, {"severity": "warning"}])
    assert [f["severity"] for f in ranked] == ["critical", "warning", "info"]
    assert [f["rank"] for f in ranked] == [1, 2, 3]
