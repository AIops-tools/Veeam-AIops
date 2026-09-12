"""Connection-layer coverage: OAuth2 login, central error translation, the
per-segment id encoder, and ConnectionManager session reuse/teardown.

httpx.Client is replaced by a scripted fake so no live Veeam server is needed;
assertions target the real contract — the teaching-error mapping per HTTP
status, the bearer stashing, empty/non-JSON body handling, and manager caching.
"""

from __future__ import annotations

import httpx
import pytest

from veeam_aiops.config import AppConfig, TargetConfig
from veeam_aiops.connection import (
    ConnectionManager,
    VeeamApiError,
    VeeamConnection,
    _seg,
    _teaching_message,
)


class _Resp:
    def __init__(self, status, payload=None, content=b"{}", text="body"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = text

    def json(self):
        if self._payload == "raise":
            raise ValueError("not json")
        return self._payload


def _target(monkeypatch, name="lab"):
    monkeypatch.setenv(f"VEEAM_{name.upper()}_PASSWORD", "secret")
    return TargetConfig(name=name, host="vbr.local", username="admin", verify_ssl=False)


# ─── _seg path-segment encoder ───────────────────────────────────────────────


@pytest.mark.unit
def test_seg_encodes_traversal_and_slashes():
    assert _seg("../jobs") == "..%2Fjobs"
    assert _seg("a b/c") == "a%20b%2Fc"
    assert _seg("plain") == "plain"


# ─── teaching-message mapping per status ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "status, needle",
    [
        (401, "Authentication/authorization failed"),
        (403, "Authentication/authorization failed"),
        (404, "Resource not found"),
        (502, "transient error"),
        (503, "transient error"),
        (504, "transient error"),
        (500, "Veeam API error (500)"),
    ],
)
def test_teaching_message_maps_status(status, needle):
    msg = _teaching_message(status, "/api/v1/x", "detail body here")
    assert needle in msg
    assert "/api/v1/x" in msg


# ─── login paths ─────────────────────────────────────────────────────────────


def _install_client(monkeypatch, *, token_resp=None, request_fn=None, post_raises=False):
    class _Client:
        def __init__(self, *a, **k):
            self.headers = {}
            self.closed = False

        def post(self, path, **k):
            if post_raises:
                raise httpx.ConnectError("refused")
            return token_resp or _Resp(200, {"access_token": "TOK"})

        def request(self, method, path, **k):
            if request_fn:
                return request_fn(method, path, **k)
            return _Resp(200, {"data": []}, content=b"{}")

        def close(self):
            self.closed = True

    monkeypatch.setattr(httpx, "Client", _Client)


@pytest.mark.unit
def test_login_success_sets_bearer_header(monkeypatch):
    _install_client(monkeypatch)
    conn = VeeamConnection(_target(monkeypatch))
    # The Authorization header IS the auth mechanism — assert it directly rather
    # than a side cache, so the test fails if login stops authenticating.
    assert conn._client.headers["Authorization"] == "Bearer TOK"


@pytest.mark.unit
def test_login_transport_error_becomes_teaching_error(monkeypatch):
    _install_client(monkeypatch, post_raises=True)
    with pytest.raises(VeeamApiError) as ei:
        VeeamConnection(_target(monkeypatch))
    assert "Could not reach Veeam server" in str(ei.value)
    assert ei.value.path == "/api/oauth2/token"


@pytest.mark.unit
def test_login_non_200_raises(monkeypatch):
    _install_client(monkeypatch, token_resp=_Resp(401, text="bad creds"))
    with pytest.raises(VeeamApiError) as ei:
        VeeamConnection(_target(monkeypatch))
    assert ei.value.status_code == 401
    assert "Authentication/authorization failed" in str(ei.value)


@pytest.mark.unit
def test_login_missing_token_raises(monkeypatch):
    _install_client(monkeypatch, token_resp=_Resp(200, {"no_token": True}))
    with pytest.raises(VeeamApiError) as ei:
        VeeamConnection(_target(monkeypatch))
    assert "no access_token" in str(ei.value)


# ─── request paths ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_request_transport_error_translated(monkeypatch):
    # Was written with ReadTimeout standing in for "some transport error", which
    # quietly made the merged handling the specification. A timeout now has its
    # own answer, so this case uses a fault that really is about reachability.
    def _boom(method, path, **k):
        raise httpx.ConnectError("refused")

    _install_client(monkeypatch, request_fn=_boom)
    conn = VeeamConnection(_target(monkeypatch))
    with pytest.raises(VeeamApiError) as ei:
        conn.get("/api/v1/jobs")
    assert "Transport error" in str(ei.value)


@pytest.mark.unit
def test_request_empty_body_returns_empty_dict(monkeypatch):
    _install_client(
        monkeypatch,
        request_fn=lambda m, p, **k: _Resp(204, content=b""),
    )
    conn = VeeamConnection(_target(monkeypatch))
    assert conn.post("/api/v1/jobs/j1/start") == {}


@pytest.mark.unit
def test_request_non_json_body_returns_empty_dict(monkeypatch):
    _install_client(
        monkeypatch,
        request_fn=lambda m, p, **k: _Resp(200, payload="raise", content=b"<html>"),
    )
    conn = VeeamConnection(_target(monkeypatch))
    assert conn.get("/api/v1/jobs") == {}


@pytest.mark.unit
def test_request_error_status_translated(monkeypatch):
    _install_client(
        monkeypatch,
        request_fn=lambda m, p, **k: _Resp(503, content=b"x"),
    )
    conn = VeeamConnection(_target(monkeypatch))
    with pytest.raises(VeeamApiError) as ei:
        conn.get("/api/v1/jobs")
    assert ei.value.status_code == 503


@pytest.mark.unit
def test_close_clears_credential_and_closes_client(monkeypatch):
    _install_client(monkeypatch)
    conn = VeeamConnection(_target(monkeypatch))
    client = conn._client
    assert client.headers["Authorization"] == "Bearer TOK"
    conn.close()
    assert "Authorization" not in client.headers
    assert client.closed is True


# ─── ConnectionManager ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_manager_connect_caches_and_reuses(monkeypatch):
    _install_client(monkeypatch)
    t1 = _target(monkeypatch, "lab")
    t2 = _target(monkeypatch, "prod")
    mgr = ConnectionManager(AppConfig(targets=(t1, t2)))

    default = mgr.connect()  # default_target == first
    assert default.target.name == "lab"
    again = mgr.connect("lab")
    assert again is default  # cached, no re-login
    other = mgr.connect("prod")
    assert other is not default

    assert set(mgr.list_targets()) == {"lab", "prod"}
    assert set(mgr.list_connected()) == {"lab", "prod"}


@pytest.mark.unit
def test_manager_disconnect_and_disconnect_all(monkeypatch):
    _install_client(monkeypatch)
    t1 = _target(monkeypatch, "lab")
    t2 = _target(monkeypatch, "prod")
    mgr = ConnectionManager(AppConfig(targets=(t1, t2)))
    mgr.connect("lab")
    mgr.connect("prod")

    mgr.disconnect("lab")
    assert mgr.list_connected() == ["prod"]
    mgr.disconnect("missing")  # no-op, must not raise
    mgr.disconnect_all()
    assert mgr.list_connected() == []


@pytest.mark.unit
def test_manager_from_config_uses_loader(monkeypatch):
    _install_client(monkeypatch)
    cfg = AppConfig(targets=(_target(monkeypatch, "lab"),))
    mgr = ConnectionManager.from_config(cfg)
    assert mgr.list_targets() == ["lab"]


# ─── slow endpoint vs unreachable server ─────────────────────────────────────


@pytest.mark.unit
def test_read_timeout_is_not_reported_as_a_connectivity_problem(monkeypatch):
    """A slow endpoint and an unreachable server need different answers.

    On a large VBR installation ``/jobs`` and ``/sessions`` can exceed any
    client budget while every other endpoint on the same authenticated session
    answers in seconds (field report on issue #2: repositories 2.6 s, infra
    servers 13.3 s, jobs timing out at 120 s for a single record). The old
    wording — "Transport error ... Check connectivity" — sends the operator to
    diagnose a network that is demonstrably fine, and the reporter had to run
    his own httpx experiment to rule our client out.
    """
    def _timeout(method, path, **k):
        raise httpx.ReadTimeout("timed out")

    _install_client(monkeypatch, request_fn=_timeout)
    conn = VeeamConnection(_target(monkeypatch))
    with pytest.raises(VeeamApiError) as ei:
        conn.get("/api/v1/jobs")
    msg = str(ei.value)
    assert "timed out" in msg.lower()
    assert "30" in msg                        # names the budget that was spent
    assert "Check connectivity" not in msg    # the wrong headline
    assert "timeout" in msg.lower()           # names the knob to raise


@pytest.mark.unit
def test_a_real_transport_failure_still_says_check_connectivity(monkeypatch):
    """Positive control: narrowing the timeout case must not blunt the other."""
    def _refused(method, path, **k):
        raise httpx.ConnectError("refused")

    _install_client(monkeypatch, request_fn=_refused)
    conn = VeeamConnection(_target(monkeypatch))
    with pytest.raises(VeeamApiError) as ei:
        conn.get("/api/v1/jobs")
    assert "Check connectivity" in str(ei.value)


@pytest.mark.unit
def test_timeout_is_configurable_per_target(monkeypatch):
    """The 30 s budget was hardcoded with no way to override it.

    Same shape as the hardcoded ``https://`` this line already fixed: the value
    is defensible, having no knob is not.
    """
    seen: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            seen.update(k)
            self.headers = {}

        def post(self, path, **k):
            return _Resp(200, {"access_token": "TOK"})

        def request(self, method, path, **k):
            return _Resp(200, {})

        def close(self):
            pass

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setenv("VEEAM_SLOW_PASSWORD", "secret")
    target = TargetConfig(name="slow", host="vbr.local", username="admin",
                          verify_ssl=False, timeout=180.0)
    VeeamConnection(target)
    assert seen["timeout"] == 180.0


@pytest.mark.unit
def test_timeout_defaults_to_thirty_seconds(monkeypatch):
    seen: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            seen.update(k)
            self.headers = {}

        def post(self, path, **k):
            return _Resp(200, {"access_token": "TOK"})

        def request(self, method, path, **k):
            return _Resp(200, {})

        def close(self):
            pass

    monkeypatch.setattr(httpx, "Client", _Client)
    VeeamConnection(_target(monkeypatch))
    assert seen["timeout"] == 30.0
