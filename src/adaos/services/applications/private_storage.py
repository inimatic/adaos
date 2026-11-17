"""Operator-enrolled, exact-release Trial placement on a private native volume.

This is local operational configuration, never a tool/API path argument. No
symlinks, existing Trial relocation, existing data overmount, or fallback to the
system disk is allowed. Volume-backed recovery is retained by default, outside
generic derived-artifact garbage collection.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import stat

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock, sync_directory
from .sqlite_data_transition import SQLiteTransitionPolicy


class PrivateStorageError(ValueError):
    pass


def _segment(value: str) -> str:
    if not value or not all(c.isalnum() or c in '-_' for c in value):
        raise PrivateStorageError('Unsafe private storage identity')
    return value


def _read(path: Path) -> dict:
    if path.absolute() != path.resolve() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise PrivateStorageError('Private storage record is linked, missing or oversized')
    # Only the local runtime owner may enroll/change this operational authority.
    if os.name != 'posix':
        raise PrivateStorageError('Native private storage profiles require POSIX ownership')
    for entry in (path, *path.parents):
        info = entry.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise PrivateStorageError('Private storage authority is not owner-controlled')
        if entry == path.parent.parent.parent.parent:
            break
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise PrivateStorageError('Invalid private storage record')
    return value


def _allocated(root: Path) -> int:
    total = 0
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            child = Path(directory) / name
            info = child.lstat()
            count += 1
            if count > 100_000 or stat.S_ISLNK(info.st_mode):
                raise PrivateStorageError('Private volume inventory is linked or exceeds its bounded envelope')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise PrivateStorageError('Unsupported private volume entry')
            total += info.st_blocks * 512
    return total + root.stat().st_blocks * 512


@dataclass(frozen=True)
class CandidateStorage:
    private: Path
    record: dict

    @property
    def volume(self) -> Path:
        relative = PurePosixPath(str(self.record['volume_relative']))
        if len(relative.parts) != 2 or relative.parts[0] != 'volumes':
            raise PrivateStorageError('Private volume must be a direct enrolled volume')
        _segment(relative.parts[1])
        return self.private.joinpath(*relative.parts)

    @property
    def candidate_root(self) -> Path:
        return self.volume / 'candidates' / _segment(self.record['candidate_id'])

    @property
    def workspace(self) -> Path:
        return self.candidate_root / 'workspace'

    @property
    def recovery(self) -> Path:
        return self.candidate_root / 'recovery'

    def rollback(self, trial_id: str) -> Path:
        path = self.candidate_root / 'trial-rollbacks' / _segment(trial_id) / 'workspace'
        if path.absolute() != path.resolve():
            raise PrivateStorageError('Private rollback archive traverses a link')
        return path

    @property
    def policy(self) -> SQLiteTransitionPolicy:
        return SQLiteTransitionPolicy(**self.record['sqlite_policy'])

    def verify_volume(self) -> None:
        if os.name != 'posix':
            raise PrivateStorageError('Native private storage profiles require POSIX ownership')
        volume = self.volume
        if volume.absolute() != volume.resolve() or not volume.is_dir() or not volume.is_mount():
            raise PrivateStorageError('Enrolled private volume is missing; system-disk fallback is forbidden')
        info = volume.stat()
        identity = self.record['volume_identity']
        if (info.st_ino != identity['inode'] or os.statvfs(volume).f_fsid != identity['fsid']
                or info.st_uid != os.geteuid() or info.st_gid != identity['gid']
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise PrivateStorageError('Enrolled private volume identity or permissions changed')
        if volume.stat().st_dev == self.private.stat().st_dev:
            raise PrivateStorageError('Private application storage must use a separate filesystem')
        for path in (self.candidate_root, self.workspace, self.recovery):
            if path.absolute() != path.resolve():
                raise PrivateStorageError('Private candidate placement traverses a link')

    def admit_write(self, target: Path, reserve_bytes: int = 0) -> None:
        """Conservative preflight, not a substitute for all-writer reservations."""
        self.verify_volume()
        if type(reserve_bytes) is not int or reserve_bytes < 0:
            raise PrivateStorageError('Invalid private storage reservation')
        target = target.absolute()
        if target != target.resolve() or not target.is_relative_to(self.private):
            raise PrivateStorageError('Private storage write escaped the owner')
        budget = self.record['budget']
        integers = ('external_reserved_bytes', 'system_floor_bytes', 'volume_floor_bytes')
        if any(type(budget.get(key)) is not int or budget[key] < 0 for key in integers):
            raise PrivateStorageError('Invalid private storage budget')
        mode = budget.get('limit_mode', 'legacy_fixed')
        ceiling = budget.get('aggregate_ceiling_bytes')
        valid_ceiling = type(ceiling) is int and 0 < ceiling <= 1048576 * 1024**3
        if (mode not in {'legacy_fixed', 'fixed', 'filesystem'}
                or mode == 'filesystem' and ceiling is not None
                or mode != 'filesystem' and not valid_ceiling
                or mode == 'legacy_fixed' and valid_ceiling and ceiling > 20 * 1024**3):
            raise PrivateStorageError('Private storage budget exceeds the admitted aggregate ceiling')
        if budget['system_floor_bytes'] < 4 * 1024**3 or budget['volume_floor_bytes'] < 10 * 1024**3:
            raise PrivateStorageError('Private storage cannot lower the admitted free-space floors')
        on_volume = target.is_relative_to(self.volume)
        growth = reserve_bytes if on_volume else 0
        if mode != 'filesystem' and _allocated(self.volume) + growth + budget['external_reserved_bytes'] > ceiling:
            raise PrivateStorageError('Private storage aggregate growth budget would be exceeded')
        for root, floor, reserve in (
            (self.private, budget['system_floor_bytes'], reserve_bytes if not on_volume else 0),
            (self.volume, budget['volume_floor_bytes'], growth),
        ):
            info = os.statvfs(root)
            if (type(info.f_bavail) is not int or info.f_bavail < 0
                    or type(info.f_frsize) is not int or info.f_frsize <= 0):
                raise PrivateStorageError('Private storage filesystem capacity is unknown')
            if info.f_bavail * info.f_frsize - reserve < floor:
                raise PrivateStorageError('Private storage free-space floor would be crossed')


def _bindings(private: Path) -> Path:
    return private / 'state/applications/private-storage-bindings'


def _sync_binding(path: Path) -> None:
    # File fsync + rename alone does not durably enroll a new placement on
    # POSIX. Its directory link must survive before any Candidate data effect.
    if not sync_directory(path.parent.parent) or not sync_directory(path.parent):
        raise PrivateStorageError('Private storage placement metadata could not be persisted')


def load_candidate_storage(private: Path, candidate_id: str, *, application_id: str | None = None,
                           release_digest: str | None = None) -> CandidateStorage | None:
    path = _bindings(private) / f'{_segment(candidate_id)}.json'
    if not path.exists() and not path.is_symlink():
        return None
    record = _read(path)
    if (record.get('schema') != 'adaos.application.private_candidate_storage.v1'
            or record.get('candidate_id') != candidate_id
            or application_id is not None and record.get('application_id') != application_id
            or release_digest is not None and record.get('release_digest') != release_digest):
        raise PrivateStorageError('Private candidate storage identity changed')
    binding = CandidateStorage(private, record)
    binding.verify_volume()
    binding.policy  # Reject an invalid profile before creating any directory.
    return binding


def enroll_candidate_storage(private: Path, candidate_id: str, release, *, workspace_root: Path | None = None) -> CandidateStorage | None:
    """Called by immutable Candidate preparation, before Trial materialization."""
    existing = load_candidate_storage(private, candidate_id, application_id=release.project_id,
                                      release_digest=release.release_digest)
    if existing is not None:
        _sync_binding(_bindings(private) / f'{candidate_id}.json')
        return existing
    from .storage_settings import ApplicationStorageSettings
    settings = ApplicationStorageSettings(private, release.project_id)
    if settings.read()['revision']:
        profile = settings.candidate_profile(release)
        if profile is None:
            return None
    else:
        # Compatibility with previously enrolled, exact-release profiles. New
        # Management settings are the authority once explicitly saved.
        profile_path = private / 'state/applications/private-storage-profiles' / f'{_segment(release.project_id)}.json'
        if not profile_path.exists() and not profile_path.is_symlink():
            return None
        profile = _read(profile_path)
    if (profile.get('schema') != 'adaos.application.private_storage_profile.v1'
            or profile.get('application_id') != release.project_id
            or profile.get('release_digest') != release.release_digest):
        raise PrivateStorageError('Private storage profile does not admit this exact release')
    record = {**profile, 'schema': 'adaos.application.private_candidate_storage.v1',
              'candidate_id': _segment(candidate_id)}
    binding = CandidateStorage(private, record)
    binding.verify_volume()
    binding.policy
    binding.admit_write(binding.workspace, profile['initial_reservation_bytes'])
    workspace = workspace_root if workspace_root is not None else private / 'workspace'
    legacy_roots = (private / 'trials' / candidate_id,
                    workspace / 'trials' / candidate_id,
                    workspace / '.runtime/trials' / candidate_id)
    if any(path.exists() or path.is_symlink() for path in (*legacy_roots, binding.candidate_root)):
        raise PrivateStorageError('Private placement cannot adopt an existing Trial tree')
    path = _bindings(private) / f'{candidate_id}.json'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with mutation_lock(path.with_suffix('.lock')):
        existing = load_candidate_storage(private, candidate_id, application_id=release.project_id,
                                          release_digest=release.release_digest)
        if existing is not None:
            _sync_binding(path)
            return existing
        # Commit placement authority before any directory or data effect. Replay
        # uses this immutable snapshot, not a later operator profile revision.
        atomic_write_json(path, record)
        _sync_binding(path)
    return binding
