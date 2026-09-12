"""Shared plumbing for the backup storage footprint reads.

Everything that decides *whose* bytes a backup file is lives here, so the
per-object usage (``footprint``) and the fleet ranking (``ranking``) cannot
drift apart on it.
"""

from __future__ import annotations

from typing import Any

from veeam_aiops.connection import VeeamApiError, _seg
from veeam_aiops.governance import opt_str
from veeam_aiops.ops._paging import fetch_all
from veeam_aiops.ops._revision import MINIMUM, UnsupportedServer

_KIND_BY_RP_TYPE = {"full": "full", "increment": "incremental", "rollback": "rollback"}
_KIND_BY_EXTENSION = {".vbk": "full", ".vib": "incremental", ".vrb": "rollback"}
KINDS = ("full", "incremental", "rollback", "other")
_IDENTITY_KEYS = ("path", "objectId", "biosUuid", "computerId")

BLOCK_CLONE_CAVEAT = (
    "storedBytes sums Veeam's per-file backupSize. On block-clone repositories "
    "(ReFS / XFS fast clone) synthetic fulls share blocks with earlier files, so "
    "the sum can exceed the space the machine actually consumes there — treat it "
    "as an upper bound on those repositories."
)
SHARED_CAVEAT = (
    "Some files store several machines (per-job backup chain). Their bytes are in "
    "sharedStoredBytes and are NOT included in storedBytes: Veeam does not report "
    "a per-machine split of a shared file."
)
SINGLE_OWNER_CAVEAT = (
    "This server's REST revision names one owner per backup file. Per-object usage "
    "still recognises a shared file by the restore points it stores; where the "
    "server lists none, a file naming this machine as its owner is charged to it. "
    "The ranking reads no restore points and charges every file to its listed "
    "owner. Revision 1.3-rev2 (VBR 13.1+) lists every owner."
)
UNATTRIBUTED_CAVEAT = (
    "Some files hold this machine's restore points but the server states their "
    "ownership inconsistently (no owner listed, or a single owner that is a "
    "different object, with nothing showing another machine's points in them). "
    "They are in unattributedStoredBytes and NOT in storedBytes. If storedBytes is "
    "0 while these are large, report it with the VBR build: file owner ids may not "
    "match backup-object ids on this build."
)
UNMATCHED_OWNER_CAVEAT = (
    "Some backup files name an owner id that their own backup's object listing "
    "does not contain, so their bytes are charged to no machine (they are in "
    "unmatchedOwnerStoredBytes, not in any storedBytes). If that total is large, "
    "treat this ranking as unsafe for chargeback and report it with the VBR "
    "build: backup-file owner ids and backup-object ids do not match on it. "
    "Files naming no owner at all are ordinary per-job chain files and are "
    "counted apart, as ownerlessStoredBytes."
)
UNREADABLE_CAVEAT = (
    "Some backups could not be read (see unreadableBackups); their bytes are not "
    "in any total."
)
NAME_IDENTITY_CAVEAT = (
    "Some machines are identified by name only — this server's REST revision "
    "exposes no inventory id for their platform — so same-named machines on that "
    "platform would be merged."
)


def as_int(value: Any) -> int | None:
    """An int64 size as ``int``; ``None`` when absent or not integral."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def owners(backup_file: dict) -> tuple[list[str], str]:
    """Backup-object ids that own a file, and which field reported them."""
    ids = backup_file.get("objectIds")
    if isinstance(ids, list):
        return [str(i) for i in ids if i], "objectIds"
    single = backup_file.get("objectId")
    if single:
        return [str(single)], "objectId"
    return [], "none"


def file_points(backup_file: dict) -> set[str] | None:
    """Restore-point ids stored in a file; ``None`` when the server did not say.

    The published spec names the property ``restorePointIds`` but lists
    ``restorePointId`` as required in revisions 1.2-rev0 to 1.3-rev0, so both
    spellings are read rather than trusting either half of that contradiction.
    """
    many = backup_file.get("restorePointIds")
    if isinstance(many, list):
        return {str(i) for i in many if i}
    one = backup_file.get("restorePointId")
    if one:
        return {str(one)}
    return None


def rp_kind(restore_point: dict) -> str:
    return _KIND_BY_RP_TYPE.get(str(restore_point.get("type") or "").lower(), "other")


def file_kind(backup_file: dict, rp_kinds: dict[str, str]) -> tuple[str, str]:
    """(kind, basis) for one file — restore-point type first, extension second."""
    kind = rp_kinds.get(str(backup_file.get("id") or ""))
    if kind:
        return kind, "restorePointType"
    name = str(backup_file.get("name") or "").lower()
    for extension, by_ext in _KIND_BY_EXTENSION.items():
        if name.endswith(extension):
            return by_ext, "fileExtension"
    return "other", "unknown"


def identity(obj: dict) -> tuple[str, str]:
    """A key that tells two same-named machines apart (vCenter path, moref, …).

    Falls back to platform + name when the revision exposes no inventory id for
    the platform (Hyper-V and agents before 1.3-rev2): the per-backup object id
    would split one machine into one row per backup, which misreports a single
    machine as several and can push it out of a top-N.
    """
    platform = obj.get("platformName") or "?"
    for key in _IDENTITY_KEYS:
        value = obj.get(key)
        if value:
            return f"{platform}:{value}", key
    name = str(obj.get("name") or "").strip().lower()
    if name:
        return f"{platform}:name:{name}", "name"
    return f"backupObject:{obj.get('id')}", "backupObjectId"


def classify(backup_file: dict, object_ids: set[str], file_ids: set[str],
             point_ids: set[str]) -> str | None:
    """``"mine"``, ``"shared"``, ``"unattributed"``, or ``None`` (not this machine's).

    A file touches the machine when one of the machine's restore points lives in
    it (by the point's ``backupFileId`` or the file's own point list) or the
    machine is among its owners. Then, strongest evidence first:

      * the file stores restore points that are not this machine's, or lists
        several owners beyond this machine → **shared** (never charged);
      * every listed owner is this machine → **mine**;
      * no owner listed, but the file's point list is all this machine's →
        **mine**;
      * otherwise (a single owner that is someone else, or no owner and no point
        list) the server contradicts itself or says nothing → **unattributed**
        (never charged, reported so an id-namespace mismatch stays visible).
    """
    listed, _ = owners(backup_file)
    points = file_points(backup_file)
    referenced = str(backup_file.get("id") or "") in file_ids
    if not (referenced or object_ids.intersection(listed)
            or (points and points & point_ids)):
        return None
    if points and points - point_ids:
        return "shared"
    if len(listed) > 1 and not set(listed) <= object_ids:
        return "shared"
    if listed and set(listed) <= object_ids:
        return "mine"
    if not listed and points:
        return "mine"
    return "unattributed"


class Session:
    """Per-call caches and the negotiated header for one footprint read."""

    def __init__(self, conn: Any, negotiated: dict) -> None:
        self.conn = conn
        self.negotiated = negotiated
        self.headers = {"x-api-version": negotiated["revision"]}
        self._files: dict[str, list[dict]] = {}
        self._repos: dict[str, str | None] | None = None
        self.repository_error: str | None = None

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.conn.get(path, headers=self.headers, **kwargs)

    def all(self, path: str, **kwargs: Any) -> list[dict]:
        return fetch_all(self.conn, path, headers=self.headers, **kwargs)

    def files(self, backup_id: str) -> list[dict]:
        if backup_id not in self._files:
            try:
                self._files[backup_id] = self.all(f"/api/v1/backups/{_seg(backup_id)}/backupFiles")
            except VeeamApiError as exc:
                # Only an unproven revision turns a refusal into "server too old":
                # under "probe" the server already accepted the revision, so a
                # 404 here is about this backup, not the build.
                if exc.status_code in (400, 404) and self.negotiated["basis"] == "assumed":
                    raise UnsupportedServer(
                        f"The server did not serve backup-file sizes (HTTP "
                        f"{exc.status_code}). They need VBR 12.3+ (REST "
                        f"{MINIMUM[0]}); this server's build could not be read "
                        f"({self.negotiated['buildError']}), so a Backup "
                        f"Administrator account running 'veeam-aiops doctor' can "
                        f"confirm the build."
                    ) from exc
                raise
        return self._files[backup_id]

    def repository_name(self, repository_id: str | None) -> str | None:
        if not repository_id:
            return None
        if self._repos is None:
            try:
                rows = self.all("/api/v1/backupInfrastructure/repositories")
            except Exception as exc:  # noqa: BLE001 — a name is a label; kept as an error field
                rows = []
                self.repository_error = str(exc)[:200]
            self._repos = {str(r.get("id")): opt_str(r.get("name"), 128) for r in rows}
        return self._repos.get(str(repository_id))
