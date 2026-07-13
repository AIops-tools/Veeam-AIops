"""Smoke tests for the veeam-aiops skeleton.

Proves: every module imports, the CLI Typer app builds and --help works, the
MCP server exposes the expected tools, and EVERY MCP tool carries the
veeam-aiops harness marker ``_is_governed_tool`` (i.e. the governance harness
wraps them). No real Veeam server is needed — ``httpx.Client`` is mocked.
"""

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

EXPECTED_TOOLS = {
    # jobs
    "job_list", "job_get", "job_start", "job_stop", "job_retry",
    "job_enable", "job_disable",
    # restore
    "restore_list_points", "start_vm_restore",
    # repositories
    "repository_list", "repository_get", "repository_state",
    # backups
    "backup_list", "backup_object_list",
    # sessions
    "session_list", "session_get", "session_log", "session_stop",
    # infrastructure
    "managed_server_list", "proxy_list",
    # overview
    "overview",
}

WRITE_TOOLS_WITH_UNDO = {
    "job_start", "job_stop", "job_retry", "job_enable", "job_disable",
}


@pytest.mark.unit
def test_all_modules_import():
    for name in (
        "veeam_aiops",
        "veeam_aiops.config",
        "veeam_aiops.connection",
        "veeam_aiops.doctor",
        "veeam_aiops.secretstore",
        "veeam_aiops.ops.jobs",
        "veeam_aiops.ops.restore",
        "veeam_aiops.ops.repositories",
        "veeam_aiops.ops.sessions",
        "veeam_aiops.ops.backups",
        "veeam_aiops.ops.infrastructure",
        "veeam_aiops.ops.overview",
        "veeam_aiops.cli",
        "veeam_aiops.cli._root",
        "veeam_aiops.cli._common",
        "veeam_aiops.cli.init",
        "veeam_aiops.cli.secret",
        "veeam_aiops.cli.job",
        "veeam_aiops.cli.restore",
        "veeam_aiops.cli.repository",
        "veeam_aiops.cli.session",
        "veeam_aiops.cli.backup",
        "veeam_aiops.cli.infrastructure",
        "veeam_aiops.cli.overview",
        "veeam_aiops.cli.doctor",
        "mcp_server.server",
        "mcp_server._shared",
        "mcp_server.tools.jobs",
        "mcp_server.tools.restore",
        "mcp_server.tools.repositories",
        "mcp_server.tools.sessions",
        "mcp_server.tools.backups",
        "mcp_server.tools.infrastructure",
        "mcp_server.tools.overview",
    ):
        importlib.import_module(name)


@pytest.mark.unit
def test_version_matches_pyproject():
    """__version__ is single-sourced from package metadata; it must track
    pyproject.toml so a release bump can never ship a stale self-report."""
    import tomllib
    from pathlib import Path

    import veeam_aiops

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    assert veeam_aiops.__version__ == expected


@pytest.mark.unit
def test_cli_app_builds_and_help_works():
    from veeam_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in (
        "job", "restore", "repository", "session", "backup", "infra",
        "secret", "init", "overview", "doctor", "mcp",
    ):
        assert sub in result.output


@pytest.mark.unit
def test_cli_leaf_help_triggers_lazy_imports():
    """Recurse into leaf commands so any broken lazy import surfaces."""
    from veeam_aiops.cli import app

    runner = CliRunner()
    for cmd in (
        ["job", "--help"], ["restore", "--help"], ["repository", "--help"],
        ["session", "--help"], ["backup", "--help"], ["infra", "--help"],
        ["secret", "--help"], ["doctor", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"
    for cmd in (
        ["job", "list", "--help"], ["job", "get", "--help"], ["job", "start", "--help"],
        ["job", "stop", "--help"], ["job", "retry", "--help"],
        ["job", "enable", "--help"], ["job", "disable", "--help"],
        ["restore", "list-points", "--help"], ["restore", "start", "--help"],
        ["repository", "list", "--help"], ["repository", "get", "--help"],
        ["repository", "state", "--help"],
        ["session", "list", "--help"], ["session", "get", "--help"],
        ["session", "log", "--help"], ["session", "stop", "--help"],
        ["backup", "list", "--help"], ["backup", "objects", "--help"],
        ["infra", "servers", "--help"], ["infra", "proxies", "--help"],
        ["secret", "list", "--help"], ["secret", "set", "--help"],
        ["init", "--help"], ["overview", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"


@pytest.mark.unit
def test_mcp_list_tools_exposes_expected_tools():
    from mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.unit
def test_every_mcp_tool_is_governed_by_harness():
    """Every registered tool callable must carry the @governed_tool marker."""
    from mcp_server import _shared

    tool_objs = _shared.mcp._tool_manager._tools
    assert EXPECTED_TOOLS <= set(tool_objs), "tool registry incomplete"
    for name, tool in tool_objs.items():
        fn = getattr(tool, "fn", None)
        assert fn is not None, f"{name} has no fn"
        assert getattr(fn, "_is_governed_tool", False), (
            f"{name} is not wrapped with @governed_tool (harness marker missing)"
        )


@pytest.mark.unit
def test_write_tool_records_undo_token_via_harness(monkeypatch):
    """Calling job_start through the harness records an inverse undo descriptor.

    Proves the @governed_tool ``undo=`` feature lights up: the harness invokes the
    undo lambda on success and persists the inverse to the undo store.
    """
    import veeam_aiops.governance.undo as undo_mod
    from mcp_server.tools import jobs as job_tools

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    monkeypatch.setattr(job_tools, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params):
            recorded["descriptor"] = undo_descriptor
            recorded["tool"] = tool
            return "undo-123"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = job_tools.job_start(job_id="abc-123")
    assert "error" not in result
    assert recorded["descriptor"]["tool"] == "job_stop"  # inverse of start
    assert recorded["descriptor"]["params"]["job_id"] == "abc-123"
    assert result.get("_undo_id") == "undo-123"


@pytest.mark.unit
def test_ops_use_mocked_connection():
    """list_jobs works end-to-end against a mocked Veeam connection."""
    from veeam_aiops.ops import jobs as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {
        "data": [
            {
                "id": "job-1", "name": "Daily-SQL", "type": "Backup",
                "status": "Running", "lastResult": "Success",
            }
        ]
    }
    rows = ops.list_jobs(conn)
    assert rows[0]["id"] == "job-1"
    assert rows[0]["name"] == "Daily-SQL"
    assert rows[0]["lastResult"] == "Success"


@pytest.mark.unit
def test_connection_login_and_error_translation(monkeypatch):
    """VeeamConnection logs in via OAuth2 and translates non-2xx to VeeamApiError."""
    import httpx

    from veeam_aiops.config import TargetConfig
    from veeam_aiops.connection import VeeamApiError, VeeamConnection, get_token

    monkeypatch.setenv("VEEAM_LAB_PASSWORD", "secret")
    target = TargetConfig(name="lab", host="vbr.local", username="admin", verify_ssl=False)

    class _Resp:
        def __init__(self, status, payload=None, content=b"{}"):
            self.status_code = status
            self._payload = payload or {}
            self.content = content
            self.text = "body"

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **k):
            self.headers = {}

        def post(self, path, **k):
            return _Resp(200, {"access_token": "TOK"})

        def request(self, method, path, **k):
            if path == "/api/v1/notfound":
                return _Resp(404, content=b"x")
            return _Resp(200, {"data": []}, content=b"{}")

        def close(self):
            pass

    monkeypatch.setattr(httpx, "Client", _Client)
    conn = VeeamConnection(target)
    assert get_token(conn._client) == "TOK"
    assert conn._client.headers["Authorization"] == "Bearer TOK"
    assert conn.get("/api/v1/ok") == {"data": []}
    with pytest.raises(VeeamApiError) as ei:
        conn.get("/api/v1/notfound")
    assert ei.value.status_code == 404
    assert "not found" in str(ei.value).lower()
