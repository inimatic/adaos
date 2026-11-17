"""Node-local Application storage settings, separate from release/business data.

Only the OS administrator enrolls native volumes. Management can select those
opaque references and CAS-edit policy, never submit a path or mount identity.
New Candidates pin a snapshot; editing settings does not move an existing tree.
"""
from copy import deepcopy
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .configuration import ConfigurationConflict, _digest, _validate
from .private_storage import CandidateStorage, PrivateStorageError, _read, _segment, _sync_binding

GIB = 1024**3
COMPONENT_REF = 'application:storage'
DEFAULTS = {
    'volume_ref': '', 'capacity_policy': 'fixed_budget', 'max_growth_gib': 20,
    'system_reserve_gib': 4, 'volume_reserve_gib': 10,
    'trial_preflight_gib': 12, 'migration_max_database_mib': 4096,
    'migration_timeout_seconds': 1800,
}
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': [key for key in DEFAULTS if key != 'max_growth_gib'],
    'properties': {
        'volume_ref': {'type': 'string', 'maxLength': 160, 'title': 'Private owned-data storage',
            'description': 'Administrator-enrolled volume. Changes apply to new Candidates; originals are not moved.',
            'pattern': r'^(|volume:[A-Za-z0-9_-]{1,128})$'},
        'capacity_policy': {'type': 'string', 'enum': ['fixed_budget', 'available_space'],
            'x-enum-labels': ['Fixed growth budget', 'Available volume space'],
            'title': 'Storage capacity policy'},
        'max_growth_gib': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 1048576,
            'title': 'Maximum additional growth (GiB)',
            'description': 'Required for a fixed budget. Not used in available-space mode.'},
        'system_reserve_gib': {'type': 'integer', 'minimum': 4, 'maximum': 1048576,
            'title': 'System filesystem free-space reserve (GiB)'},
        'volume_reserve_gib': {'type': 'integer', 'minimum': 10, 'maximum': 1048576,
            'title': 'Private volume free-space reserve (GiB)'},
        'trial_preflight_gib': {'type': 'integer', 'minimum': 1, 'maximum': 1048576,
            'title': 'Trial initial free-space admission (GiB)',
            'description': 'An admission check, not preallocation. Each copy is checked again.'},
        'migration_max_database_mib': {'type': 'integer', 'minimum': 512, 'maximum': 4096,
            'title': 'Migration database size limit (MiB)'},
        'migration_timeout_seconds': {'type': 'integer', 'minimum': 120, 'maximum': 1800,
            'title': 'Migration operation deadline (seconds)'},
    },
}


def normalize(values):
    if not isinstance(values, dict):
        raise ValueError('Storage settings must be an object')
    values = deepcopy(values)
    _validate(SCHEMA, values)
    for key in DEFAULTS:
        if key not in {'volume_ref', 'capacity_policy'} and values.get(key) is not None:
            if type(values[key]) is not int:
                raise ValueError('Storage limits require integer units')
    if values['capacity_policy'] == 'fixed_budget':
        if values.get('max_growth_gib') is None:
            raise ValueError('Fixed storage budget requires a growth limit')
    else:
        if not values['volume_ref']:
            raise ValueError('Available-space policy requires an enrolled private volume')
        values['max_growth_gib'] = None
    return values


class ApplicationStorageSettings:
    def __init__(self, private: Path, application_id: str):
        self.private = Path(private).absolute()
        if not isinstance(application_id, str) or not application_id or len(application_id) > 256:
            raise ValueError('Application storage identity is required')
        self.application_id = application_id
        key = _digest(application_id).split(':', 1)[1]
        self.path = self.private / 'state/applications/storage-settings' / f'{key}.json'
        self.volumes = self.private / 'state/applications/storage-volumes' / key

    def read(self):
        if not self.path.exists() and not self.path.is_symlink():
            return {'schema': 'adaos.application.storage_settings.v1',
                'application_id': self.application_id, 'revision': 0, 'values': deepcopy(DEFAULTS)}
        record = _read(self.path)
        if (record.get('schema') != 'adaos.application.storage_settings.v1'
                or record.get('application_id') != self.application_id
                or type(record.get('revision')) is not int or record['revision'] < 1):
            raise PrivateStorageError('Application storage settings identity changed')
        if normalize(record['values']) != record['values']:
            raise PrivateStorageError('Application storage settings are not canonical')
        return record

    def _volume(self, ref):
        if not isinstance(ref, str) or not ref.startswith('volume:'):
            raise PrivateStorageError('An enrolled volume reference is required')
        name = _segment(ref.removeprefix('volume:'))
        record = _read(self.volumes / f'{name}.json')
        if (record.get('schema') != 'adaos.application.enrolled_storage_volume.v1'
                or record.get('application_id') != self.application_id
                or record.get('volume_ref') != ref
                or record.get('volume_relative') != f'volumes/{name}'):
            raise PrivateStorageError('Enrolled Application volume identity changed')
        return record

    def _verify(self, volume):
        CandidateStorage(self.private, {**volume, 'candidate_id': 'storage-settings-check'}).verify_volume()

    def enroll_volume(self, name: str, *, label: str):
        """Trusted OS-owner provisioning only; not exposed to Management input."""
        name = _segment(name)
        path = self.private / 'volumes' / name
        import os
        info = path.stat()
        record = {'schema': 'adaos.application.enrolled_storage_volume.v1',
            'application_id': self.application_id, 'volume_ref': f'volume:{name}',
            'volume_relative': f'volumes/{name}', 'label': str(label)[:160],
            'volume_identity': {'inode': info.st_ino, 'fsid': os.statvfs(path).f_fsid, 'gid': info.st_gid}}
        self._verify(record)
        self.volumes.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.volumes / f'{name}.json'
        with mutation_lock(target.with_suffix('.lock')):
            if target.exists() or target.is_symlink():
                if _read(target) != record:
                    raise ConfigurationConflict('Enrolled volume changed; explicit re-enrollment review required')
            else:
                atomic_write_json(target, record)
            _sync_binding(target)
        return record

    def write(self, values, *, expected_revision: int):
        values = normalize(values)
        if values['volume_ref']:
            self._verify(self._volume(values['volume_ref']))
        if type(expected_revision) is not int or expected_revision < 0:
            raise ConfigurationConflict('A displayed storage settings revision is required')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with mutation_lock(self.path.with_suffix('.lock')):
            record = self.read()
            if record['revision'] != expected_revision:
                raise ConfigurationConflict('Storage settings changed; reopen before applying')
            saved = {**record, 'revision': expected_revision + 1, 'values': values}
            atomic_write_json(self.path, saved)
            _sync_binding(self.path)
        return saved

    def inspect(self):
        record = self.read()
        volumes = []
        if self.volumes.exists() or self.volumes.is_symlink():
            if self.volumes.absolute() != self.volumes.resolve():
                raise PrivateStorageError('Enrolled volume registry is linked')
            for path in sorted(self.volumes.glob('*.json')):
                if len(volumes) == 64:
                    raise PrivateStorageError('Enrolled volume listing exceeds its bounded envelope')
                value = self._volume(f'volume:{path.stem}')
                volumes.append({'ref': value['volume_ref'], 'label': value['label']})
        status = 'not_configured'
        if record['values']['volume_ref']:
            try:
                self._verify(self._volume(record['values']['volume_ref']))
                status = 'mounted'  # Identity observation, not an all-writer admission claim.
            except (PrivateStorageError, OSError):
                status = 'unavailable'
        schema = deepcopy(SCHEMA)
        schema['properties']['volume_ref'].update(
            enum=['', *[value['ref'] for value in volumes]],
            **{'x-enum-labels': ['Workspace (default)', *[value['label'] for value in volumes]]})
        return {**record, 'configured': record['revision'] > 0, 'status': status,
            'available_volumes': volumes, 'form_schema': schema, 'applies_to': 'new_candidates',
            'existing_data_relocated': False}

    def candidate_profile(self, release):
        if release.project_id != self.application_id:
            raise PrivateStorageError('Storage settings belong to another Application')
        record = self.read()
        values = record['values']
        if not record['revision'] or not values['volume_ref']:
            return None
        volume = self._volume(values['volume_ref'])
        self._verify(volume)
        return {'schema': 'adaos.application.private_storage_profile.v1',
            'application_id': self.application_id, 'release_digest': release.release_digest,
            'volume_relative': volume['volume_relative'], 'volume_identity': deepcopy(volume['volume_identity']),
            'storage_settings_revision': record['revision'], 'storage_settings_digest': _digest(values),
            'storage_settings': deepcopy(values),
            'sqlite_policy': {'max_database_bytes': values['migration_max_database_mib'] * 1024**2,
                'operation_timeout_seconds': values['migration_timeout_seconds']},
            'budget': {'limit_mode': 'filesystem' if values['capacity_policy'] == 'available_space' else 'fixed',
                'aggregate_ceiling_bytes': values['max_growth_gib'] * GIB if values['max_growth_gib'] is not None else None,
                'external_reserved_bytes': 0, 'system_floor_bytes': values['system_reserve_gib'] * GIB,
                'volume_floor_bytes': values['volume_reserve_gib'] * GIB},
            'initial_reservation_bytes': values['trial_preflight_gib'] * GIB}
