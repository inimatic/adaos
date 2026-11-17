"""Prepare an exact source-checkpoint Trial through existing Core effect owners."""
from collections.abc import Mapping
import re


def checkpoint_identity(value: Mapping) -> dict:
    keys = {'change_id', 'operation_id', 'source_snapshot_digest'}
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError('Source Trial requires one exact Change, operation and snapshot digest')
    if (not all(isinstance(value[k], str) for k in keys)
            or not all(re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,127}', value[k]) for k in ('change_id', 'operation_id'))
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(value['source_snapshot_digest']))):
        raise ValueError('Invalid source checkpoint identity')
    return dict(value)


def prepare_source_trial(application, *, source_checkpoint, verification_evidence,
                         source_webspace_id, actor_ref, idempotency_key, permission_decision):
    from . import applications, artifacts
    checkpoint = checkpoint_identity(source_checkpoint)
    evidence = dict(verification_evidence or {})
    if (evidence.get('status') != 'passed'
            or evidence.get('source_snapshot_digest') != checkpoint['source_snapshot_digest']):
        raise ValueError('Source Trial requires passed checks of the exact frozen Project')
    webspace = applications.production_webspace_id(source_webspace_id)
    prepared = artifacts.prepare_project_candidate(project_id=application.legacy_project_id,
        change_id=checkpoint['change_id'], operation_id=checkpoint['operation_id'],
        validation_evidence=evidence, target_webspace_id=webspace, target_space_kind='workspace',
        idempotency_key=idempotency_key, permission_decision=permission_decision)
    candidate, release = prepared.get('candidate') or {}, prepared.get('release') or {}
    if (prepared.get('ok') is not True or candidate.get('project_id') != application.legacy_project_id
            or release.get('project_id') != application.legacy_project_id
            or not candidate.get('candidate_id') or not candidate.get('package_digest')
            or not candidate.get('release_digest') or candidate['release_digest'] != release.get('release_digest')):
        raise ValueError('Source Trial returned another Candidate or release')
    commit = (candidate.get('source_ref') or {}).get('revision')
    if not commit or evidence.get('source_commit') not in {None, '', commit}:
        raise ValueError('Source Trial verification identifies another source commit')
    access = applications.verify_candidate_access(application.legacy_project_id, candidate['candidate_id'],
        evidence={**evidence, 'source_commit': commit}, actor_ref=actor_ref, webspace_id=webspace,
        application_id=application.application_id)
    placement = applications.place_local_trial(candidate['candidate_id'], webspace_id=webspace,
        actor_ref=actor_ref, application_id=application.application_id)
    return {**prepared, 'application_verification': access, 'placement': placement,
            'source_checkpoint_identity': checkpoint}
