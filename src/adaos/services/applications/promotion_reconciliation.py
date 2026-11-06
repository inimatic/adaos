"""Rejoin completed local publication receipts to their installed Application.

No publication, activation, data adoption or new acceptance takes place here.
The existing completed promotion is authority; channels are its CAS projection.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from packaging.version import Version

from adaos.domain.artifact_release import WorkspaceLock
from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.publication import PROMOTION_OPERATION_SCHEMA
from adaos.services.artifact_pipeline.storage import mutation_lock, MutationLockTimeout
from .store import ApplicationStore

_LOG = logging.getLogger(__name__)


def completed_promotion(state, candidate):
    """Require exact Stable and Workspace receipts, never a loose status bit."""
    candidates = CandidateStore(Path(state) / 'artifact_pipeline/candidates')
    path = Path(state) / 'artifact_pipeline/promotions' / (candidates.path(candidate.candidate_id).stem + '.json')
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding='utf8'))
    if (record.get('schema') != PROMOTION_OPERATION_SCHEMA or record.get('status') != 'completed'
            or record.get('phase') != 'completed'
            or record.get('candidate_id') != candidate.candidate_id
            or record.get('project_id') != candidate.project_id
            or record.get('release_digest') != candidate.release_digest):
        return None
    receipts = record.get('receipts') or {}
    pointer = (receipts.get('channel_moved') or {}).get('pointer') or {}
    activation = receipts.get('workspace_activated') or {}
    binding = record.get('attestation_binding') or {}
    attestations = binding.get('attestation_set') or {}
    if (pointer.get('channel') != 'stable' or pointer.get('project_id') != candidate.project_id
            or pointer.get('release_digest') != candidate.release_digest or not activation.get('workspace_lock')):
        return None
    if (binding.get('status') != 'completed' or attestations.get('project_id') != candidate.project_id
            or attestations.get('release_digest') != candidate.release_digest):
        return None
    lock = WorkspaceLock.from_mapping(activation['workspace_lock'])
    if (activation.get('lock_digest') != lock.to_dict()['lock_digest']
            or not any(s.project_id == candidate.project_id and s.release_digest == candidate.release_digest for s in lock.slots)):
        return None
    return record


def _reconcile_completed_workspace_promotions(owner, *, limit=2, application_id=None):
    owner = getattr(owner, 'authority_context', None) or owner
    state, workspace = Path(owner.paths.state_dir()), Path(owner.paths.workspace_dir())
    store = ApplicationStore(state)
    config = getattr(owner, 'config', None)
    subnet = str(getattr(config, 'subnet_id_value', None) or getattr(config, 'subnet_id', None) or '')
    if not subnet:
        return []
    publisher = subnet if subnet.startswith('subnet:') else 'subnet:' + subnet
    results = []
    # Serialize with the existing publication writer, not a second delivery lock.
    with mutation_lock(workspace / '.adaos/.workspace-writer.lock', timeout_s=.25):
        lock_path = workspace / '.adaos/workspace.lock.json'
        if not lock_path.is_file():
            return results
        lock = WorkspaceLock.from_mapping(json.loads(lock_path.read_text(encoding='utf8')))
        selections = store.list_runtime_selections()
        for installed in store.list_installations():
            if application_id is not None and installed.application_id != application_id:
                continue
            app = store.get_application(installed.application_id)
            digest = installed.installed_release_digest
            channels = store.get_channels(app.application_id).get('channels') or {}
            if installed.status != 'active' or app.publisher_ref != publisher or channels.get('stable') == digest:
                continue
            current = [s for s in selections if s.application_id == app.application_id]
            if not current or any((s.source, s.runtime_root_ref, s.release_digest) != ('stable_installation', 'workspace', digest) for s in current):
                continue
            if not any(s.project_id == app.legacy_project_id and s.release_digest == digest for s in lock.slots):
                continue
            try:
                release = store.get_release(app.application_id, digest)
                if channels.get('stable'):
                    previous = store.get_release(app.application_id, channels['stable'])
                    if Version(previous.project_release.version) >= Version(release.project_release.version):
                        continue  # An intentionally selected older Workspace never rolls back the channel.
                if channels.get('prerelease') not in {None, digest}:
                    continue
                candidate = CandidateStore(state / 'artifact_pipeline/candidates').load(release.accepted_candidate_id)
                if candidate.status != 'accepted' or candidate.project_id != app.legacy_project_id or candidate.release_digest != digest:
                    continue
                record = completed_promotion(state, candidate)
                if record is None:
                    continue
                if store.list_operations(app.application_id, statuses={'applying', 'unknown', 'reconciling'}):
                    continue
                # Normal installation reconciliation verifies the current exact
                # component/dependency closure and preserves local data.
                from .service import ApplicationService
                ApplicationService(store).reconcile_workspace_installation(app.application_id, digest, lock)
                with mutation_lock(store.lock_path, timeout_s=30):
                    if store.list_operations(app.application_id, statuses={'applying', 'unknown', 'reconciling'}):
                        continue
                    current_installation = store.get_installation(app.application_id)
                    if current_installation.status != 'active' or current_installation.installed_release_digest != digest:
                        continue
                    store.set_channel(app.application_id, 'stable', digest, expected_release_digest=channels.get('stable'))
                # The existing lease observer emits channel-revision changes.
                results.append({'application_id': app.application_id, 'candidate_id': candidate.candidate_id,
                                'release_digest': digest, 'status': 'reconciled'})
            except (OSError, ValueError, KeyError, RuntimeError):
                _LOG.warning('Completed publication projection could not reconcile application=%s', app.application_id, exc_info=True)
            if len(results) >= limit:
                break
    return results


def reconcile_completed_workspace_promotions(owner, *, limit=2, application_id=None):
    try:
        return _reconcile_completed_workspace_promotions(owner, limit=limit, application_id=application_id)
    except MutationLockTimeout:
        return []  # A foreground publication owns the same writer; observe on the next cadence.
