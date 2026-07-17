"""mcp_server._shared primitives: error sanitisation, ``tool_errors`` shapes,
and lazy connection init.

The tool bodies rely on ``_safe_error`` to pass through known exception text
but mask unexpected types, and on ``tool_errors`` to return the canonical
error envelope per shape. These assert that contract directly.
"""

from __future__ import annotations

import pytest

from mcp_server import _shared
from veeam_aiops.connection import VeeamApiError

# ─── _safe_error ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_safe_error_passes_through_known_types():
    msg = _shared._safe_error(VeeamApiError("404 not found on /api/v1/jobs"), "job_get")
    assert "404 not found" in msg


@pytest.mark.unit
def test_safe_error_masks_unexpected_types():
    msg = _shared._safe_error(RuntimeError("internal secret detail"), "job_get")
    assert "secret detail" not in msg
    assert msg == "RuntimeError: operation failed."


# ─── tool_errors shapes ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_tool_errors_dict_shape():
    @_shared.tool_errors("dict")
    def boom():
        raise ValueError("bad input")

    out = boom()
    assert out["error"] == "bad input"
    assert "doctor" in out["hint"]


@pytest.mark.unit
def test_tool_errors_list_shape():
    @_shared.tool_errors("list")
    def boom():
        raise ValueError("bad input")

    out = boom()
    assert isinstance(out, list)
    assert out[0]["error"] == "bad input"


@pytest.mark.unit
def test_tool_errors_str_shape():
    @_shared.tool_errors("str")
    def boom():
        raise ValueError("bad input")

    out = boom()
    assert out.startswith("Error: bad input")
    assert "doctor" in out


@pytest.mark.unit
def test_tool_errors_passes_success_through():
    @_shared.tool_errors("dict")
    def ok():
        return {"ok": True}

    assert ok() == {"ok": True}


# ─── lazy connection init ────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_connection_lazily_builds_manager(monkeypatch):
    monkeypatch.setattr(_shared, "_conn_mgr", None)

    sentinel = object()

    class _FakeMgr:
        def __init__(self, cfg):
            self.cfg = cfg

        def connect(self, target):
            return sentinel

    monkeypatch.setattr(_shared, "load_config", lambda path: {"cfg": True})
    monkeypatch.setattr(_shared, "ConnectionManager", _FakeMgr)
    monkeypatch.delenv("VEEAM_AIOPS_CONFIG", raising=False)

    assert _shared._get_connection() is sentinel
    # second call reuses the cached manager (no re-init needed)
    assert _shared._get_connection("lab") is sentinel
    # reset module global so we don't leak the fake into other tests
    monkeypatch.setattr(_shared, "_conn_mgr", None)
