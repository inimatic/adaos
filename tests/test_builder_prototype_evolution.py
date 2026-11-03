import json
import pytest
from pathlib import Path

from adaos.e2e.prototype_evolution_trial import LIMITS, PROFILES, expected_records, confirmed_turn, qualified_previous_revision, continuation_authority
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


def test_prospective_preservation_probe_is_not_another_feature_or_a_silent_rewrite_of_prior_steps():
    from adaos.e2e.prototype_evolution_trial import IMPLEMENTED_CHECKS
    ladder=json.loads((ROOT/'e2e/builder/development/evolution/work-queue.v1.json').read_text(encoding='utf-8'))
    probe=ladder['steps'][10]
    assert probe['id']=='explicit-preservation-protocol'
    assert probe['prompt'].startswith('Preserve all existing behavior unchanged.')
    assert 'explicit_reaffirmation' in probe['checks'] and set(probe['checks'])<=IMPLEMENTED_CHECKS
    assert expected_records(ladder,10)==expected_records(ladder,9)
    assert not probe.get('extra_records') and not probe.get('extra_fields')


def test_separate_45_call_allowance_and_effort_controls_do_not_select_a_paid_api():
    assert sum(LIMITS.values())==45
    assert 1+7*2==LIMITS['exploration']
    for effort,profile in PROFILES.items():
        options=profile_metadata(profile)
        assert options['builder_provider_call_limit']==2
        assert options['builder_interpretation_output_mode']=='typed'
        assert options.get('builder_llm_reasoning_effort','low')==effort
        assert not {'model','resource','account','api_key'} & set(options)


def test_new_80_call_allowance_is_separate_and_does_not_change_old_limits():
    from adaos.e2e.prototype_evolution_trial import MATURITY_LIMITS
    assert sum(MATURITY_LIMITS.values()) == 80
    assert sum(LIMITS.values()) == 45
    assert MATURITY_LIMITS == {'exploration': 24, 'reasoning': 24, 'diagnostic': 32}


@pytest.mark.parametrize('cause', ['harness_css_caption_casing_false_negative',
                                'harness_board_event_owner_assertion_false_negative'])
def test_oracle_correction_preserves_the_original_and_cannot_adopt_a_chat_failure(tmp_path, cause):
    import hashlib
    turn = {'functional_success': False, 'chat_ok': True, 'accounting_findings': [], 'actual_revision': '002'}
    path = tmp_path/'result.json'
    path.write_text(json.dumps(turn), encoding='utf-8')
    receipt = {'model_calls': 0, 'oracle_ok': True, 'same_accepted_revision': '002',
        'cause': cause,
        'original_result_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    correction = tmp_path/'oracle-revalidation-accepted.json'
    correction.write_text(json.dumps(receipt), encoding='utf-8')
    assert confirmed_turn(path)
    assert json.loads(path.read_text()) == turn
    receipt['model_calls'] = 1
    correction.write_text(json.dumps(receipt), encoding='utf-8')
    assert not confirmed_turn(path)
    for update in ({'chat_ok': False}, {'accounting_findings': ['unjoined_attempts']}, {'actual_revision': '003'}):
        changed = {**turn, **update}
        path.write_text(json.dumps(changed), encoding='utf-8')
        receipt.update(model_calls=0, original_result_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        correction.write_text(json.dumps(receipt), encoding='utf-8')
        assert not confirmed_turn(path)
    receipt['model_calls'] = 0
    receipt['original_result_sha256'] = 'wrong'
    correction.write_text(json.dumps(receipt), encoding='utf-8')
    assert not confirmed_turn(path)


def test_evolution_chain_retains_failed_publication_before_qualified_diagnostic(tmp_path):
    import hashlib
    import pytest
    def save(relative, value):
        path = tmp_path/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path
    seed = {'project': 'owned', 'level': 0, 'phase': 'exploration', 'base_revision': None,
            'expected_revision': '001', 'actual_revision': '001', 'functional_success': True}
    save('turns/exploration-00-low/result.json', seed)
    failed = {**seed, 'level': 1, 'base_revision': '001', 'expected_revision': '002',
              'actual_revision': '002', 'functional_success': False}
    save('turns/exploration-01-low/result.json', failed)
    retry = {**failed, 'phase': 'diagnostic', 'base_revision': '002', 'expected_revision': '003',
             'actual_revision': '003', 'functional_success': True}
    path = save('turns/diagnostic-01-low-fix/result.json', retry)
    receipt = {'result_ref': str(path.relative_to(tmp_path)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    save('accepted-level-01.json', receipt)
    assert qualified_previous_revision(tmp_path, 2, 'owned') == '003'
    for patch in ({'base_revision': '004'}, {'project': 'foreign'}, {'actual_revision': '004'}, {'level': 2}):
        save(str(path.relative_to(tmp_path)), {**retry, **patch})
        receipt['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        save('accepted-level-01.json', receipt)
        with pytest.raises(ValueError):
            qualified_previous_revision(tmp_path, 2, 'owned')


def test_continuation_links_exact_origin_without_importing_or_resetting_its_allowance(tmp_path):
    import hashlib
    import pytest
    origin=tmp_path/'e2e/artifacts/builder/closed-24'
    origin.mkdir(parents=True)
    project='test_creation_'+hashlib.sha256((origin.name+':repair-queue').encode()).hexdigest()[:18]
    registration=origin/'preregistration.json'
    registration.write_text(json.dumps({'project':project,'maximum_calls':24}),encoding='utf-8')
    (origin/'final-summary.json').write_text(json.dumps({'totals':{'calls':20}}),encoding='utf-8')
    result=continuation_authority(tmp_path,origin)
    assert result['project']==project and result['allowance_inheritance'] is False
    assert result['preregistration_sha256']==hashlib.sha256(registration.read_bytes()).hexdigest()
    registration.write_text(json.dumps({'project':'foreign','maximum_calls':24}),encoding='utf-8')
    with pytest.raises(ValueError):continuation_authority(tmp_path,origin)
    with pytest.raises(ValueError):continuation_authority(tmp_path,tmp_path/'foreign')


def test_evolution_chain_requires_every_failed_publication_before_corrective_feedback(tmp_path):
    import hashlib
    def save(relative, value):
        path=tmp_path/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return {'result_ref': relative, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    original = {'project':'owned', 'level':0, 'phase':'exploration', 'base_revision':None,
        'expected_revision':'001', 'actual_revision':None, 'functional_success':False}
    save('turns/exploration-00-low/result.json', original)
    intermediate = {**original, 'phase':'reasoning', 'actual_revision':'001'}
    witness = save('turns/reasoning-00-medium/result.json', intermediate)
    final = {**original, 'phase':'diagnostic', 'base_revision':'001',
        'expected_revision':'002', 'actual_revision':'002', 'functional_success':True,
        'turn_kind':'corrective_feedback'}
    receipt = save('turns/diagnostic-00-medium/result.json', final)
    adoption = tmp_path/'accepted-level-00.json'
    def adopt(value): adoption.write_text(json.dumps(value), encoding='utf-8')
    adopt(receipt)
    with pytest.raises(ValueError, match='discontinuous'):
        qualified_previous_revision(tmp_path, 1, 'owned')
    adopt({**receipt, 'intermediate_results':[witness]})
    assert qualified_previous_revision(tmp_path, 1, 'owned') == '002'
    for entries in ([{**witness, 'sha256':'wrong'}], [witness, witness],
                    [{**witness, 'result_ref':'../foreign/result.json'}]):
        adopt({**receipt, 'intermediate_results':entries})
        with pytest.raises(ValueError): qualified_previous_revision(tmp_path, 1, 'owned')
    changed={**intermediate, 'project':'foreign'}
    witness=save('turns/reasoning-00-medium/result.json', changed)
    adopt({**receipt, 'intermediate_results':[witness]})
    with pytest.raises(ValueError): qualified_previous_revision(tmp_path, 1, 'owned')
