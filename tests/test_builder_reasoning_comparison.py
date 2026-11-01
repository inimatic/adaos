import copy
import json

import pytest

from adaos.e2e.reasoning_comparison import compare_reasoning_inputs


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def body():
    return {'model': 'selected-model', 'instructions': 'Сохрани исходное содержание.',
        'input': [{'role': 'user', 'content': 'generation-plan:original'}],
        'reasoning': {'effort': 'low'}, 'text': {'format': {'type': 'json_schema', 'strict': True}},
        'tools': [], 'store': False}


@pytest.mark.parametrize('cohort', ['historical_input', 'current_baseline'])
def test_only_actual_effort_bytes_can_define_the_treatment(cohort):
    before = body()
    after = copy.deepcopy(before)
    after['reasoning']['effort'] = 'medium'
    report = compare_reasoning_inputs(wire(before), wire(after), cohort=cohort,
        provenance='reconstructed_offline_projection')
    assert report['reasoning_only_pair_eligible']
    assert not report['historical_wire_observed']
    assert report['model_calls'] == 0 and not report['inference_submission_authorized']
    assert 'Сохрани' not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize('mutation', ['plan', 'model', 'schema', 'tools', 'instructions', 'reasoning_option'])
def test_extra_treatments_are_not_normalized_away(mutation):
    before = body()
    after = copy.deepcopy(before)
    after['reasoning']['effort'] = 'medium'
    if mutation == 'plan': after['input'][0]['content'] = 'generation-plan:resealed'
    elif mutation == 'model': after['model'] = 'another'
    elif mutation == 'schema': after['text']['format']['strict'] = False
    elif mutation == 'tools': after['tools'] = [{'type': 'function'}]
    elif mutation == 'instructions': after['instructions'] += ' Additional rule.'
    else: after['reasoning']['summary'] = 'auto'
    report = compare_reasoning_inputs(wire(before), wire(after), cohort='historical_input',
        provenance='observed_provider_body')
    assert not report['reasoning_only_pair_eligible']
    assert not report['only_effort_structurally_changed']


def test_no_change_or_different_serialization_is_not_a_pure_effort_wire_pair():
    before = body()
    assert not compare_reasoning_inputs(wire(before), wire(before), cohort='current_baseline',
        provenance='observed_provider_body')['reasoning_only_pair_eligible']
    after = copy.deepcopy(before)
    after['reasoning']['effort'] = 'medium'
    report = compare_reasoning_inputs(json.dumps(before).encode(), wire(after), cohort='historical_input',
        provenance='observed_provider_body')
    assert report['only_effort_structurally_changed']
    assert not report['reasoning_only_pair_eligible']


def test_duplicate_keys_or_undeclared_provenance_cannot_be_a_matched_pair():
    raw = b'{"reasoning":{"effort":"low","effort":"medium"}}'
    with pytest.raises(ValueError, match='Duplicate'):
        compare_reasoning_inputs(raw, wire(body()), cohort='historical_input', provenance='observed_provider_body')
    with pytest.raises(ValueError, match='Declare'):
        compare_reasoning_inputs(wire(body()), wire(body()), cohort='mixed', provenance='observed_provider_body')
