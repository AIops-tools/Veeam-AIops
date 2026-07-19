"""Backup infrastructure inventory for Veeam Backup & Replication (read-only).

Managed servers (the hypervisor hosts and agent machines VBR drives) and backup
proxies (the data movers). These are read-only inventory views to help an agent
understand where a job runs and what moves the data.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.governance import opt_str

_MANAGED_SERVERS = "/api/v1/backupInfrastructure/managedServers"
_PROXIES = "/api/v1/backupInfrastructure/proxies"


def _proxy_host(proxy: dict) -> object | None:
    """Host name of the machine a proxy runs on, nested or flat, else None.

    Returns None (not "") when neither shape carries a host, so an absent host
    stays distinguishable from a host the API reported as empty.
    """
    server = proxy.get("server")
    if isinstance(server, dict):
        return server.get("hostName")
    return proxy.get("serverName")


def list_managed_servers(conn: Any) -> list[dict]:
    """[READ] List managed servers (id, name, type, description)."""
    data = conn.get(_MANAGED_SERVERS)
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for s in items or []:
        out.append(
            {
                "id": opt_str(s.get("id"), 64),
                "name": opt_str(s.get("name"), 128),
                "type": opt_str(s.get("type"), 64),
                "description": opt_str(s.get("description"), 200),
            }
        )
    return out


def list_proxies(conn: Any) -> list[dict]:
    """[READ] List backup proxies (id, name, type, host/server)."""
    data = conn.get(_PROXIES)
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for p in items or []:
        out.append(
            {
                "id": opt_str(p.get("id"), 64),
                "name": opt_str(p.get("name"), 128),
                "type": opt_str(p.get("type"), 64),
                "server": opt_str(_proxy_host(p), 128),
            }
        )
    return out
