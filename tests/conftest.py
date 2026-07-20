"""Shared test doubles for the ops layer (no live Veeam server).

``FakeVeeam`` mimics :class:`veeam_aiops.connection.VeeamConnection`'s surface
(``get``/``post``/``put``/``delete``/``request``). Responses are matched by
substring of the request path, so a single fake can serve the several calls a
flagship view issues, and every outbound call is recorded (method, path,
params/json) for assertions on the real REST contract.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _default_approver(monkeypatch):
    """The policy layer is secure-by-default: with no rules.yaml, high/critical
    governed calls require a named approver. Tests exercising tool behavior
    are not about that gate, so record a synthetic approver globally; the
    governance-persistence tests remove it to test the gate itself."""
    monkeypatch.setenv("VEEAM_AUDIT_APPROVED_BY", "pytest")


class FakeTarget:
    """The slice of ``TargetConfig`` the ops layer reads (the VBR hostname)."""

    def __init__(self, host: str = "") -> None:
        self.host = host


class FakeVeeam:
    """Records every REST call; answers canned JSON per path-substring.

    ``host`` sets the connection's target hostname. It defaults to empty, which
    is what an unconfigured/unknown target looks like — guards that compare
    against it must fail open there rather than matching a blank name.
    """

    def __init__(self, responses: dict[str, Any] | None = None, host: str = "") -> None:
        self.responses = responses or {}
        self.target = FakeTarget(host)
        self.calls: list[tuple[str, str, dict]] = []  # (method, path, kwargs)

    def _match(self, path: str) -> Any:
        for key, value in self.responses.items():
            if key in path:
                return value
        return {}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        return self._match(path)

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    # ── assertion helpers ────────────────────────────────────────────
    def paths(self, method: str | None = None) -> list[str]:
        return [p for (m, p, _k) in self.calls if method is None or m == method]


@pytest.fixture
def fake_veeam():
    return FakeVeeam
