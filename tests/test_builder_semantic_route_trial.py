import json
from pathlib import Path

import pytest

from adaos.e2e.semantic_route_trial import ORDER, PROFILES, execution_registration
from adaos.services.builder.deterministic_task import admit_facts
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder.generation_profiles import profile_metadata

ROOT=Path(__file__).parents[1]


@pytest.mark.parametrize('name', ['equipment-en','sensors-ru'])
def test_new_source_tasks_have_independent_complete_scalar_expectations(name):
    fixtures=ROOT/'e2e/builder/development/creation'
    case=json.loads((fixtures/'facts-expectations.json').read_text(encoding='utf-8'))[name]
    source=(fixtures/case['prompt']).read_text(encoding='utf-8')
    intent=capture_intent(source,project_ref='project:fresh-source-check',locale='ru' if name.endswith('ru') else 'en')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    _,brief,receipt=admit_facts(task,intent)
    facts=brief['task_contract']['facts']
    assert {(row['subject'],row['source_field']):row['value'] for row in facts}=={
        (row['subject'],field):value for row in case['records'] for field,value in row['fields'].items()}
    assert receipt['provider_calls']==0 and receipt['fresh_source_admitted']


def test_series_is_counterbalanced_and_within_separate_48_call_allowance():
    assert sum(PROFILES[row[1]][1] for row in ORDER)==20
    assert 20+20+8==48
    for task in {'equipment-en','sensors-ru'}:
        for arm in ('S1','S2'):
            assert sorted(row[2] for row in ORDER if row[0]==task and row[1]==arm)==[1,2]
    for arm,(name,cap) in PROFILES.items():
        meta=profile_metadata(name)
        assert meta['builder_provider_call_limit']==cap
        assert meta['builder_context_retrieval']=='inline'
        assert meta.get('builder_task_interpretation_mode')==('deterministic_facts' if arm=='S1' else None)
        assert meta['builder_interpretation_output_mode']==('typed' if arm=='S1' else 'bound_json_text')


def test_unrelated_checkout_commits_are_not_context_or_oracle_changes():
    import copy
    original={'pins':{'evaluation_commit':'old','files':{'core.py':'fixed',
        'src/adaos/e2e/semantic_route_trial.py':'old-driver','oracle.mjs':'fixed'}}}
    changed=copy.deepcopy(original);changed['pins']['evaluation_commit']='new-gitlink-only-commit'
    assert execution_registration(original)==execution_registration(changed)
    changed['pins']['files']['core.py']='changed'
    assert execution_registration(original,harness_amendment=True)!=execution_registration(changed,harness_amendment=True)
    changed=copy.deepcopy(original);changed['pins']['files']['src/adaos/e2e/semantic_route_trial.py']='new-driver'
    assert execution_registration(original)!=execution_registration(changed)
    assert execution_registration(original,harness_amendment=True)==execution_registration(changed,harness_amendment=True)
    changed['pins']['files']['oracle.mjs']='weakened-grader'
    assert execution_registration(original,harness_amendment=True)!=execution_registration(changed,harness_amendment=True)
