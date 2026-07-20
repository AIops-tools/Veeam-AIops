"""Refuse a restore that would overwrite the VBR server running this tool.

``start_vm_restore`` sends only a restorePointId — no target mapping — so it is
a restore-to-original, an in-place overwrite. Veeam's own guidance is to back up
the VBR server itself, which puts a VBR restore point in the same list as every
other one with nothing marking it as special. There is no undo and there never
could be: the machine that would perform the rollback is the machine being
overwritten.

The guard matches a VM display name against a hostname, so it is a safety net
and not a proof. These tests pin both halves of that: it must fire on the
obvious case, and it must NOT fire on anything else — including when it simply
cannot tell.
"""

from __future__ import annotations

import pytest

from veeam_aiops.ops.restore import SelfLockout, preview_vm_restore, start_vm_restore

_VBR_POINT = {"id": "rp-vbr", "name": "vbr01", "creationTime": "2026-07-19T02:00:00Z"}
_OTHER_POINT = {"id": "rp-sql", "name": "sql-01", "creationTime": "2026-07-19T03:00:00Z"}


def _conn(fake_veeam, point: dict | None, *, host: str = "vbr01.corp.example"):
    responses = {f"/api/v1/restorePoints/{point['id']}": point} if point else {}
    return fake_veeam(responses=responses, host=host)


# ── the guard fires ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_restoring_over_the_vbr_server_is_refused(fake_veeam):
    conn = _conn(fake_veeam, _VBR_POINT)
    with pytest.raises(SelfLockout, match="matches the configured VBR host"):
        start_vm_restore(conn, "rp-vbr")
    assert conn.paths("POST") == [], "must refuse BEFORE issuing the restore"


@pytest.mark.unit
def test_the_refusal_says_why_and_what_to_do_instead(fake_veeam):
    with pytest.raises(SelfLockout) as ei:
        start_vm_restore(_conn(fake_veeam, _VBR_POINT), "rp-vbr")
    msg = str(ei.value)
    assert "restore-to-original" in msg, "must name the mechanism, not just refuse"
    assert "no undo" in msg
    assert "Veeam console" in msg, "must offer a way forward"


@pytest.mark.unit
def test_match_is_case_insensitive_and_accepts_the_fqdn_short_form(fake_veeam):
    """The VM is named 'VBR01'; the target host is 'vbr01.corp.example'."""
    point = dict(_VBR_POINT, name="VBR01")
    with pytest.raises(SelfLockout):
        start_vm_restore(_conn(fake_veeam, point), "rp-vbr")


@pytest.mark.unit
def test_match_also_fires_when_the_host_is_configured_unqualified(fake_veeam):
    with pytest.raises(SelfLockout):
        start_vm_restore(_conn(fake_veeam, _VBR_POINT, host="vbr01"), "rp-vbr")


# ── the guard is exact ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_an_ordinary_vm_restore_still_runs(fake_veeam):
    """Over-blocking would break the tool's entire purpose."""
    conn = _conn(fake_veeam, _OTHER_POINT)
    out = start_vm_restore(conn, "rp-sql")
    assert out["action"] == "vm_restore_started"
    assert out["vmName"] == "sql-01"
    _, path, kwargs = [c for c in conn.calls if c[0] == "POST"][0]
    assert path == "/api/v1/restore/vm"
    assert kwargs["json"] == {"restorePointId": "rp-sql"}


@pytest.mark.unit
def test_a_vm_merely_prefixed_with_the_host_name_is_not_blocked(fake_veeam):
    """Matching is exact, not substring — 'vbr01-fileserver' is a different VM."""
    point = dict(_OTHER_POINT, name="vbr01-fileserver")
    conn = _conn(fake_veeam, point)
    start_vm_restore(conn, "rp-sql")  # must not raise
    assert conn.paths("POST") == ["/api/v1/restore/vm"]


# ── the guard fails open ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_unresolvable_restore_point_does_not_block(fake_veeam):
    """Unknown is never 'it is the VBR server' — the restore proceeds."""
    conn = _conn(fake_veeam, None)  # no canned response: resolution yields {}
    start_vm_restore(conn, "rp-unknown")  # must not raise
    assert conn.paths("POST") == ["/api/v1/restore/vm"]


@pytest.mark.unit
def test_a_read_failure_while_resolving_does_not_block(fake_veeam):
    conn = _conn(fake_veeam, _VBR_POINT)
    conn.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("VBR unreachable"))
    start_vm_restore(conn, "rp-vbr")  # must not raise
    assert conn.paths("POST") == ["/api/v1/restore/vm"]


@pytest.mark.unit
def test_unknown_target_host_does_not_match_a_blank_vm_name(fake_veeam):
    """Two empty strings are equal — the guard must not read that as a match."""
    point = dict(_VBR_POINT, name="")
    conn = _conn(fake_veeam, point, host="")
    start_vm_restore(conn, "rp-vbr")  # must not raise
    assert conn.paths("POST") == ["/api/v1/restore/vm"]


# ── both entry points are guarded ────────────────────────────────────────────


@pytest.mark.unit
def test_the_mcp_tool_refuses(monkeypatch, fake_veeam, tmp_path):
    """A guard that only covers the ops layer is one an agent can walk past."""
    import mcp_server.tools.restore as gov_restore

    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    conn = _conn(fake_veeam, _VBR_POINT)
    monkeypatch.setattr(gov_restore, "_get_connection", lambda target=None: conn)
    out = gov_restore.start_vm_restore(restore_point_id="rp-vbr")
    assert "matches the configured VBR host" in out["error"]
    assert conn.paths("POST") == []


@pytest.mark.unit
def test_the_cli_refuses_too(monkeypatch, fake_veeam, tmp_path):
    from typer.testing import CliRunner

    import mcp_server.tools.restore as gov_restore
    from veeam_aiops.cli import app

    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    conn = _conn(fake_veeam, _VBR_POINT)
    monkeypatch.setattr(gov_restore, "_get_connection", lambda target=None: conn)
    result = CliRunner().invoke(
        app, ["restore", "start", "--restore-point-id", "rp-vbr"], input="y\ny\n"
    )
    # Rich re-wraps the message, so compare on collapsed whitespace.
    assert "matches the configured VBR host" in " ".join(result.output.split())
    # A visible error that still exits 0 is a silent failure to any CI job.
    assert result.exit_code == 1
    assert conn.paths("POST") == []


# ── the preview gives the approver something real ────────────────────────────


@pytest.mark.unit
def test_preview_resolves_the_guid_to_a_vm_name_and_time(fake_veeam):
    out = preview_vm_restore(_conn(fake_veeam, _OTHER_POINT), "rp-sql")
    assert out["vmName"] == "sql-01"
    assert out["creationTime"] == "2026-07-19T03:00:00Z"
    assert out["resolved"] is True


@pytest.mark.unit
def test_preview_says_so_when_it_could_not_resolve(fake_veeam):
    """An unresolved preview must read as 'unknown', not as 'nothing there'."""
    out = preview_vm_restore(_conn(fake_veeam, None), "rp-unknown")
    assert out["resolved"] is False
    assert out["vmName"] is None, "missing must be null, never an empty string"
    assert out["creationTime"] is None


# ── the dry-run answers with the refusal, not with a preview ─────────────────


@pytest.mark.unit
def test_preview_refuses_the_vbr_server_rather_than_flagging_it(fake_veeam):
    """A flag on a preview is something a hurried operator scrolls past.

    A dry-run exists to report what would happen; handing back a clean preview
    for a call that will then be refused is the preview being wrong.
    """
    with pytest.raises(SelfLockout, match="matches the configured VBR host"):
        preview_vm_restore(_conn(fake_veeam, _VBR_POINT), "rp-vbr")


@pytest.mark.unit
def test_mcp_dry_run_on_the_self_target_is_refused(monkeypatch, fake_veeam, tmp_path):
    """Green preview → refusal reads to a model as a transient error to retry."""
    import mcp_server.tools.restore as gov_restore

    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    conn = _conn(fake_veeam, _VBR_POINT)
    monkeypatch.setattr(gov_restore, "_get_connection", lambda target=None: conn)
    out = gov_restore.start_vm_restore(restore_point_id="rp-vbr", dry_run=True)
    assert "matches the configured VBR host" in out["error"]
    assert "dryRun" not in out
    assert conn.paths("POST") == []


@pytest.mark.unit
def test_mcp_dry_run_on_any_other_target_still_previews(monkeypatch, fake_veeam, tmp_path):
    """The dry-run must never refuse what the real call would allow."""
    import mcp_server.tools.restore as gov_restore

    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    conn = _conn(fake_veeam, _OTHER_POINT)
    monkeypatch.setattr(gov_restore, "_get_connection", lambda target=None: conn)
    out = gov_restore.start_vm_restore(restore_point_id="rp-sql", dry_run=True)
    assert out["dryRun"] is True
    assert out["wouldRestore"]["vmName"] == "sql-01"
    assert conn.paths("POST") == []


@pytest.mark.unit
def test_dry_run_fails_open_exactly_as_the_real_call_does(fake_veeam):
    """Identical fail-open semantics on both paths, or the preview lies again."""
    out = preview_vm_restore(_conn(fake_veeam, None), "rp-unknown")  # must not raise
    assert out["resolved"] is False


@pytest.mark.unit
def test_cli_dry_run_on_the_self_target_is_refused(monkeypatch, fake_veeam, tmp_path):
    from typer.testing import CliRunner

    import mcp_server.tools.restore as gov_restore
    from veeam_aiops.cli import app

    monkeypatch.setenv("VEEAM_AIOPS_HOME", str(tmp_path))
    conn = _conn(fake_veeam, _VBR_POINT)
    monkeypatch.setattr(gov_restore, "_get_connection", lambda target=None: conn)
    result = CliRunner().invoke(
        app, ["restore", "start", "--restore-point-id", "rp-vbr", "--dry-run"]
    )
    assert result.exit_code == 1
    assert "matches the configured VBR host" in " ".join(result.output.split())
    assert "DRY-RUN" not in result.output
