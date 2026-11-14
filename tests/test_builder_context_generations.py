"""A long-lived development policy must not retain every source generation."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.builder import automation


@pytest.mark.parametrize('legacy_history', [False, True])
def test_changed_source_context_stays_bounded_and_old_runs_remain_replayable(tmp_path, monkeypatch, legacy_history):
    service = BuilderAutomationService(
        state_dir=tmp_path / 'state', repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / 'skills', dev_scenarios_root=tmp_path / 'scenarios',
    )
    policy = {
        'session_id': 'development.demo', 'project_ref': 'project:demo',
        'created_at': '2026-11-13T00:00:00Z',
        'targets': {'primary': [{'ref': 'skill:demo'}], 'secondary': []},
        'handoff': {'request': 'Preserve existing behavior while implementing the change.'},
    }
    monkeypatch.setattr(BuilderAutomationService, '_load_development_session', lambda *args, **kwargs: (policy, {}))
    contexts = service._contexts()
    def compile_generation(generation, *, iteration=None):
        digest = 'sha256:' + f'{generation:064x}'
        control = service._compile_iteration_context(
            session={'session_id': 'automation.demo', 'development_session_id': policy['session_id'],
                     'iteration': iteration or generation, 'change_set_id': 'change.demo'},
            kind='skill', project_id='demo',
            context_packet={'schema': 'adaos.builder.context_packet.v1', 'digest': digest,
                            'project': {'ref': 'project:demo'}, 'base': {'generation': generation},
                            'change': {'change_id': 'change.demo', 'status': 'active', 'issues': []},
                            'allowed_paths': ['skills/demo/'], 'facets': {}},
            source_snapshot={'digest': digest, 'created_at': '2026-11-13T00:00:00Z'},
            implementation_brief=policy['handoff']['request'],
        )
        return digest, control

    legacy_plan = None
    if legacy_history:
        # Reproduce the old writer's shared policy capsule and accumulated
        # required edges. The upgraded writer must isolate new attempts without
        # revoking that history or rewriting its immutable plans.
        register = type(contexts).register_capsule

        def legacy_register(owner, capsule, **kwargs):
            capsule = copy.deepcopy(capsule)
            if capsule.get('kind') == 'development_session':
                capsule['source_digests'].pop('project_capsule', None)
            return register(owner, capsule, **kwargs)

        with monkeypatch.context() as legacy_writer:
            legacy_writer.setattr(type(contexts), 'register_capsule', legacy_register)
            compile_generation(1001)
            _, old_control = compile_generation(1002)
        old_plan = contexts.get_plan(old_control['plan_id'])
        assert len([row for row in old_plan['selected'] if row['kind'] == 'project']) == 2
        legacy_plan = old_control['plan_id'], copy.deepcopy(old_plan)

    retained = []
    previous_policy = None
    # Returning to an earlier source is a new attempt. The policy must bind the
    # exact selected project capsule even if its source projection repeats.
    for iteration, generation in enumerate([*range(1, 21), 1, 1, 20, 1], 1):
        digest, control = compile_generation(generation, iteration=iteration)
        plan = contexts.get_plan(control['plan_id'])
        projects = [row for row in plan['selected'] if row['kind'] == 'project']
        assert len(projects) == 1, 'Current context includes a previous source generation'
        assert contexts.get_capsule(projects[0]['ref'])['source_digests']['source_snapshot'] == digest
        assert len(plan['selected']) <= 4
        policies = [row for row in plan['selected'] if row['kind'] == 'development_session']
        assert len(policies) == 1
        if previous_policy and previous_policy[0] == generation:
            assert policies[0]['ref'] == previous_policy[1]
        previous_policy = generation, policies[0]['ref']
        task = next(ref for ref in control['capsule_refs'] if contexts.get_capsule(ref)['kind'] == 'task')
        retained.append((task, digest, control['plan_id'], copy.deepcopy(plan)))

    for task, digest, plan_id, original_plan in retained:
        assert contexts.get_plan(plan_id) == original_plan
        replay = contexts.resolve({'subject_refs': [task], 'purpose': 'builder.automation', 'audience': 'builder'})
        projects = [row for row in replay['required'] if row['kind'] == 'project']
        assert len(projects) == 1
        assert contexts.get_capsule(projects[0]['ref'])['source_digests']['source_snapshot'] == digest
    if legacy_plan:
        assert contexts.get_plan(legacy_plan[0]) == legacy_plan[1]


def test_changed_platform_does_not_mutate_a_project_generation(tmp_path, monkeypatch):
    service = BuilderAutomationService(
        state_dir=tmp_path / 'state', repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / 'skills', dev_scenarios_root=tmp_path / 'scenarios',
    )
    contexts = service._contexts()

    def compile_iteration(iteration):
        return service._compile_iteration_context(
            session={'session_id': 'automation.platform', 'iteration': iteration, 'change_set_id': 'change.demo'},
            kind='skill', project_id='demo', context_packet={'project': {'ref': 'project:demo'}},
            source_snapshot={'digest': 'sha256:' + 'a' * 64}, implementation_brief='Preserve behavior.',
        )

    original = compile_iteration(1)
    original_plan = copy.deepcopy(contexts.get_plan(original['plan_id']))
    old_task = next(ref for ref in original['capsule_refs'] if contexts.get_capsule(ref)['kind'] == 'task')
    previous = automation.BUILD_INFO
    monkeypatch.setattr(automation, 'BUILD_INFO', SimpleNamespace(
        git_commit='b' * 40, version=previous.version, build_date=previous.build_date,
    ))
    current = contexts.get_plan(compile_iteration(2)['plan_id'])
    platforms = [row for row in current['selected'] if row['kind'] == 'platform']
    assert len(platforms) == 1
    assert contexts.get_capsule(platforms[0]['ref'])['source_digests']['core'] == 'git:' + 'b' * 40
    assert contexts.get_plan(original['plan_id']) == original_plan
    replay = contexts.resolve({'subject_refs': [old_task], 'purpose': 'builder.automation', 'audience': 'builder'})
    old_platforms = [row for row in replay['required'] if row['kind'] == 'platform']
    assert len(old_platforms) == 1
    assert old_platforms[0]['ref'] != platforms[0]['ref']
