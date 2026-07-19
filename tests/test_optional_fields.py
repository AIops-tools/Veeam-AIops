"""Absent fields come back as null, not as an empty string.

An empty string reads as "this field exists and is empty"; a missing field is a
different fact. Collapsing the two hides information from any consumer, and a
smaller local model will confidently invent the difference. These tests pin the
contract end-to-end: helper, ops layer, and the CLI rendering that has to cope
with a null.

The companion contract lives here too: a capped read announces its own
truncation rather than leaving the consumer to infer it from a row count.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from veeam_aiops.governance import opt_str
from veeam_aiops.ops import diagnostics as diag
from veeam_aiops.ops import infrastructure as infra_ops
from veeam_aiops.ops import sessions as session_ops

runner = CliRunner()


# ─── the helper ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_opt_str_distinguishes_absent_from_empty():
    assert opt_str(None) is None, "absent must stay absent"
    assert opt_str("") == "", "a genuinely empty value is not the same as absent"
    assert opt_str("Daily-SQL Backup", 64) == "Daily-SQL Backup"


@pytest.mark.unit
def test_opt_str_still_sanitizes_and_truncates():
    assert opt_str("a\x00b") == "ab"  # control character stripped
    assert opt_str("abcdef", 3) == "abc"


@pytest.mark.unit
def test_opt_str_accepts_non_string_values():
    assert opt_str(42) == "42"


# ─── the ops layer ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ops_report_absent_fields_as_none(fake_veeam):
    """A session row with no name/state/result reports null, not ''."""
    fake = fake_veeam(responses={"/api/v1/sessions": {"data": [{"id": "sess-1"}]}})
    rows = session_ops.list_sessions(fake)
    assert rows[0]["id"] == "sess-1"
    assert rows[0]["name"] is None
    assert rows[0]["state"] is None
    assert rows[0]["result"] is None


@pytest.mark.unit
def test_ops_keep_empty_string_when_source_is_empty(fake_veeam):
    """An explicitly empty upstream value is preserved as '' — not turned into null."""
    fake = fake_veeam(
        responses={"/api/v1/sessions": {"data": [{"id": "sess-1", "name": ""}]}}
    )
    assert session_ops.list_sessions(fake)[0]["name"] == ""


@pytest.mark.unit
def test_ops_never_drop_the_key_itself(fake_veeam):
    """Keys are always present; only their value may be null.

    Omitting a key entirely is worse than a null — the consumer cannot tell the
    field was even considered.
    """
    fake = fake_veeam(responses={"/api/v1/sessions": {"data": [{}]}})
    row = session_ops.list_sessions(fake)[0]
    for key in ("id", "name", "type", "state", "result"):
        assert key in row, f"{key} must be present even when the source omitted it"


@pytest.mark.unit
def test_nested_session_result_is_unwrapped_and_absence_preserved(fake_veeam):
    """VBR reports the session result nested or flat; neither invents a value."""
    fake = fake_veeam(
        responses={
            "/api/v1/sessions": {
                "data": [
                    {"id": "a", "result": {"result": "Failed"}},
                    {"id": "b", "result": "Success"},
                    {"id": "c", "result": {}},  # running: nested, no verdict yet
                ]
            }
        }
    )
    rows = session_ops.list_sessions(fake)
    assert rows[0]["result"] == "Failed"
    assert rows[1]["result"] == "Success"
    assert rows[2]["result"] is None


@pytest.mark.unit
def test_proxy_host_absent_is_none_not_empty(fake_veeam):
    """A proxy whose server host the API never returned reports null."""
    path = "/api/v1/backupInfrastructure/proxies"
    fake = fake_veeam(responses={path: {"data": [{"id": "px-1", "name": "Proxy01"}]}})
    row = infra_ops.list_proxies(fake)[0]
    assert row["server"] is None
    assert row["type"] is None


@pytest.mark.unit
def test_session_log_titles_may_be_null(fake_veeam):
    fake = fake_veeam(responses={"/logs": {"data": [{"status": "Failed"}]}})
    rec = session_ops.get_session_log(fake, "sess-1")[0]
    assert rec["title"] is None
    assert rec["startTime"] is None


# ─── analysis consumers must survive a null ──────────────────────────────────


@pytest.mark.unit
def test_diagnostics_ignore_sessions_with_no_result():
    """A null result is 'no verdict yet', never a failure."""
    out = diag.job_failure_findings([{"id": "s1", "name": "Daily-SQL", "result": None}])
    assert out["failures"] == 0
    assert out["findings"] == []


@pytest.mark.unit
def test_diagnostics_skip_null_log_titles_when_classifying():
    """A null log title carries no signal — it must not be matched as text."""
    rows = [{"id": "s1", "name": "Daily-SQL", "result": "Failed"}]
    out = diag.job_failure_findings(rows, {"s1": [None, "Not enough space on repo"]})
    finding = out["findings"][0]
    assert finding["rank"] == 1
    assert "Not enough space on repo" in finding["detail"]
    assert "ran out of space" in finding["cause"]


# ─── the CLI rendering ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_renders_rows_with_null_fields(monkeypatch, fake_veeam):
    """The table must survive a null field rather than crashing on render."""
    from veeam_aiops.cli import app

    fake = fake_veeam(responses={"/api/v1/jobs": {"data": [{"id": "job-1"}]}})
    monkeypatch.setattr(
        "veeam_aiops.cli.job.get_connection",
        lambda target=None, config_path=None: (fake, None),
    )

    result = runner.invoke(app, ["job", "list"])
    assert result.exit_code == 0, result.output
    assert "job-1" in result.output


# ─── truncation announces itself ─────────────────────────────────────────────


@pytest.mark.unit
def test_undo_list_envelope_reports_truncation(monkeypatch):
    """``truncated`` is measured (one extra row fetched), not guessed."""
    from mcp_server.tools import undo as gov

    class _Store:
        def __init__(self) -> None:
            self.asked: list[int] = []

        def list(self, *, status: str, limit: int) -> list[dict]:
            self.asked.append(limit)
            return [
                {
                    "undo_id": f"u{i}",
                    "ts": i,
                    "tool": "job_start",
                    "undo_tool": "job_stop",
                    "note": "",
                }
                for i in range(limit)  # store always has more than we asked for
            ]

    store = _Store()
    monkeypatch.setattr(gov, "get_undo_store", lambda: store)

    out = gov.undo_list(limit=2)
    assert store.asked == [3], "one extra row must be fetched to measure truncation"
    assert out["returned"] == 2
    assert out["limit"] == 2
    assert out["truncated"] is True
    assert len(out["undos"]) == 2


@pytest.mark.unit
def test_undo_list_envelope_is_honest_when_not_truncated(monkeypatch):
    from mcp_server.tools import undo as gov

    class _Store:
        def list(self, *, status: str, limit: int) -> list[dict]:
            return [
                {
                    "undo_id": "u0",
                    "ts": 0,
                    "tool": "job_start",
                    "undo_tool": "job_stop",
                    "note": "",
                }
            ]

    monkeypatch.setattr(gov, "get_undo_store", lambda: _Store())

    out = gov.undo_list(limit=50)
    assert out["truncated"] is False
    assert out["returned"] == 1
    assert out["limit"] == 50
