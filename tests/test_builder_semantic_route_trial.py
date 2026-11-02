import json
from pathlib import Path

import pytest

from adaos.e2e.semantic_route_trial import ORDER, PROFILES, cohort_order, cohort_profiles, execution_registration, allowance_identity
from adaos.services.builder.deterministic_task import admit_facts
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder.generation_profiles import profile_metadata

ROOT=Path(__file__).parents[1]


@pytest.mark.parametrize('name', ['equipment-en','sensors-ru','parcels-en','readings-ru', 'samples-en', 'service-ru'])
def test_new_source_tasks_have_independent_complete_scalar_expectations(name):
    fixtures=ROOT/'e2e/builder/development/creation'
    case=json.loads((fixtures/('facts-maturity-exit-expectations.json' if name in {'samples-en', 'service-ru'} else
                              'facts-heldout-expectations.json' if name in {'parcels-en','readings-ru'}
                              else 'facts-expectations.json')).read_text(encoding='utf-8'))[name]
    source=(fixtures/case['prompt']).read_text(encoding='utf-8')
    intent=capture_intent(source,project_ref='project:fresh-source-check',locale='ru' if name.endswith('ru') else 'en')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    _,brief,receipt=admit_facts(task,intent)
    facts=brief['task_contract']['facts']
    assert {(row['subject'],row['source_field']):row['value'] for row in facts}=={
        (row['subject'],field):value for row in case['records'] for field,value in row['fields'].items()}
    assert receipt['provider_calls']==0 and receipt['fresh_source_admitted']


def test_maturity_cohort_has_new_tasks_counterbalanced_no_repair_and_separate_authority():
    order = cohort_order('maturity-exit-new-facts-8')
    profiles = cohort_profiles('maturity-exit-new-facts-8')
    assert len(order) == 8 and {row[0] for row in order} == {'samples-en', 'service-ru'}
    assert sum(profiles[row[1]][1] for row in order) == 12
    assert allowance_identity(80) == 'user-20261102-up-to80-complete-unfinished-semantic-maturity'
    for profile, _ in profiles.values():
        assert profile_metadata(profile)['builder_candidate_repair_limit'] == 0


def test_final_maturity_requalification_is_an_explicit_four_cell_cohort_not_a_truncated_repeat():
    original=cohort_order('maturity-exit-new-facts-8')
    name='maturity-exit-final-facts-4'
    final=cohort_order(name)
    assert final == [('samples-en','S1',1),('service-ru','S2',1),
                     ('samples-en','S2',1),('service-ru','S1',1)]
    profiles=cohort_profiles(name)
    assert sum(profiles[arm][1] for _,arm,_ in final)==6
    assert len(original)==8 and cohort_order('maturity-exit-new-facts-8')==original
    assert final != original[:4]
    for profile,_ in profiles.values():
        assert profile_metadata(profile)['builder_candidate_repair_limit']==0


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


def test_restart_continuation_is_a_separate_fixed_cohort_not_an_edited_prior_matrix():
    assert cohort_order('fresh-facts-8')==ORDER
    assert cohort_order('sensors-facts-4')==ORDER[4:]
    assert sum(PROFILES[row[1]][1] for row in cohort_order('sensors-facts-4'))==10
    assert 18+10<=48
    copy=cohort_order('fresh-facts-8');copy.clear()
    assert len(ORDER)==8
    with pytest.raises(ValueError,match='cohort'):
        cohort_order('unreviewed')


def test_behavior_regression_preserves_existing_source_and_oracle_without_hiding_its_scope():
    order=cohort_order('infrastructure-regression-2')
    assert order==[('infrastructure-ru','S2',1),('infrastructure-ru','S2',2)]
    assert sum(PROFILES[row[1]][1] for row in order)==6
    assert 28+6<=48
    fixture=(ROOT/'e2e/builder/development/creation/infrastructure-ru.txt').read_text(encoding='utf-8')
    assert all(value in fixture for value in ('RU-I1','RU-backup','created_at 2026-10-29','CPU 92'))


def test_typed_fix_qualification_has_two_calls_per_turn_and_cannot_silently_use_envelope_or_repair():
    name='infrastructure-typed-no-repair-2'
    assert cohort_order(name)==cohort_order('infrastructure-regression-2')
    profiles=cohort_profiles(name)
    assert sum(profiles[arm][1] for _,arm,_ in cohort_order(name))==4
    meta=profile_metadata(profiles['S2'][0])
    assert meta['builder_interpretation_output_mode']=='typed' and meta['builder_provider_call_limit']==2
    assert meta['builder_semantic_input_profile']=='semantic-input-v1'
    assert PROFILES['S2'][1]==3


def test_interrupted_attempt_is_terminal_but_not_active_work_or_success():
    from adaos.e2e.builder_creation_episode import TERMINAL
    from adaos.services.ai.service import TERMINAL as AI_TERMINAL
    assert AI_TERMINAL <= TERMINAL
    assert 'interrupted' in TERMINAL and not {'queued','running','unknown'} & TERMINAL


def test_additional_allowance_has_a_distinct_identity_and_no_repair_profiles():
    assert allowance_identity(24)!=allowance_identity(48)
    for bad in (True, 24.0, 25, '24'):
        with pytest.raises(ValueError):allowance_identity(bad)
    name='first-pass-typed-facts-8'
    assert cohort_order(name)==ORDER
    profiles=cohort_profiles(name)
    assert sum(profiles[arm][1] for _,arm,_ in ORDER)==12
    for arm,(profile,cap) in profiles.items():
        assert cap==(1 if arm=='S1' else 2)
        assert profile_metadata(profile)['builder_provider_call_limit']==cap
        assert profile_metadata(profile)['builder_interpretation_output_mode']=='typed'
    heldout=cohort_order('first-pass-heldout-facts-8')
    assert len(heldout)==8 and {row[0] for row in heldout}=={'parcels-en','readings-ru'}
    assert sum(cohort_profiles('first-pass-heldout-facts-8')[arm][1] for _,arm,_ in heldout)==12
    assert sum(cohort_profiles('first-pass-heldout-facts-4')[arm][1]
        for _,arm,_ in cohort_order('first-pass-heldout-facts-4'))==6
    assert cohort_order('heldout-s1-repeats-2')==[('parcels-en','S1',2),('readings-ru','S1',2)]
    assert 12 + 4 + 6 + 2 == 24
