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
    """The approver is an optional audit annotation now, not a gate: record a
    synthetic one globally so audit rows carry a who; the governance-persistence
    tests remove it to prove a high-risk write runs without one."""
    monkeypatch.setenv("VEEAM_AUDIT_APPROVED_BY", "pytest")


READ_METHODS = ("GET", "HEAD", "OPTIONS")


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

    def mutating(self) -> list[str]:
        """Every recorded call that could change server state.

        Used to assert the one rule a dry-run must obey: it MAY read (a preview
        that cannot read cannot say whether the real call would be refused), it
        must never write. An allowlist of read verbs rather than a denylist of
        POST/PUT/PATCH/DELETE, so a verb this fake learns later counts as
        mutating until someone deliberately decides otherwise.
        """
        return [f"{m} {p}" for (m, p, _k) in self.calls if m not in READ_METHODS]


@pytest.fixture
def fake_veeam():
    return FakeVeeam
