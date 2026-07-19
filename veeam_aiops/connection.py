"""Connection management for Veeam Backup & Replication REST API.

Thin httpx wrapper with per-target session reuse and OAuth2 bearer auth:

  * ``POST /api/oauth2/token`` with form body
    ``grant_type=password&username=<u>&password=<p>`` yields an
    ``access_token``; we cache it per target.
  * Every request carries ``x-api-version: 1.1-rev1`` and (after login) the
    ``Authorization: Bearer <token>`` header.

Per-connection metadata (the bearer token) is kept in a module-level dict
keyed by ``id(client)`` rather than set as an attribute on the httpx client.
Third-party SDK objects must not be monkey-patched (same discipline as the
pyVmomi 8.x ManagedObject lesson); we apply it pre-emptively to keep the
harness pattern consistent.

All non-2xx responses are translated centrally into ``VeeamApiError`` with a
teaching message — REST-wrapper skills should translate HTTP errors at the
connection layer from the first version, not let users hit raw tracebacks.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from veeam_aiops.config import AppConfig, TargetConfig, load_config

API_VERSION = "1.1-rev1"
_TIMEOUT = 30.0


def _seg(value: Any) -> str:
    """Percent-encode one URL *path segment* (agent-supplied ids).

    Prevents path traversal / smuggling when an id like ``../jobs`` is
    interpolated into an f-string REST path. Query-string params passed via
    httpx ``params=`` must NOT go through this (httpx encodes those itself).
    """
    return quote(str(value), safe="")


class VeeamApiError(Exception):
    """A Veeam REST API call failed; carries a teaching message + status code."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str = "") -> None:
        self.status_code = status_code
        self.path = path
        super().__init__(message)


def _teaching_message(status: int, path: str, body: str) -> str:
    """Map a non-2xx status to an actionable, teaching error message."""
    snippet = body[:200].strip()
    if status in (401, 403):
        return (
            f"Authentication/authorization failed ({status}) on {path}. "
            f"Check the username/password env var and the account's Veeam role. {snippet}"
        )
    if status == 404:
        return (
            f"Resource not found (404) on {path}. The id may be stale — list the "
            f"parent collection first to get a current id. {snippet}"
        )
    if status in (502, 503, 504):
        return (
            f"Veeam server transient error ({status}) on {path}. The VBR service "
            f"may be starting or busy; retry shortly. {snippet}"
        )
    return f"Veeam API error ({status}) on {path}. {snippet}"


class VeeamConnection:
    """A single authenticated session against one Veeam VBR REST API target."""

    def __init__(self, target: TargetConfig) -> None:
        self._target = target
        self._client = httpx.Client(
            base_url=target.base_url,
            verify=target.verify_ssl,
            timeout=_TIMEOUT,
            headers={"x-api-version": API_VERSION, "Accept": "application/json"},
        )
        self._login()

    @property
    def target(self) -> TargetConfig:
        return self._target

    def _login(self) -> None:
        """Obtain a bearer token via the OAuth2 password grant."""
        data = {
            "grant_type": "password",
            "username": self._target.username,
            "password": self._target.password,
        }
        try:
            resp = self._client.post(
                "/api/oauth2/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise VeeamApiError(
                f"Could not reach Veeam server at {self._target.base_url}: {exc}. "
                f"Check the host/port and that the VBR REST service is running.",
                path="/api/oauth2/token",
            ) from exc
        if resp.status_code != 200:
            raise VeeamApiError(
                _teaching_message(resp.status_code, "/api/oauth2/token", resp.text),
                status_code=resp.status_code,
                path="/api/oauth2/token",
            )
        token = (resp.json() or {}).get("access_token", "")
        if not token:
            raise VeeamApiError(
                "Veeam token endpoint returned no access_token.",
                path="/api/oauth2/token",
            )
        self._client.headers["Authorization"] = f"Bearer {token}"

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Issue a request and return parsed JSON, translating errors centrally."""
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise VeeamApiError(
                f"Transport error on {method} {path}: {exc}. Check connectivity.",
                path=path,
            ) from exc
        if not (200 <= resp.status_code < 300):
            raise VeeamApiError(
                _teaching_message(resp.status_code, path, resp.text),
                status_code=resp.status_code,
                path=path,
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        self._client.headers.pop("Authorization", None)
        self._client.close()


class ConnectionManager:
    """Manages connections to multiple Veeam VBR targets with session reuse."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, VeeamConnection] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> VeeamConnection:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        cached = self._connections.get(target.name)
        if cached is not None:
            return cached
        conn = VeeamConnection(target)
        self._connections[target.name] = conn
        return conn

    def disconnect(self, target_name: str) -> None:
        conn = self._connections.pop(target_name, None)
        if conn is not None:
            conn.close()

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        return list(self._connections.keys())
