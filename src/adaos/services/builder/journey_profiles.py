"""Explicit trusted application profiles; registration is not behavioral proof.

The platform owns runners and acceptance plans. Candidate declarations cannot
register code, change obligations, or widen TEST mutation authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class JourneyProfile:
    profile_id: str
    domain: str
    runner: str
    plan: str | None
    scope: str
    test_only: bool = False


_PROFILES = (
    JourneyProfile('natural-retouch.v4', 'natural_retouch', 'e2e/stand/qualify-retouch-browser.py', None,
                   'Real handlers/SQLite/CPU with synthetic provider; live image quality excluded'),
    JourneyProfile('knowledge-library.v1', 'knowledge_library', 'e2e/stand/browser/application-journey.mjs',
                   'e2e/builder/development/lifecycle/acceptance/library-browser.json',
                   'Owned TEST library create/edit/validate/publish/archive/reopen/cancel; no production mutation', True),
)


def registered_profiles():
    return [dict(vars(profile)) for profile in _PROFILES]


def get_profile(profile_id):
    for profile in _PROFILES:
        if profile.profile_id == profile_id:
            return profile
    raise ValueError('Application journey profile is not registered')


def profile_definition(*, repo_root, profile_id):
    """Return the exact platform plan and immutable obligation identities."""
    profile = get_profile(profile_id)
    if profile.plan:
        document = json.loads((Path(repo_root) / profile.plan).read_text(encoding='utf-8'))
        steps = document.get('steps') or []
        identities = [row.get('id') for row in steps]
        if (document.get('schema') != 'adaos.e2e.application_journey.v1' or not identities
                or not all(isinstance(value, str) and value for value in identities)
                or len(set(identities)) != len(identities)):
            raise ValueError('Registered platform plan has invalid obligation identities')
        return {'profile': dict(vars(profile)), 'obligations': identities, 'plan': document,
                'cases': ['wide', 'compact']}
    from .application_journeys import RETOUCH_OBLIGATIONS, RETOUCH_CASES
    return {'profile': dict(vars(profile)), 'obligations': list(RETOUCH_OBLIGATIONS),
            'cases': list(RETOUCH_CASES), 'plan': None}


def require_owned_plan_target(*, profile_id, scenario_id, task_id, webspace_id, checkpoint, pin):
    """Reuse the existing TEST checkpoint authority; never invent one here."""
    profile = get_profile(profile_id)
    if not profile.test_only or not profile.plan:
        raise ValueError('This profile does not use the owned TEST plan runner')
    if (not isinstance(task_id, str) or not task_id.startswith('task.')
            or pin.get('stage') != 'automation' or pin.get('revision') != task_id
            or pin.get('scenario_id') != scenario_id or pin.get('preview_webspace_id') != webspace_id
            or not checkpoint.get('cleanup', {}).get('test')
            or not checkpoint.get('context', {}).get('retain_test_projects')
            or not any(row.get('primary_ref') == 'scenario:' + scenario_id
                       for row in checkpoint.get('context', {}).get('owned_artifacts', []))):
        raise ValueError('Profile requires an exact task pin and retained owned TEST checkpoint')


def qualify_owned_plan_report(*, repo_root, profile_id, report, pin, source_unchanged, verifier_unchanged):
    """Grade registered steps and exact loaded target; a green flag is insufficient."""
    definition = profile_definition(repo_root=repo_root, profile_id=profile_id)
    if not definition['profile']['test_only']:
        raise ValueError('Use the registered profile-specific owner for this report')
    failures = []
    expected_identity = pin.get('expected_materialization') or {}
    if expected_identity.get('revision') != pin.get('revision') or not expected_identity.get('source_fingerprint'):
        failures.append('exact_materialization_pin_missing')
    samples = report.get('samples') or []
    if len(samples) != 2 or {row.get('layout') for row in samples} != {'wide', 'compact'}:
        failures.append('layout_cohort_incomplete')
    for sample in samples:
        expected = {row['id']: 'passed' if not row.get('layouts') or sample.get('layout') in row['layouts']
                    else 'not_applicable' for row in definition['plan']['steps']}
        checks = sample.get('checks') or []
        if (len(checks) != len(expected) or {row.get('id'): row.get('status') for row in checks} != expected):
            failures.append('registered_steps_incomplete:' + str(sample.get('layout')))
        if (sample.get('failure') or sample.get('errors') or sample.get('foreign_api_origins')
                or not sample.get('observed_materializations')
                or any(any(observed.get(key) != value for key,value in expected_identity.items())
                       for observed in sample.get('observed_materializations') or [])
                or not sample.get('browser_environment') or not sample.get('client_assets')
                or not all(row.get('digest') and row.get('size_bytes',0) > 0 for row in sample.get('client_assets') or [])):
            failures.append('execution_dependencies_incomplete:' + str(sample.get('layout')))
    if source_unchanged is not True: failures.append('candidate_changed')
    if verifier_unchanged is not True: failures.append('verifier_changed')
    return {'schema': 'adaos.builder.registered_journey_qualification.v1', 'profile': profile_id,
            'passed': report.get('passed') is True and not failures,
            'failures': failures, 'obligations': definition['obligations'],
            'scope': definition['profile']['scope'], 'application_ready_claim': False}


def evaluate_registered_application(*, scenario_id, **context):
    """Existing Retouch owner behind the registry; other targets need explicit admission."""
    if scenario_id != 'natural_retouch':
        return None
    get_profile('natural-retouch.v4')
    from .application_journeys import evaluate_retouch
    return evaluate_retouch(scenario_id=scenario_id, **context)


def automatic_profile_for(scenario_id):
    # TEST plan profiles require their own retained ownership checkpoint.
    return get_profile('natural-retouch.v4') if scenario_id == 'natural_retouch' else None
