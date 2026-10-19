"""Placement routing tests use fake volume capacity, never real mounts or media."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.applications import private_storage as module
from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root, trial_rollback_workspace

GIB = 1024**3


@pytest.fixture
def enrolled(tmp_path, monkeypatch):
    private = tmp_path.resolve()
    volume = private / 'volumes/test-volume'
    volume.mkdir(parents=True)
    release = SimpleNamespace(project_id='media_center', release_digest='sha256:' + '1' * 64)
    profile = {
        'schema': 'adaos.application.private_storage_profile.v1',
        'application_id': release.project_id, 'release_digest': release.release_digest,
        'volume_relative': 'volumes/test-volume', 'volume_identity': {'inode': 123, 'fsid': 456, 'gid': 0},
        'sqlite_policy': {'max_database_bytes': 4 * GIB, 'operation_timeout_seconds': 1800},
        'budget': {'aggregate_ceiling_bytes': 20 * GIB, 'external_reserved_bytes': 7 * GIB,
                   'system_floor_bytes': 4 * GIB, 'volume_floor_bytes': 10 * GIB},
        'initial_reservation_bytes': 12 * GIB,
    }
    profile_path = private / 'state/applications/private-storage-profiles/media_center.json'
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(json.dumps(profile), encoding='utf-8')
    # POSIX trust and actual mount identity are independently tested below.
    monkeypatch.setattr(module, '_read', lambda path: json.loads(path.read_text(encoding='utf-8')))
    def verify(binding):
        assert binding.volume.absolute() == binding.volume.resolve()
        if not binding.volume.is_dir():
            raise module.PrivateStorageError('missing volume')
    monkeypatch.setattr(module.CandidateStorage, 'verify_volume', verify)
    monkeypatch.setattr(module, '_allocated', lambda root: sum(p.stat().st_size for p in root.rglob('*') if p.is_file()))
    monkeypatch.setattr(module.os, 'statvfs', lambda root: SimpleNamespace(f_bavail=200 * GIB, f_frsize=1), raising=False)
    return private, volume, release, profile, profile_path


def test_no_profile_keeps_the_existing_trial_layout(tmp_path):
    workspace = tmp_path.resolve() / 'workspace'
    assert module.enroll_candidate_storage(workspace.parent, 'candidate1', SimpleNamespace(project_id='app', release_digest='digest')) is None
    assert trial_workspace_root(workspace, 'candidate1') == workspace.parent / 'trials/candidate1'


def test_plain_directory_is_not_a_substitute_for_the_enrolled_volume(tmp_path):
    private = tmp_path.resolve()
    (private / 'volumes/test-volume').mkdir(parents=True)
    binding = module.CandidateStorage(private, {'volume_relative': 'volumes/test-volume'})
    with pytest.raises(module.PrivateStorageError, match='fallback is forbidden|POSIX ownership'):
        binding.verify_volume()


def test_exact_release_placement_is_durable_and_pins_its_profile_snapshot(enrolled):
    private, volume, release, profile, profile_path = enrolled
    binding = module.enroll_candidate_storage(private, 'candidate1', release)
    assert trial_workspace_root(private / 'workspace', 'candidate1') == volume / 'candidates/candidate1/workspace'
    assert trial_rollback_workspace(private / 'workspace', private / 'state/artifact_pipeline',
                                    'candidate1', 'trial-candidate1') == volume / 'candidates/candidate1/trial-rollbacks/trial-candidate1/workspace'
    assert binding.recovery == volume / 'candidates/candidate1/recovery'
    assert binding.policy.max_database_bytes == 4 * GIB
    assert not binding.candidate_root.exists()  # Binding commits before data effects.
    profile['volume_relative'] = 'volumes/other-volume'
    profile_path.write_text(json.dumps(profile), encoding='utf-8')
    assert module.enroll_candidate_storage(private, 'candidate1', release).record == binding.record


def test_release_or_application_mismatch_cannot_reuse_a_placement(enrolled):
    private, _volume, release, _profile, _path = enrolled
    module.enroll_candidate_storage(private, 'candidate1', release)
    for kwargs in ({'application_id': 'other'}, {'release_digest': 'sha256:' + '2' * 64}):
        with pytest.raises(module.PrivateStorageError, match='identity changed'):
            module.load_candidate_storage(private, 'candidate1', **kwargs)
    with pytest.raises(module.PrivateStorageError, match='exact release'):
        module.enroll_candidate_storage(private, 'candidate2', SimpleNamespace(project_id='media_center', release_digest='different'))


@pytest.mark.parametrize('where', ['trials/candidate1', 'volumes/test-volume/candidates/candidate1'])
def test_placement_never_adopts_or_moves_an_existing_trial(enrolled, where):
    private, _volume, release, _profile, _path = enrolled
    existing = private / where
    existing.mkdir(parents=True)
    original = existing / 'keep.txt'
    original.write_text('owned data')
    with pytest.raises(module.PrivateStorageError, match='existing Trial'):
        module.enroll_candidate_storage(private, 'candidate1', release)
    assert original.read_text() == 'owned data'
    assert not (private / 'state/applications/private-storage-bindings/candidate1.json').exists()


def test_missing_volume_never_falls_back_to_the_system_disk(enrolled):
    private, volume, release, _profile, _path = enrolled
    module.enroll_candidate_storage(private, 'candidate1', release)
    volume.rmdir()
    with pytest.raises(module.PrivateStorageError, match='missing volume'):
        trial_workspace_root(private / 'workspace', 'candidate1')
    assert not (private / 'trials/candidate1').exists()


@pytest.mark.parametrize('relative', ['../elsewhere', '/mnt/public', 'volumes/../public', 'volumes/name/child'])
def test_profile_cannot_escape_the_private_volume_namespace(enrolled, relative):
    private, _volume, release, profile, path = enrolled
    profile['volume_relative'] = relative
    path.write_text(json.dumps(profile))
    with pytest.raises(module.PrivateStorageError):
        module.enroll_candidate_storage(private, 'candidate1', release)


def test_growth_budget_and_each_filesystem_floor_are_admitted_before_writes(enrolled, monkeypatch):
    private, volume, release, _profile, _path = enrolled
    binding = module.enroll_candidate_storage(private, 'candidate1', release)
    with pytest.raises(module.PrivateStorageError, match='growth budget'):
        binding.admit_write(binding.workspace, 14 * GIB)
    for low_root, target in ((private, private / 'workspace/main.sqlite'), (volume, binding.workspace)):
        monkeypatch.setattr(module.os, 'statvfs', lambda root: SimpleNamespace(
            f_bavail=(4 if root == private else 10) * GIB if root == low_root else 200 * GIB, f_frsize=1))
        with pytest.raises(module.PrivateStorageError, match='free-space floor'):
            binding.admit_write(target, 1)
    assert not binding.workspace.exists()


def test_a_profile_cannot_lower_the_approved_floors(enrolled):
    private, _volume, release, profile, path = enrolled
    profile['budget']['system_floor_bytes'] = 1
    path.write_text(json.dumps(profile))
    with pytest.raises(module.PrivateStorageError, match='cannot lower'):
        module.enroll_candidate_storage(private, 'candidate1', release)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX enrollment authority')
def test_local_authority_rejects_group_writable_or_linked_records(tmp_path):
    private = tmp_path / 'private'
    record = private / 'state/applications/private-storage-profiles/app.json'
    record.parent.mkdir(parents=True, mode=0o700)
    record.write_text('{}')
    record.chmod(0o600)
    assert module._read(record) == {}
    record.chmod(0o660)
    with pytest.raises(module.PrivateStorageError, match='owner-controlled'):
        module._read(record)
    record.chmod(0o600)
    link = record.with_name('linked.json')
    link.symlink_to(record)
    with pytest.raises(module.PrivateStorageError, match='linked'):
        module._read(link)


@pytest.mark.skipif(os.name != 'posix', reason='Native POSIX volume inventory')
def test_volume_inventory_rejects_links_instead_of_traversing_them(tmp_path):
    root = tmp_path / 'store'
    root.mkdir()
    (root / 'one').write_bytes(b'bounded')
    assert module._allocated(root) >= 7
    (root / 'link').symlink_to(tmp_path)
    with pytest.raises(module.PrivateStorageError, match='linked'):
        module._allocated(root)


@pytest.mark.skipif(os.name != 'posix', reason='Native POSIX mount helper')
def test_boot_mount_refuses_changed_identity_before_any_mount_effect(tmp_path, monkeypatch):
    from adaos.services.applications import private_volume_mount as mount
    private = tmp_path / 'private'
    config = private / 'state/private-storage/mount.json'
    config.parent.mkdir(parents=True)
    source = tmp_path / 'source'
    source.mkdir(mode=0o700)
    config.write_text(json.dumps({'schema': 'adaos.application.private_volume_mount.v1',
        'private_root': str(private), 'source': str(source), 'volume_name': 'test',
        'volume_identity': {'inode': source.stat().st_ino, 'fsid': -1, 'gid': source.stat().st_gid}}))
    config.chmod(0o600)
    monkeypatch.setattr(mount.subprocess, 'run', lambda *a, **k: pytest.fail('mount must not run'))
    with pytest.raises(ValueError, match='identity'):
        mount.mount_enrolled_store(config)
    assert not (private / 'volumes').exists()
    config.chmod(0o644)
    with pytest.raises(ValueError, match='owner-controlled'):
        mount.mount_enrolled_store(config)
