import json

import pytest

from adaos.e2e.prototype_evolution_analysis import summarize


def make_turn(tmp_path,name,effort='low'):
    path=tmp_path/'turns'/name;path.mkdir(parents=True)
    turn={'effort':effort,'phase':'exploration','level':0,'functional_success':True,
        'provider_calls':1,'base_revision':None,'actual_revision':'001','generation_ms':50,'whole_outcome_ms':90}
    attempt={'id':'ai-test','request_id':'builder-ui-test','usage':{'input_tokens':100,'output_tokens':20,
        'input_tokens_details':{'cached_tokens':40},'output_tokens_details':{'reasoning_tokens':7}},
        'telemetry':{'provider_calls':1,'wire':{'reasoning_effort':effort}}}
    (path/'result.json').write_text(json.dumps(turn),encoding='utf-8')
    (path/'provider-attempts.json').write_text(json.dumps({'attempts':[attempt]}),encoding='utf-8')


def test_token_subsets_are_not_double_counted_and_effort_is_observed(tmp_path):
    make_turn(tmp_path,'one')
    result=summarize(tmp_path)
    assert result['totals']=={'input':100,'output':20,'cached_input':40,'reasoning_output_subset':7,
        'uncached_input':60,'total':120,'calls':1}
    assert result['by_effort']['low']['qualified']==1
    assert result['by_effort']['low']['qualified_latency_median_ms']==50


def test_old_journals_cannot_be_counted_again_as_new_calls(tmp_path):
    make_turn(tmp_path,'one');make_turn(tmp_path,'two')
    with pytest.raises(ValueError,match='Duplicate provider attempt'):summarize(tmp_path)
