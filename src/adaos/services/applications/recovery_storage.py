"""Application recovery placement, stable path identities and relocation admission.

Only the committed Application recovery root may live outside the private root.
Receipt identities keep their historical logical paths across physical moves.
SQLite read leases allow concurrent lifecycle operations and drain them before
the migration closes admission. The lease database stays outside the moved tree.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from functools import wraps
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from adaos.services.artifact_pipeline.storage import mutation_lock
from adaos.services.node_maintenance.storage import read, volume
from .publication_readiness import PublicationPreconditionFailed


ROLE = "application_recovery"
ADAPTER_VERSION = 1
_access = threading.local()


class ApplicationRecoveryStorage:
    def __init__(self, private_root: Path):
        self.private = Path(private_root).resolve()
        self.logical_root = self.private / "recovery/applications"
        self.state = self.private / "state/maintenance"

    def _gate(self):
        from adaos.services.node_maintenance.migration import gate_paths
        return gate_paths(self.logical_root)

    def _connection(self):
        self.state.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.state / "application-recovery-access.sqlite3",
                                     timeout=.25, isolation_level=None)
        try:
            if connection.execute("PRAGMA journal_mode").fetchone() != ('delete',):
                raise ValueError("Recovery admission requires rollback-journal reader locks")
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='access'").fetchone():
                connection.execute("CREATE TABLE access(id INTEGER PRIMARY KEY CHECK(id=1))")
                connection.execute("INSERT INTO access VALUES(1)")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def exclusive(self):
        """Caller holds the admission mutex until the durable migration gate exists."""
        with closing(self._connection()) as connection:
            try:
                connection.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError as exc:
                raise ValueError("Application recovery is in use; finish its lifecycle operation before relocation") from exc
            try:
                yield
            finally:
                connection.rollback()

    @contextmanager
    def access(self):
        active = getattr(_access, "roots", set())
        key = str(self.private)
        if key in active:
            yield
            return
        gate, lock = self._gate()
        with mutation_lock(lock, timeout_s=2):
            record = read(gate, {})
            if record and record.get("status") not in {"completed", "cancelled"}:
                raise PublicationPreconditionFailed("RecoveryRelocationInProgress")
            connection = self._connection()
            try:
                connection.execute("BEGIN")
                connection.execute("SELECT id FROM access").fetchone()
                self.path(self.logical_root / ".placement-check")
            except BaseException:
                connection.close()
                raise
        _access.roots = active | {key}
        try:
            yield
        finally:
            _access.roots = active
            connection.rollback()
            connection.close()

    def _placement(self):
        settings = read(self.state / "storage-settings.json", {})
        placement = settings.get("roles", {}).get(ROLE, {})
        physical = Path(placement.get("active_path") or self.logical_root).absolute()
        if physical == self.logical_root:
            if self.logical_root.resolve() != self.logical_root:
                raise ValueError("Application recovery root uses an unregistered link")
            return self.logical_root, placement
        marker = read(physical / ".adaos-migration-owner.json", {})
        job_id = marker.get("job_id", "")
        if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("Application recovery placement has no exact migration ownership")
        job = read(self.state / "migrations" / job_id / "job.json", {})
        if (physical.resolve() != physical or not physical.is_dir()
                or placement.get("compatibility_path") != str(self.logical_root)
                or self.logical_root.resolve() != physical
                or job.get("role") != ROLE or job.get("status") != "completed"
                or job.get("node_id") != settings.get("node_id")
                or job.get("source") != str(self.logical_root) or job.get("destination") != str(physical)
                or marker.get("admission_root") != str(self.logical_root)
                or (job.get("recovery_verification") or {}).get("adapter_version") != ADAPTER_VERSION
                or not job.get("content_digest")
                or job.get("copied_bytes") != job.get("bytes") or job.get("verified_bytes") != job.get("bytes")
                or job.get("volume_id") != placement.get("active_volume_id")
                or volume(physical)["id"] != job.get("volume_id")):
            raise ValueError("Application recovery placement is unavailable or its committed identity changed")
        return physical, placement

    def path(self, path: Path) -> Path:
        """Return the stable logical path after checking its physical owner.

        Ordinary runtime data keeps the existing private-root/no-links policy.
        Only the exact committed compatibility root is an admitted alias.
        """
        absolute = Path(path).absolute()
        if ".." in absolute.parts:
            raise ValueError("Application data path contains parent traversal")
        if absolute.is_relative_to(self.private) and not absolute.is_relative_to(self.logical_root):
            if absolute == self.private or absolute.resolve() != absolute:
                raise ValueError("Application data path escaped its private owner root or used a link")
            # Retained originals are recovery evidence, never the active writer.
            placement = read(self.state / "storage-settings.json", {}).get("roles", {}).get(ROLE, {})
            retained = placement.get("retained_source")
            if retained and absolute.is_relative_to(Path(retained)):
                raise ValueError("Retained recovery originals are read-only migration evidence")
            return absolute
        physical, _placement = self._placement()
        if absolute.is_relative_to(self.logical_root):
            relative = absolute.relative_to(self.logical_root)
        elif physical != self.logical_root and absolute.is_relative_to(physical):
            relative = absolute.relative_to(physical)
        else:
            raise ValueError("Application data path escaped its private owner root")
        if not relative.parts or absolute.resolve() != physical / relative:
            raise ValueError("Application recovery path used a nested link or escaped its owner")
        return self.logical_root / relative


def recovery_access(method):
    @wraps(method)
    def admitted(self, *args, **kwargs):
        root = self.private if hasattr(self, "private") else self.root
        with ApplicationRecoveryStorage(root).access():
            return method(self, *args, **kwargs)
    return admitted


def verify_relocated_points(job, ledger, deadline):
    """Bounded restore-path/identity verification after the byte verification.

    Legacy evidence remains unclassified and retained; copying it never asserts
    that every historical archive has a usable business-data restore adapter.
    """
    checked = 0
    while time.monotonic() < deadline and checked < 200:
        row = ledger.execute("SELECT rowid,path,kind FROM entries WHERE rowid>? ORDER BY rowid LIMIT 1",
                             (job.get("recovery_cursor", 0),)).fetchone()
        if row is None:
            job["recovery_verification"] = {"adapter_version": ADAPTER_VERSION,
                "registered_points": job.get("registered_recovery_points", 0),
                "unclassified_points_retained": job.get("unclassified_recovery_points", 0),
                "scope": "native_lifecycle_path_identity; content_verified_by_chunk_ledger"}
            job["phase"] = "seal"
            return
        cursor, relative, kind = row
        if kind == "dir" and '/' not in relative:
            point = Path(job["destination"]) / relative
            marker = point / ".adaos-recovery.json"
            if not marker.exists():
                job["unclassified_recovery_points"] = job.get("unclassified_recovery_points", 0) + 1
            else:
                from adaos.services.node_maintenance.migration import _plain
                if _plain(marker).st_size > 65536:
                    raise ValueError("Recovery point metadata exceeds its bounded native contract")
                metadata = read(marker, {})
                if metadata.get("schema") != "adaos.recovery.point.v1" or metadata.get("adapter") != "applications.lifecycle":
                    raise ValueError("Recovery point has an unsupported native identity")
                identity = [metadata.get(k) for k in ("application_id", "candidate_id", "release_digest")]
                if not all(isinstance(value, str) and value for value in identity) or hashlib.sha256(json.dumps(identity).encode()).hexdigest() != relative:
                    raise ValueError("Recovery point identity does not match its immutable directory")
                job["registered_recovery_points"] = job.get("registered_recovery_points", 0) + 1
        job["recovery_cursor"] = cursor
        checked += 1
