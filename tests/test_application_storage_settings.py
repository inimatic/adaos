"""Synthetic Application settings and placement; never read a real installation."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.sdk import applications
from adaos.services.applications import private_storage, storage_settings as storage
from adaos.services.applications.configuration import ConfigurationConflict

GIB = 1024**3


@pytest.fixture
def settings(tmp_path, monkeypatch):
    private = tmp_path / 'private'
    (private / 'volumes/store').mkdir(parents=True)
    monkeypatch.setattr(storage, '_read', lambda path: json.loads(path.read_text(encoding='utf-8')))
    monkeypatch.setattr(private_storage, '_read', lambda path: json.loads(path.read_text(encoding='utf-8')))
    monkeypatch.setattr(storage, '_sync_binding', lambda path: None)
    monkeypatch.setattr(private_storage, '_sync_binding', lambda path: None)
    monkeypatch.setattr(private_storage, '_allocated', lambda root: 0)
    monkeypatch.setattr(private_storage.os, 'statvfs', lambda path: SimpleNamespace(
        f_fsid=42, f_bavail=500 * GIB, f_frsize=1), raising=False)
    def verify(binding):
        if not binding.volume.is_dir():
            raise private_storage.PrivateStorageError('mount missing; no fallback')
    monkeypatch.setattr(private_storage.CandidateStorage, 'verify_volume', verify)
    value = storage.ApplicationStorageSettings(private, 'media_center')
    value.enroll_volume('store', label='Private library store')
    return value


def available_values():
    return {**deepcopy(storage.DEFAULTS), 'volume_ref': 'volume:store',
            'capacity_policy': 'available_space', 'max_growth_gib': None}


def release():
    return SimpleNamespace(project_id='media_center', release_digest='sha256:' + '1' * 64)


def test_default_read_has_no_effect_and_does_not_claim_an_enrolled_profile(tmp_path):
    before = list(tmp_path.iterdir())
    value = storage.ApplicationStorageSettings(tmp_path, 'app:with.dots/and:opaque-id')
    assert value.read()['revision'] == 0
    assert value.inspect()['status'] == 'not_configured'
    assert not value.path.exists() and list(tmp_path.iterdir()) == before


def test_persistent_available_space_settings_use_cas_and_do_not_relocate(settings):
    saved = settings.write(available_values(), expected_revision=0)
    assert saved['revision'] == 1 and saved['values']['max_growth_gib'] is None
    assert storage.ApplicationStorageSettings(settings.private, 'media_center').read() == saved
    with pytest.raises(ConfigurationConflict, match='changed'):
        settings.write(available_values(), expected_revision=0)
    view = settings.inspect()
    assert view['status'] == 'mounted' and view['applies_to'] == 'new_candidates'
    assert view['existing_data_relocated'] is False
    assert view['available_volumes'] == [{'ref': 'volume:store', 'label': 'Private library store'}]


@pytest.mark.parametrize('patch', [
    {'volume_ref': '/mnt/public'}, {'volume_ref': 'volume:../elsewhere'},
    {'source': '/root/private'}, {'system_reserve_gib': 3}, {'volume_reserve_gib': 9},
    {'system_reserve_gib': True}, {'system_reserve_gib': 4.0},
    {'migration_max_database_mib': 8192}, {'migration_timeout_seconds': 1801},
    {'capacity_policy': 'unlimited'}, {'max_growth_gib': float('nan')},
    {'capacity_policy': 'fixed_budget', 'max_growth_gib': None},
])
def test_invalid_settings_are_rejected_without_a_persistent_write(settings, patch):
    with pytest.raises(ValueError):
        settings.write({**available_values(), **patch}, expected_revision=0)
    assert not settings.path.exists()


def test_a_caller_cannot_select_an_unenrolled_or_other_app_volume(settings):
    with pytest.raises((ValueError, OSError)):
        settings.write({**available_values(), 'volume_ref': 'volume:other'}, expected_revision=0)
    other = storage.ApplicationStorageSettings(settings.private, 'other_app')
    with pytest.raises((ValueError, OSError)):
        other.write(available_values(), expected_revision=0)
    assert not settings.path.exists() and not other.path.exists()


def test_missing_mount_preserves_visible_settings_and_denies_new_candidate(settings):
    settings.write(available_values(), expected_revision=0)
    (settings.private / 'volumes/store').rmdir()
    assert settings.inspect()['status'] == 'unavailable'
    assert settings.read()['values']['volume_ref'] == 'volume:store'
    with pytest.raises(private_storage.PrivateStorageError, match='mount missing'):
        private_storage.enroll_candidate_storage(settings.private, 'candidate1', release())
    assert not (settings.private / 'trials').exists()


def test_candidate_snapshots_settings_and_a_later_save_cannot_change_existing_data_policy(settings):
    settings.write(available_values(), expected_revision=0)
    first = private_storage.enroll_candidate_storage(settings.private, 'candidate1', release())
    assert first.record['storage_settings_revision'] == 1
    assert first.record['budget']['aggregate_ceiling_bytes'] is None
    first.admit_write(first.workspace, 30 * GIB)  # No hidden old 20 GiB cap.
    settings.write({**available_values(), 'capacity_policy': 'fixed_budget', 'max_growth_gib': 128},
                   expected_revision=1)
    assert private_storage.enroll_candidate_storage(settings.private, 'candidate1', release()).record == first.record
    second = private_storage.enroll_candidate_storage(settings.private, 'candidate2', release())
    assert second.record['storage_settings_revision'] == 2
    assert second.record['budget']['aggregate_ceiling_bytes'] == 128 * GIB
    second.admit_write(second.workspace, 30 * GIB)
    with pytest.raises(private_storage.PrivateStorageError, match='growth budget'):
        second.admit_write(second.workspace, 129 * GIB)


def test_available_space_still_checks_both_filesystem_reserves(settings, monkeypatch):
    settings.write(available_values(), expected_revision=0)
    binding = private_storage.enroll_candidate_storage(settings.private, 'candidate1', release())
    for low_path in (binding.private, binding.volume):
        monkeypatch.setattr(private_storage.os, 'statvfs', lambda path: SimpleNamespace(
            f_bavail=(3 if path == binding.private else 9) * GIB if path == low_path else 500 * GIB,
            f_frsize=1))
        with pytest.raises(private_storage.PrivateStorageError, match='free-space floor'):
            binding.admit_write(binding.workspace, 1)
    assert not binding.workspace.exists()


def test_unknown_capacity_never_permits_a_new_data_tree(settings, monkeypatch):
    settings.write(available_values(), expected_revision=0)
    def unavailable(path):
        raise OSError('capacity unknown')
    monkeypatch.setattr(private_storage.os, 'statvfs', unavailable)
    with pytest.raises(OSError, match='unknown'):
        private_storage.enroll_candidate_storage(settings.private, 'candidate1', release())
    assert not (settings.private / 'volumes/store/candidates').exists()


@pytest.mark.parametrize('free,block', [(float('nan'), 1), (-1, 1), (100, 0), (100, float('inf'))])
def test_invalid_capacity_never_bypasses_an_available_space_reserve(settings, monkeypatch, free, block):
    settings.write(available_values(), expected_revision=0)
    monkeypatch.setattr(private_storage.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=free, f_frsize=block))
    with pytest.raises(private_storage.PrivateStorageError, match='capacity'):
        private_storage.enroll_candidate_storage(settings.private, 'candidate1', release())
    assert not (settings.private / 'volumes/store/candidates').exists()


def test_standard_application_setup_displays_and_saves_the_native_storage_editor(settings, monkeypatch):
    monkeypatch.setattr(applications, '_state_dir', lambda: settings.private / 'state')
    selected = SimpleNamespace(release_digest=release().release_digest, setup_contract=None)
    monkeypatch.setattr(applications, '_application_setup_target', lambda *a, **k: ({}, selected, 'stable'))
    admissions = []
    monkeypatch.setattr(applications, '_mutation_identity', lambda *a, **k: admissions.append(k))
    view = applications.get_application_setup('media_center')
    assert view['available'] is True
    editor = view['editors']['settings'][0]
    assert editor['component_ref'] == storage.COMPONENT_REF and editor['supported']
    volume = next(field for field in editor['fields'] if field['id'] == 'volume_ref')
    assert volume['options'][1] == {'value': 'volume:store', 'label': 'Private library store'}
    result = applications.update_application_configuration('media_center', storage.COMPONENT_REF,
        available_values(), release_digest=selected.release_digest, expected_revision=editor['expected_revision'],
        actor_ref='user:owner', subnet_ref='subnet:test', capability='applications.apply')
    assert admissions == [{'required_capability': 'applications.apply'}]
    assert result['configuration']['revision'] == result['setup']['storage']['revision'] == 1
    assert result['setup']['editors']['settings'][0]['values']['capacity_policy'] == 'available_space'
    from jsonschema import Draft202012Validator
    from adaos.services.root_mcp.applications_plane import contracts
    tools = {tool.id: tool for tool in contracts()}
    setup_schema = tools['applications.setup.show'].output_schema['properties']['result']['properties']['setup']
    Draft202012Validator(setup_schema).validate(result['setup'])
    request = {'application_id': 'media_center', 'component_ref': storage.COMPONENT_REF,
        'release_digest': selected.release_digest, 'values': available_values(), 'expected_revision': 1}
    validator = Draft202012Validator(tools['applications.setup.configure'].input_schema)
    validator.validate(request)
    assert list(validator.iter_errors({**request, 'component_ref': 'application:storage/forged'}))
    assert list(validator.iter_errors({**request, 'component_ref': 'application:unrelated'}))


def test_setup_save_cannot_skip_existing_management_authorization(settings, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError('caller denied')
    monkeypatch.setattr(applications, '_mutation_identity', denied)
    with pytest.raises(PermissionError, match='caller denied'):
        applications.update_application_configuration('media_center', storage.COMPONENT_REF,
            available_values(), release_digest=release().release_digest, expected_revision=0,
            actor_ref='user:forged', subnet_ref='subnet:test', capability='applications.apply')
    assert not settings.path.exists()
