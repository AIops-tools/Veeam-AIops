"""Backup infrastructure inventory for Veeam Backup & Replication (read-only).

Managed servers (the hypervisor hosts and agent machines VBR drives) and backup
proxies (the data movers). These are read-only inventory views to help an agent
understand where a job runs and what moves the data.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.governance import sanitize

_MANAGED_SERVERS = "/api/v1/backupInfrastructure/managedServers"
_PROXIES = "/api/v1/backupInfrastructure/proxies"


def list_managed_servers(conn: Any) -> list[dict]:
    """[READ] List managed servers (id, name, type, description)."""
    data = conn.get(_MANAGED_SERVERS)
    items = data.get("data", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for s in items or []:
        out.append(
            {
                "id": sanitize(str(s.get("id", "")), 64),
                "name": sanitize(str(s.get("name", "")), 128),
                "type": sanitize(str(s.get("type", "")), 64),
                "description": sanitize(str(s.get("description", "")), 200),
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
                "id": sanitize(str(p.get("id", "")), 64),
                "name": sanitize(str(p.get("name", "")), 128),
                "type": sanitize(str(p.get("type", "")), 64),
                "server": sanitize(str(p.get("server", {}).get("hostName", "") if isinstance(
                    p.get("server"), dict) else p.get("serverName", "")), 128),
            }
        )
    return out
