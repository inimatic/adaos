from types import SimpleNamespace

import pytest

from adaos.sdk.builder import applications, artifacts, workflow
from adaos.services.applications.development import ApplicationDevelopmentCoordinator
from adaos.services.applications.store import ApplicationStore


@pytest.fixture
def source_trial(tmp_path, monkeypatch):
    calls = []
    checkpoint = {'change_id': 'changed-requirements', 'operation_id': 'source-1',
                  'source_snapshot_digest': 'sha256:' + 'a' * 64}
    evidence = {'status': 'passed', 'source_snapshot_digest': checkpoint['source_snapshot_digest'],
                'regression_evidence': ['suite:changed-requirements'], 'refs': ['suite:changed-requirements']}
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    # A source-checkpoint Trial must not need mutable authoring or a current
    # primary Scenario. The exact primary belongs to the frozen Project.
    monkeypatch.setattr(applications, '_application', lambda *_: SimpleNamespace(application_id='mail', legacy_project_id='mail'))
    monkeypatch.setattr(applications, '_coordinator', lambda: coordinator)
    monkeypatch.setattr(applications, '_admit_builder_mutation', lambda *a, **k: None)
    monkeypatch.setattr(applications, 'production_webspace_id', lambda value: value)
    monkeypatch.setattr(workflow, 'get_state', lambda *a, **k: pytest.fail('mutable workflow read'))
    def prepared(**kwargs):
        calls.append(('prepare', kwargs))
        return {'ok': True, 'candidate': {'candidate_id': 'candidate-1', 'project_id': 'mail',
            'package_digest': 'sha256:package', 'release_digest': 'sha256:release',
            'source_ref': {'revision': 'b' * 40}}, 'release': {'project_id': 'mail', 'release_digest': 'sha256:release'}}
    monkeypatch.setattr(artifacts, 'prepare_project_candidate', prepared)
    monkeypatch.setattr(applications, 'verify_candidate_access', lambda *a, **k: calls.append(('verify', k)) or {'status': 'passed'})
    monkeypatch.setattr(applications, 'place_local_trial', lambda *a, **k: calls.append(('place', k)) or {'ok': True})
    args = dict(actor_ref='user:owner', subnet_ref='subnet:home', capability='applications.develop',
        expected_revision=1, idempotency_key='trial-1', source_webspace_id='desktop',
        verification_evidence=evidence, source_checkpoint=checkpoint, permission_decision={'approved': True})
    return coordinator, args, calls


def test_source_trial_joins_frozen_preparation_access_and_native_placement_with_durable_progress(source_trial, tmp_path):
    coordinator, args, calls = source_trial
    result = applications.create_trial('mail', **args)
    assert result['status'] == 'succeeded'
    assert [kind for kind, _ in calls] == ['prepare', 'verify', 'place']
    assert calls[0][1]['project_id'] == 'mail'
    assert calls[0][1]['operation_id'] == 'source-1'
    assert calls[0][1]['validation_evidence'] == args['verification_evidence']
    assert calls[1][1]['evidence']['source_commit'] == 'b' * 40
    assert calls[1][1]['application_id'] == calls[2][1]['application_id'] == 'mail'
    assert applications.create_trial('mail', **args) == result
    assert len(calls) == 3
    events, _ = ApplicationStore(tmp_path).list_operation_events(application_id='mail')
    assert [e['status'] for e in events] == ['planned', 'applying', 'succeeded']
    assert events[-1]['operation']['source_checkpoint'] == args['source_checkpoint']
    # A repeated public request does not read a later mutable workflow or
    # require the original Application revision to remain current.
    applications.create_trial('mail', **args)
    assert len(calls) == 3


def test_source_only_qualification_reuses_the_existing_access_report(source_trial):
    _, args, calls = source_trial
    args['verification_evidence'] = {'status': 'passed',
        'source_snapshot_digest': args['source_checkpoint']['source_snapshot_digest']}
    applications.create_trial('mail', **args)
    assert calls[0][1]['validation_evidence'] == args['verification_evidence']
    assert calls[1][1]['evidence'] is None


def test_later_access_evidence_does_not_rebind_frozen_source_qualification(source_trial):
    _, args, calls = source_trial
    frozen = dict(args['verification_evidence'])
    access = {'regression_evidence': ['browser:exact-candidate'],
              'access_matrix_evidence': ['verification:qualified-access-contract']}
    args['verification_evidence']['application_verification'] = access
    applications.create_trial('mail', **args)
    assert calls[0][1]['validation_evidence'] == frozen
    assert calls[1][1]['evidence'] == {**access, 'source_commit': 'b' * 40}


@pytest.mark.parametrize('fault', [None, 'profile', 'source_commit', 'snapshot', 'failed_tests'])
def test_access_carry_forward_requires_unchanged_profile_and_bound_frozen_tests(fault):
    from adaos.services.applications import ApplicationAccessError
    profile = SimpleNamespace(digest='same-profile')
    source = {'status': 'passed', 'receipt_digest': 'sha256:' + 'c' * 64,
        'source_snapshot_digest': 'sha256:' + 'd' * 64}
    custody = {'commit': 'b' * 40, 'snapshot_digest': source['source_snapshot_digest']}
    if fault == 'source_commit': custody['commit'] = 'e' * 40
    if fault == 'snapshot': custody['snapshot_digest'] = 'sha256:' + 'e' * 64
    if fault == 'failed_tests': source['status'] = 'failed'
    candidate = SimpleNamespace(base_release_digest='base', release_digest='target',
        source_ref=SimpleNamespace(revision='b' * 40), validation_evidence=({'status': 'passed',
            'source_validation': source, 'source_custody': custody},))
    calls = []
    report = {'release_digest': 'base', 'permission_profile_digest': profile.digest,
        'overall': 'passed', 'report_digest': 'prior-report', 'release_scope': 'publication'}
    management = SimpleNamespace(list_verification_reports=lambda _: [report],
        final_verification=lambda *a, **kw: calls.append(kw) or {
            'publication_allowed': True, 'report': {'report_digest': 'new-report'}})
    release = SimpleNamespace(permission_profile=SimpleNamespace(digest=profile.digest,
        flat_permissions=('workspace.read',)))
    old = SimpleNamespace(permission_profile=SimpleNamespace(digest='changed' if fault == 'profile' else profile.digest))
    distribution = SimpleNamespace(applications=SimpleNamespace(store=SimpleNamespace(get_release=lambda *a: old)))
    kwargs = dict(application_id='mail', candidate=candidate, candidate_release=release, actor_ref='user:owner')
    if fault:
        with pytest.raises(ApplicationAccessError):
            applications._carry_forward_unchanged_access_verification(management, distribution, **kwargs)
        assert not calls
    else:
        result = applications._carry_forward_unchanged_access_verification(management, distribution, **kwargs)
        assert result['base_report_digest'] == 'prior-report'
        assert calls[0]['regression_evidence'] == ('source-validation:' + source['receipt_digest'],)


def test_source_trial_recovery_keeps_original_checkpoint_after_new_authoring_edits(source_trial, monkeypatch):
    coordinator, args, calls = source_trial
    activated = set()
    def lost_reply(candidate, **kwargs):
        activated.add(candidate)
        raise TimeoutError('lost placement reply')
    monkeypatch.setattr(applications, 'place_local_trial', lost_reply)
    with pytest.raises(TimeoutError):
        applications.create_trial('mail', **args)
    operation = coordinator.list('mail')[0]
    args['source_checkpoint']['operation_id'] = 'newer-local-operation'
    monkeypatch.setattr(applications, 'place_local_trial', lambda candidate, **kwargs: {'ok': candidate in activated})
    result = applications.reconcile_development_operation(operation['operation_id'], actor_ref='user:owner',
        subnet_ref='subnet:home', capability='applications.recover')
    assert result['status'] == 'succeeded'
    assert activated == {'candidate-1'}
    assert [value['operation_id'] for kind, value in calls if kind == 'prepare'] == ['source-1', 'source-1']


@pytest.mark.parametrize('fault', ['missing_evidence', 'different_snapshot', 'invalid_checkpoint', 'invalid_access_evidence'])
def test_source_trial_rejects_unqualified_inputs_without_generation_or_placement(source_trial, fault):
    _, args, calls = source_trial
    if fault == 'missing_evidence':
        args['verification_evidence'] = None
    elif fault == 'different_snapshot':
        args['verification_evidence']['source_snapshot_digest'] = 'sha256:' + 'f' * 64
    elif fault == 'invalid_access_evidence':
        args['verification_evidence']['application_verification'] = 'not a record'
    else:
        args['source_checkpoint']['unexpected_project'] = 'another-app'
    with pytest.raises(ValueError):
        applications.create_trial('mail', **args)
    assert calls == []


def test_source_trial_refuses_another_candidate_before_access_or_data_placement(source_trial, monkeypatch):
    _, args, calls = source_trial
    monkeypatch.setattr(artifacts, 'prepare_project_candidate', lambda **kwargs: {
        'ok': True, 'candidate': {'candidate_id': 'foreign', 'project_id': 'other'},
        'release': {'project_id': 'other'}})
    with pytest.raises(ValueError, match='another Candidate'):
        applications.create_trial('mail', **args)
    assert calls == []


@pytest.mark.parametrize('missing', [True, False])
def test_exact_application_access_never_recreates_a_missing_or_rebound_application(monkeypatch, missing):
    def get_application(application_id):
        assert application_id == 'mail'
        if missing:
            raise FileNotFoundError(application_id)
        return SimpleNamespace(application_id='mail', legacy_project_id='another-project')

    monkeypatch.setattr(applications, '_application_service', lambda: SimpleNamespace(
        store=SimpleNamespace(get_application=get_application)))
    monkeypatch.setattr(applications, '_ensure_application_for_project',
                        lambda *a, **k: pytest.fail('silently recreated an Application'))
    monkeypatch.setattr(applications, '_distribution_service',
                        lambda: pytest.fail('verification started with another source identity'))
    with pytest.raises(FileNotFoundError if missing else ValueError):
        applications.verify_candidate_access('mail', 'candidate-1', application_id='mail',
            actor_ref='user:owner', evidence={'status': 'passed'})


def test_ordinary_trial_operation_retains_automation_basis_across_restart_and_new_authoring(tmp_path, monkeypatch):
    from adaos.sdk.builder import lifecycle
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    current = {'change': {'change_id': 'accepted-change'},
               'automation': {'status': 'completed', 'head_task_id': 'accepted-task'},
               'delivery': {'status': 'checkpoint', 'source_revision': 'a' * 40}}
    monkeypatch.setattr(applications, '_coordinator', lambda: coordinator)
    monkeypatch.setattr(applications, '_application', lambda *a: SimpleNamespace(
        application_id='mail', legacy_project_id='mail', entrypoints=({'presentation_ref': 'scenario:mail'},)))
    monkeypatch.setattr(applications, '_admit_builder_mutation', lambda *a, **k: None)
    monkeypatch.setattr(workflow, 'get_state', lambda *a: current)
    observed = []
    def prepare(*a, **kwargs):
        observed.append(kwargs['_workflow_basis'])
        if len(observed) == 1:
            raise TimeoutError('lost ordinary Trial reply')
        assert kwargs['application_id'] == 'mail'
        return {'ok': True, 'candidate': {'candidate_id': 'same-source-candidate'}}
    monkeypatch.setattr(lifecycle, 'prepare_trial', prepare)
    with pytest.raises(TimeoutError):
        applications.create_trial('mail', actor_ref='user:owner', subnet_ref='subnet:home',
            capability='applications.develop', expected_revision=1, idempotency_key='ordinary-trial',
            verification_evidence={'source_commit': 'a' * 40})
    current['change']['change_id'] = 'new-authoring'
    current['automation'] = {'status': 'working', 'head_task_id': 'new-task'}
    operation = coordinator.list('mail')[0]
    result = applications.reconcile_development_operation(operation['operation_id'],
        actor_ref='user:owner', subnet_ref='subnet:home', capability='applications.recover')
    assert result['status'] == 'succeeded'
    assert [s['change']['change_id'] for s in observed] == ['accepted-change', 'accepted-change']
    assert [s['automation']['head_task_id'] for s in observed] == ['accepted-task', 'accepted-task']
