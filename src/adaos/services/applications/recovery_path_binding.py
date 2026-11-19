"""Explicit same-volume path repair. Historical migration journals stay immutable."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.node_maintenance.storage import digest, now, read

ROLE = 'application_recovery'
SCHEMA = 'adaos.application_recovery.path_binding.v1'
SHA = re.compile(r'^[a-f0-9]{64}$')


def _root_identity(path):
    info = path.stat()
    return {'device': str(info.st_dev), 'inode': str(info.st_ino)}


def destination_matches(state, settings, placement, physical, marker, job):
    binding_id = placement.get('path_binding_digest')
    if not binding_id:
        return job.get('destination') == str(physical)
    if not isinstance(binding_id, str) or not SHA.fullmatch(binding_id):
        return False
    record = read(state / 'recovery-path-bindings' / f'{binding_id}.json', {})
    return (digest(record) == binding_id and record.get('schema') == SCHEMA
            and record.get('node_id') == settings.get('node_id') and record.get('role') == ROLE
            and record.get('migration_job_id') == marker.get('job_id')
            and record.get('migration_digest') == digest(job)
            and record.get('previous_path') == job.get('destination')
            and record.get('confirmed_path') == str(physical)
            and record.get('volume_id') == job.get('volume_id')
            and record.get('root_identity') == _root_identity(physical))


def _point_inventory(physical):
    """Recheck bounded native point identities; never claim to rehash live payloads."""
    from adaos.services.node_maintenance.migration import _plain
    entries, registered, unclassified, metadata_bytes = [], 0, 0, 0
    with os.scandir(physical) as children:
        for child in children:
            if len(entries) >= 4096:
                raise ValueError('Recovery root exceeds the bounded path-repair inventory')
            path = Path(child.path)
            directory = child.is_dir(follow_symlinks=False)
            _plain(path, directory=directory)
            marker = path / '.adaos-recovery.json' if directory else None
            if marker and marker.exists():
                size = _plain(marker).st_size
                metadata_bytes += size
                if size > 65536 or metadata_bytes > 32 * 1024 * 1024:
                    raise ValueError('Recovery metadata exceeds the path-repair budget')
                metadata = read(marker)
                identity = [metadata.get(k) for k in ('application_id', 'candidate_id', 'release_digest')]
                if (metadata.get('schema') != 'adaos.recovery.point.v1'
                        or metadata.get('adapter') != 'applications.lifecycle'
                        or not all(isinstance(value, str) and value for value in identity)
                        or hashlib.sha256(json.dumps(identity).encode()).hexdigest() != child.name):
                    raise ValueError('Recovery point identity changed')
                entries.append((child.name, digest(metadata)))
                registered += 1
            else:
                entries.append((child.name, 'unclassified'))
                unclassified += 1
    return {'digest': digest(sorted(entries)), 'registered_points': registered,
            'unclassified_entries_retained': unclassified,
            'scope': 'same_volume_root_and_native_point_metadata; payloads_not_rehashed'}


class RecoveryPathBinding:
    def __init__(self, private_root):
        from .recovery_storage import ApplicationRecoveryStorage
        self.storage = ApplicationRecoveryStorage(private_root)
        self.state = self.storage.state
        self.root = self.state / 'recovery-path-bindings'

    def _observe(self, confirmed_path):
        settings = read(self.state / 'storage-settings.json', {})
        physical, placement, marker, job = self.storage._observe_external_placement(settings)
        requested = Path(confirmed_path).expanduser()
        if not requested.is_absolute() or requested.absolute() != physical or requested.resolve() != physical:
            raise ValueError('Confirm the exact current physical recovery path')
        if placement.get('path_binding_digest'):
            raise ValueError('Recovery path already has a reviewed binding; chained repairs are not admitted')
        previous = Path(job.get('destination', ''))
        if not previous.is_absolute() or previous == physical:
            raise ValueError('A changed committed destination is required')
        if previous.exists() and previous.resolve() != physical:
            raise ValueError('Previous recovery root still exists; reconcile both copies before rebinding')
        return settings, physical, marker, job

    def plan(self, *, confirmed_path, expected_revision, actor_ref, reason):
        if not isinstance(actor_ref, str) or not 0 < len(actor_ref) <= 160:
            raise ValueError('An identified owner is required')
        if not isinstance(reason, str) or not 0 < len(reason.strip()) <= 2000:
            raise ValueError('Record the owner-confirmed reason for the path change')
        with mutation_lock(self.state / 'storage-settings.lock', timeout_s=2):
            settings, physical, marker, job = self._observe(confirmed_path)
            if type(expected_revision) is not int or settings.get('revision') != expected_revision:
                raise ValueError('Storage settings revision changed; review again')
            plan = {'schema': 'adaos.application_recovery.path_binding_plan.v1',
                    'node_id': settings['node_id'], 'role': ROLE, 'expected_revision': expected_revision,
                    'migration_job_id': marker['job_id'], 'migration_digest': digest(job),
                    'previous_path': job['destination'], 'confirmed_path': str(physical),
                    'volume_id': job['volume_id'], 'root_identity': _root_identity(physical),
                    'point_inventory': _point_inventory(physical), 'actor_ref': actor_ref,
                    'reason': reason.strip(), 'created_at': now(), 'moves_data': False,
                    'rewrites_migration': False}
            plan_id = digest(plan)
            atomic_write_json(self.root / 'plans' / f'{plan_id}.json', plan)
            return {**plan, 'plan_digest': plan_id}

    def apply(self, plan_digest):
        if not isinstance(plan_digest, str) or not SHA.fullmatch(plan_digest):
            raise ValueError('An exact reviewed path-binding plan is required')
        plan = read(self.root / 'plans' / f'{plan_digest}.json', {})
        if digest(plan) != plan_digest or plan.get('schema') != 'adaos.application_recovery.path_binding_plan.v1':
            raise ValueError('Exact path-binding plan is unavailable')
        gate, gate_lock = self.storage._gate()
        with mutation_lock(self.state / 'storage-settings.lock', timeout_s=2), mutation_lock(gate_lock, timeout_s=2), self.storage.exclusive():
            gate_state = read(gate, {})
            if gate_state and gate_state.get('status') not in {'completed', 'cancelled'}:
                raise ValueError('Finish the active recovery relocation before path repair')
            settings = read(self.state / 'storage-settings.json', {})
            binding_id = settings.get('roles', {}).get(ROLE, {}).get('path_binding_digest')
            if binding_id:
                receipt = read(self.root / f'{binding_id}.json', {})
                if receipt.get('plan_digest') == plan_digest and receipt.get('node_id') == settings.get('node_id'):
                    self.storage._placement()  # Replay still checks the live volume and journal.
                    return {**receipt, 'binding_digest': binding_id, 'replayed': True}
                raise ValueError('A different path binding is already committed')
            current, physical, marker, job = self._observe(plan['confirmed_path'])
            if (current['revision'] != plan['expected_revision'] or current['node_id'] != plan['node_id']
                    or marker['job_id'] != plan['migration_job_id'] or digest(job) != plan['migration_digest']
                    or _root_identity(physical) != plan['root_identity']
                    or _point_inventory(physical) != plan['point_inventory']):
                raise ValueError('Reviewed recovery placement changed; create a new plan')
            receipt = {**{k: v for k, v in plan.items() if k != 'schema'}, 'schema': SCHEMA,
                       'plan_digest': plan_digest, 'completed_at': now()}
            binding_id = digest(receipt)
            # Receipt first: a crash before settings leaves a harmless orphan,
            # while settings always names an already durable immutable receipt.
            atomic_write_json(self.root / f'{binding_id}.json', receipt)
            settings['roles'][ROLE]['path_binding_digest'] = binding_id
            settings['revision'] += 1
            settings['updated_at'] = now()
            atomic_write_json(self.state / 'storage-settings.json', settings)
            self.storage._placement()
            return {**receipt, 'binding_digest': binding_id, 'replayed': False}
