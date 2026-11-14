"""A long-lived development policy must not retain every source generation."""
import copy
from pathlib import Path

import pytest

from adaos.services.builder.automation import BuilderAutomationService


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
    def compile_generation(generation):
        digest = 'sha256:' + f'{generation:064x}'
        control = service._compile_iteration_context(
            session={'session_id': 'automation.demo', 'development_session_id': policy['session_id'],
                     'iteration': generation, 'change_set_id': 'change.demo'},
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
                capsule['source_digests'].pop('project_context', None)
            return register(owner, capsule, **kwargs)

        with monkeypatch.context() as legacy_writer:
            legacy_writer.setattr(type(contexts), 'register_capsule', legacy_register)
            compile_generation(1001)
            _, old_control = compile_generation(1002)
        old_plan = contexts.get_plan(old_control['plan_id'])
        assert len([row for row in old_plan['selected'] if row['kind'] == 'project']) == 2
        legacy_plan = old_control['plan_id'], copy.deepcopy(old_plan)

    retained = []
    for generation in range(1, 21):
        digest, control = compile_generation(generation)
        plan = contexts.get_plan(control['plan_id'])
        projects = [row for row in plan['selected'] if row['kind'] == 'project']
        assert len(projects) == 1, 'Current context includes a previous source generation'
        assert contexts.get_capsule(projects[0]['ref'])['source_digests']['source_snapshot'] == digest
        assert len(plan['selected']) <= 4
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
