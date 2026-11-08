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
    initial_data_widgets: tuple[str, ...] = ()


_PROFILES = (
    JourneyProfile('applications.catalog.v1', 'applications', 'e2e/stand/browser/builder-candidate-feedback.mjs', None,
                   'Authoritative installed catalog first paint; full lifecycle acceptance is a separate obligation',
                   initial_data_widgets=('applications-list',)),
    JourneyProfile('natural-retouch.v6', 'natural_retouch', 'e2e/stand/qualify-retouch-browser.py', None,
                   'Layered workbench, native coordinates, independent alpha, durable queue, request allowance and exports; synthetic matte/provider; live quality excluded'),
    JourneyProfile('natural-retouch.v5', 'natural_retouch', 'e2e/stand/qualify-retouch-browser.py', None,
                   'Direct workspace flow; real handlers/SQLite/CPU and durable queue with synthetic provider; live quality excluded'),
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
    if profile.initial_data_widgets:
        return {'profile': dict(vars(profile)), 'obligations': ['initial-data:' + widget for widget in profile.initial_data_widgets],
                'cases': ['wide', 'compact'], 'plan': None}
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
    from .application_journeys import retouch_obligations, RETOUCH_CASES
    obligations = retouch_obligations(profile_id)
    return {'profile': dict(vars(profile)), 'obligations': list(obligations),
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
    generic_report = context.pop('generic_report', None) or {}
    if scenario_id == 'applications':
        profile = get_profile('applications.catalog.v1')
        samples = generic_report.get('samples') or []
        failures = []
        def fail(layout, message):
            failures.append({'layout': layout, 'active': 'initial', 'failure': message})
        if len(samples) != 2 or {s.get('layout') for s in samples} != {'wide', 'compact'}:
            fail('', 'layout_cohort_incomplete')
        for sample in samples:
            initial = [c for c in sample.get('checks', []) if c.get('kind') == 'authoritative-data-settlement' and c.get('checkpoint') == 'initial']
            ready = {s.get('id') for c in initial for s in c.get('states', [])
                     if s.get('state') == 'ready' and s.get('has_value') == 'true'
                     and s.get('kind') in {'api', 'mcp', 'skill', 'resourceQuery'}}
            for widget in profile.initial_data_widgets:
                if widget not in ready:
                    fail(sample.get('layout'), 'missing_required_initial_data:' + widget)
            if sample.get('hard_failures') or sample.get('foreign_api_origins'):
                fail(sample.get('layout'), 'runner_postconditions_failed')
            expected = context.get('expected_materialization') or {}
            if not expected or any((sample.get('observed_materialization') or {}).get(k) != v for k,v in expected.items()):
                fail(sample.get('layout'), 'materialization_pin_missing_or_foreign')
        return {'ok': generic_report.get('passed') is True and not failures, 'profile': profile.profile_id,
                'failures': failures, 'scope': profile.scope, 'application_ready_claim': False}
    if scenario_id != 'natural_retouch':
        return None
    profile_id = retouch_profile_for_sources(context.get('implementation_paths', ()))
    get_profile(profile_id)
    from .application_journeys import evaluate_retouch
    return evaluate_retouch(scenario_id=scenario_id, profile_id=profile_id, **context)


def retouch_profile_for_sources(paths):
    """Choose only a Core-owned contract; a candidate cannot name its runner."""
    import yaml
    expected = {'workspace_query': 'handlers.workspace:workspace_query',
                'workspace_command': 'handlers.workspace:workspace_command',
                'workspace_run': 'handlers.workspace:workspace_run',
                'workspace_draft': 'handlers.workspace:workspace_draft'}
    sources = [Path(p) for p in paths if Path(p).name == 'natural_retouch_skill']
    if len(sources) == 1:
        try:
            manifest = yaml.safe_load((sources[0] / 'skill.yaml').read_text(encoding='utf-8'))
            tools = {row['name']: row.get('entry') for row in manifest.get('tools', [])}
            if all(tools.get(name) == entry for name, entry in expected.items()):
                return 'natural-retouch.v6'
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
            pass
    return 'natural-retouch.v5'


def automatic_profile_for(scenario_id):
    # TEST plan profiles require their own retained ownership checkpoint.
    if scenario_id == 'applications':
        return get_profile('applications.catalog.v1')
    return get_profile('natural-retouch.v5') if scenario_id == 'natural_retouch' else None
