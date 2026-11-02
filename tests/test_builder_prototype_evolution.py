import json
from pathlib import Path

from adaos.e2e.prototype_evolution_trial import LIMITS, PROFILES, expected_records, confirmed_turn
from adaos.services.builder.generation_profiles import profile_metadata
from adaos.services.builder.deterministic_task import admit_facts
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief

ROOT=Path(__file__).resolve().parents[1]


def test_evolution_seed_is_genuinely_admitted_from_source_and_not_a_seeded_abi():
    ladder=json.loads((ROOT/'e2e/builder/development/evolution/work-queue.v1.json').read_text(encoding='utf-8'))
    intent=capture_intent(ladder['steps'][0]['prompt'],project_ref='project:test-evolution',locale='en')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    _,brief,receipt=admit_facts(task,intent)
    assert receipt['provider_calls']==0
    facts=brief['task_contract']['facts']
    for record in expected_records(ladder,0):
        for field,value in record['fields'].items():
            matches=[row for row in facts if row['subject']==record['subject'] and row['source_field']==field]
            assert len(matches)==1 and matches[0]['value']==value


def test_evolution_never_discards_the_source_expected_when_new_directories_are_added():
    ladder=json.loads((ROOT/'e2e/builder/development/evolution/work-queue.v1.json').read_text(encoding='utf-8'))
    old=expected_records(ladder,0)
    evolved=expected_records(ladder,4)
    assert len(evolved)==5
    assert old==ladder['records'] and 'team_id' not in old[0]['fields']
    assert evolved[0]['fields']['team_id']=='T-A'
    assert evolved[3]['fields']=={'name':'Mechanical','active':True}


def test_separate_45_call_allowance_and_effort_controls_do_not_select_a_paid_api():
    assert sum(LIMITS.values())==45
    assert 1+8*2==LIMITS['exploration']
    for effort,profile in PROFILES.items():
        options=profile_metadata(profile)
        assert options['builder_provider_call_limit']==2
        assert options['builder_interpretation_output_mode']=='typed'
        assert options.get('builder_llm_reasoning_effort','low')==effort
        assert not {'model','resource','account','api_key'} & set(options)


def test_oracle_correction_preserves_the_original_and_cannot_adopt_a_chat_failure(tmp_path):
    import hashlib
    turn = {'functional_success': False, 'chat_ok': True, 'accounting_findings': [], 'actual_revision': '002'}
    path = tmp_path/'result.json'
    path.write_text(json.dumps(turn), encoding='utf-8')
    receipt = {'model_calls': 0, 'oracle_ok': True, 'same_accepted_revision': '002',
        'cause': 'harness_css_caption_casing_false_negative',
        'original_result_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    correction = tmp_path/'oracle-revalidation-accepted.json'
    correction.write_text(json.dumps(receipt), encoding='utf-8')
    assert confirmed_turn(path)
    assert json.loads(path.read_text()) == turn
    receipt['model_calls'] = 1
    correction.write_text(json.dumps(receipt), encoding='utf-8')
    assert not confirmed_turn(path)
    receipt['model_calls'] = 0
    receipt['original_result_sha256'] = 'wrong'
    correction.write_text(json.dumps(receipt), encoding='utf-8')
    assert not confirmed_turn(path)
