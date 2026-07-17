"""Ops-layer analysis edge cases: overview partial-failure isolation +
near-full/running classification, and repository capacity-state merging.

These hit the best-effort ``except`` branches (one failing sub-query must not
blank the whole overview) and the capacity-percent math, with realistic canned
Veeam rows so the normalisation and thresholds are asserted for real.
"""

from __future__ import annotations

import pytest

from veeam_aiops.ops import overview as overview_ops
from veeam_aiops.ops import repositories as repo_ops


class _Conn:
    """Minimal conn double: canned GET per path-substring, or raises."""

    def __init__(self, responses=None, raise_on=None):
        self.responses = responses or {}
        self.raise_on = raise_on or ()

    def get(self, path, **kwargs):
        for needle in self.raise_on:
            if needle in path:
                raise RuntimeError(f"boom on {path}")
        for key, value in self.responses.items():
            if key in path:
                return value
        return {}


# ─── overview: threshold + running classification ────────────────────────────


@pytest.mark.unit
def test_overview_classifies_near_full_and_running():
    conn = _Conn(
        responses={
            "/api/v1/jobs": {"data": [
                {"id": "j1", "lastResult": "Success", "isDisabled": False},
                {"id": "j2", "lastResult": "Failed", "isDisabled": True},
                {"id": "j3", "isDisabled": False},  # no lastResult -> "" -> Unknown
            ]},
            "/api/v1/backupInfrastructure/repositories/states": {"data": [
                {"id": "r1", "name": "Full", "capacityGB": 1000, "freeGB": 100},  # 90% near full
                {"id": "r2", "name": "Edge", "capacityGB": 1000, "freeGB": 150},  # 85% near (>=)
                {"id": "r3", "name": "Ok", "capacityGB": 1000, "freeGB": 500},  # 50% not
            ]},
            "/api/v1/sessions": {"data": [
                {"id": "s1", "name": "run", "state": "Working"},
                {"id": "s2", "name": "start", "state": "Starting"},
                {"id": "s3", "name": "done", "state": "Success"},
            ]},
        }
    )
    out = overview_ops.health_overview(conn)

    assert out["jobs"]["total"] == 3
    assert out["jobs"]["byLastResult"] == {"Success": 1, "Failed": 1, "Unknown": 1}
    assert out["jobs"]["disabled"] == 1

    near_full_names = {r["name"] for r in out["repositories"]["nearFull"]}
    assert near_full_names == {"Full", "Edge"}  # Ok excluded, 85% boundary included
    assert out["repositories"]["total"] == 3

    running_ids = {r["id"] for r in out["sessions"]["running"]}
    assert running_ids == {"s1", "s2"}  # Success not running
    assert out["sessions"]["recent"] == 3
    assert out["nearFullThresholdPercent"] == 85.0


@pytest.mark.unit
def test_overview_isolates_partial_failures():
    """A failing sub-query is reported as an 'error' entry, others still fill."""
    conn = _Conn(
        responses={
            "/api/v1/sessions": {"data": [{"id": "s1", "name": "run", "state": "Working"}]},
        },
        raise_on=("/api/v1/jobs", "/api/v1/backupInfrastructure/repositories/states"),
    )
    out = overview_ops.health_overview(conn)
    assert "error" in out["jobs"]
    assert "error" in out["repositories"]
    assert "error" not in out["sessions"]
    assert out["sessions"]["running"][0]["id"] == "s1"


# ─── repository capacity-state merging ───────────────────────────────────────


@pytest.mark.unit
def test_get_repository_merges_matching_state():
    conn = _Conn(
        responses={
            "/api/v1/backupInfrastructure/repositories/repo-1": {
                "id": "repo-1", "name": "Main", "type": "WinLocal", "path": "D:\\vbr",
            },
            "/api/v1/backupInfrastructure/repositories/states": {"data": [
                {"id": "repo-1", "capacityGB": 2000, "freeGB": 500},
            ]},
        }
    )
    out = repo_ops.get_repository(conn, "repo-1")
    assert out["capacity"] == 2000
    assert out["free"] == 500
    assert out["usedPercent"] == 75.0  # (2000-500)/2000


@pytest.mark.unit
def test_get_repository_no_matching_state_row():
    conn = _Conn(
        responses={
            "/api/v1/backupInfrastructure/repositories/repo-1": {
                "id": "repo-1", "name": "Main", "type": "WinLocal", "path": "D:\\vbr",
            },
            "/api/v1/backupInfrastructure/repositories/states": {"data": [
                {"id": "other", "capacityGB": 100, "freeGB": 10},
            ]},
        }
    )
    out = repo_ops.get_repository(conn, "repo-1")
    assert "capacity" not in out  # no state merged for a non-matching id


@pytest.mark.unit
def test_get_repository_state_endpoint_failure_is_swallowed():
    conn = _Conn(
        responses={
            "/api/v1/backupInfrastructure/repositories/repo-1": {
                "id": "repo-1", "name": "Main", "type": "WinLocal", "path": "",
            },
        },
        raise_on=("/repositories/states",),
    )
    out = repo_ops.get_repository(conn, "repo-1")
    assert out["id"] == "repo-1"
    assert "capacity" not in out  # advisory state lookup failed -> no capacity keys


@pytest.mark.unit
def test_repository_state_bad_capacity_skips_percent():
    """Non-numeric capacity must not crash the percent math (except path)."""
    conn = _Conn(
        responses={
            "/api/v1/backupInfrastructure/repositories/states": {"data": [
                {"id": "r1", "name": "Bad", "type": "S3",
                 "capacity": "not-a-number", "freeSpace": "x"},
            ]},
        }
    )
    rows = repo_ops.repository_state(conn)
    assert rows[0]["id"] == "r1"
    assert "usedPercent" not in rows[0]  # computation skipped, no crash
