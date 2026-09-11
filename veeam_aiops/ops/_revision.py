"""Pick the REST revision a size read can use on *this* VBR server.

The connection pins ``x-api-version: 1.1-rev1`` so every existing read keeps
working on VBR 12.1+. Per-file backup sizes live somewhere that revision cannot
reach: ``GET /api/v1/backups/{id}/backupFiles`` first appears in revision
**1.2-rev0**, and revision 1.3-rev2 changed its shape (``objectId`` became the
array ``objectIds``, because one file can hold several machines). So the size
reads send their own header, chosen from the server's reported build.

The table is Veeam's own "REST API Revisions" table (REST API reference
1.3-rev2, Overview → Versioning): the build each revision first shipped in. A
server serves every revision released at or before its build — the API is
documented as backward-compatible.
"""

from __future__ import annotations

from typing import Any

# (revision, first VBR build that serves it), newest first. Only the revisions
# that carry backupFiles are listed; older ones cannot answer a size question.
REVISIONS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("1.3-rev2", (13, 1, 0, 411)),
    ("1.3-rev1", (13, 0, 1, 180)),
    ("1.3-rev0", (13, 0, 0, 4967)),
    ("1.2-rev1", (12, 3, 1, 1139)),
    ("1.2-rev0", (12, 3, 0, 310)),
)
MINIMUM = REVISIONS[-1]


class UnsupportedServer(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """The VBR server is too old to report per-file backup sizes."""


def parse_build(value: Any) -> tuple[int, ...] | None:
    """``"12.3.1.1139"`` → ``(12, 3, 1, 1139)``; ``None`` when not a dotted build."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def revision_for_build(build: tuple[int, ...]) -> str | None:
    """Newest size-capable revision this build serves, or ``None`` if none."""
    for revision, first_build in REVISIONS:
        if build >= first_build:
            return revision
    return None


def probe(conn: Any) -> tuple[str | None, list[dict]]:
    """Newest size-capable revision the server accepts, tried newest first.

    Used when the build cannot be read: ``/api/v1/serverInfo`` is Backup
    Administrator only from revision 1.1-rev2 on, so a least-privilege Backup
    Viewer account gets 403 there while every read these tools need is open to
    it. ``GET /api/v1/backups`` is open to every read role, so each candidate
    revision is offered on a one-item read and the first one answered wins.
    An auth failure stops the probe — it would fail every revision alike.
    Every refusal is returned too, so a revision skipped because of a transient
    5xx is visible rather than silently traded for an older one.
    """
    refusals: list[dict] = []
    for revision, _ in REVISIONS:
        try:
            conn.get("/api/v1/backups", params={"skip": 0, "limit": 1},
                     headers={"x-api-version": revision})
        except Exception as exc:  # noqa: BLE001 — a refusal means "try the next one"
            status = getattr(exc, "status_code", None)
            refusals.append({"revision": revision, "status": status, "error": str(exc)[:120]})
            if status in (401, 403):
                return None, refusals
            continue
        return revision, refusals
    return None, refusals


def negotiate(conn: Any) -> dict:
    """Choose the revision for size reads from the server's build.

    Returns ``{"revision", "buildVersion", "basis", "buildError", "probeRefusals"}``. ``basis``
    is ``"serverInfo"`` when the build was read and matched; ``"probe"`` when it
    could not be read and a revision was accepted by the server itself; or
    ``"assumed"`` when neither worked — then the oldest size-capable revision is
    sent and the size read reports whatever the server says. ``buildError`` says
    why the build could not be read (``None`` otherwise); ``probeRefusals`` lists
    the revisions the probe offered and the server refused (empty otherwise).

    Raises :class:`UnsupportedServer` when the build is readable and predates
    every revision that serves backup-file sizes.
    """
    try:
        info = conn.get("/api/v1/serverInfo")
        raw = info.get("buildVersion") if isinstance(info, dict) else None
        build = parse_build(raw)
        problem = None if build else "serverInfo carried no dotted buildVersion"
    except Exception as exc:  # noqa: BLE001 — fall back to asking the server
        raw, build, problem = None, None, str(exc)[:200]
    if build is None:
        probed, refusals = probe(conn)
        return {"revision": probed or MINIMUM[0],
                "buildVersion": raw if isinstance(raw, str) else None,
                "basis": "probe" if probed else "assumed", "buildError": problem,
                "probeRefusals": refusals}
    revision = revision_for_build(build)
    if revision is None:
        first = ".".join(str(n) for n in MINIMUM[1])
        raise UnsupportedServer(
            f"This VBR server reports build {raw}. Per-file backup sizes "
            f"(GET /api/v1/backups/{{id}}/backupFiles) were added in REST API "
            f"revision {MINIMUM[0]}, which first shipped in VBR {first} "
            f"(Veeam's published revision table). Upgrade the backup server to "
            f"read backup storage usage through the REST API."
        )
    return {"revision": revision, "buildVersion": raw, "basis": "serverInfo", "buildError": None,
            "probeRefusals": []}
