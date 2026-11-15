import json

import pytest

from adaos.services.artifact_pipeline import ActivationError, ActivationReplayBlocked, PackageCatalog, build_project_release
from test_artifact_workspace_activation import _activate, _built_scenario, _built_skill, _manager, _plan, _source, _plan_with_skill


@pytest.mark.parametrize('affected_changed', [False, True])
@pytest.mark.parametrize('with_dependency', [False, True])
@pytest.mark.parametrize('failure_phase', ['workflow-bind', 'health-verify'])
def test_already_rolled_back_recovery_preserves_unrelated_workspace_advances(
        tmp_path, affected_changed, with_dependency, failure_phase):
    old = _built_scenario(tmp_path, version='1.0.0', marker='old')
    failed = _built_scenario(tmp_path, version='1.1.0', marker='failed')
    other = _built_scenario(tmp_path, version='1.0.0', marker='other', scenario_id='unrelated')
    store, manager = _manager(tmp_path)
    for package in (old, failed, other):
        store.put(package.archive_bytes)
    first_plan, failed_plan = _plan(old), _plan(failed)
    if with_dependency:
        first_skill = _built_skill(tmp_path, version='1.0.0', marker='first')
        next_skill = _built_skill(tmp_path, version='1.1.0', marker='next')
        for package in (first_skill, next_skill):
            store.put(package.archive_bytes)
        first_plan, failed_plan = _plan_with_skill(old, first_skill), _plan_with_skill(failed, next_skill)
    _activate(manager, first_plan, idempotency_key='initial', slot_id='recipes')
    def fail_before_switch(phase):
        if failure_phase == phase == 'workflow-bind':
            raise ActivationError('pre-switch failure')

    with pytest.raises(ActivationError, match='pre-switch failure|health check failed'):
        _activate(manager, failed_plan, idempotency_key='failed', slot_id='recipes',
            phase_hook=fail_before_switch,
            reload_runtime=lambda _: {'status': 'completed'}, health_check=lambda _: False)
    plan = build_project_release(project_id='unrelated', version='1.0.0', source_ref=_source(),
        components=(other.ref,), catalog=PackageCatalog())
    _activate(manager, plan, idempotency_key='unrelated', slot_id='unrelated')
    before = manager.lock_path.read_bytes()
    unrelated_before = (tmp_path / 'workspace/scenarios/unrelated/webui.json').read_bytes()
    affected = tmp_path / 'workspace/scenarios/recipes/webui.json'
    if affected_changed:
        affected.write_text(json.dumps({'marker': 'user edit'}), encoding='utf-8')
        with pytest.raises(ActivationError, match='changed'):
            manager.recover_interrupted(manager.operation_id('failed'))
    else:
        result = manager.recover_interrupted(manager.operation_id('failed'))
        assert result['status'] == 'recovered'
        assert result['recovery_receipt']['workspace_mutated'] is False
        assert result['recovery_receipt']['rollback_evidence'] == (
            'before_workspace_mutation' if failure_phase == 'workflow-bind' else 'runtime_rollback')
        assert json.loads(affected.read_text(encoding='utf-8')) == {'marker': 'old'}
    assert manager.lock_path.read_bytes() == before
    assert (tmp_path / 'workspace/scenarios/unrelated/webui.json').read_bytes() == unrelated_before


@pytest.mark.parametrize('consumer_changed', [False, True])
def test_pre_switch_recovery_verifies_unchanged_consumers_of_updated_skill(tmp_path, consumer_changed):
    scenario = _built_scenario(tmp_path, version='1.0.0', marker='old')
    old_skill = _built_skill(tmp_path, version='1.0.0', marker='old')
    new_skill = _built_skill(tmp_path, version='1.1.0', marker='new')
    other = _built_scenario(tmp_path, version='1.0.0', marker='other', scenario_id='unrelated')
    store, manager = _manager(tmp_path)
    for package in (scenario, old_skill, new_skill, other):
        store.put(package.archive_bytes)
    _activate(manager, _plan_with_skill(scenario, old_skill), idempotency_key='initial', slot_id='recipes')

    def fail(phase):
        if phase == 'workflow-bind':
            raise ActivationError('pre-switch failure')

    with pytest.raises(ActivationError, match='pre-switch failure'):
        _activate(manager, _plan_with_skill(scenario, new_skill), idempotency_key='failed',
                  slot_id='recipes', phase_hook=fail)
    plan = build_project_release(project_id='unrelated', version='1.0.0', source_ref=_source(),
        components=(other.ref,), catalog=PackageCatalog())
    _activate(manager, plan, idempotency_key='unrelated', slot_id='unrelated')
    lock_bytes = manager.lock_path.read_bytes()
    consumer = tmp_path / 'workspace/scenarios/recipes/webui.json'
    if consumer_changed:
        consumer.write_text('{"marker": "user edit"}', encoding='utf-8')
        with pytest.raises(ActivationError, match='changed'):
            manager.recover_interrupted(manager.operation_id('failed'))
    else:
        recovered = manager.recover_interrupted(manager.operation_id('failed'))
        assert recovered['recovery_receipt']['verified_components'] == ['scenario:recipes', 'skill:shopping']
    assert manager.lock_path.read_bytes() == lock_bytes
