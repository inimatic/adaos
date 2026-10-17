import copy
import pytest
from adaos.sdk.builder.semantic_ui import validate_patch, validate_tool_arguments
from adaos.services.builder.traceability import digest


def document():
    return {'ui':{'application':{'desktop':{'pageSchema':{'id':'page','layout':{
        'version':2,'pattern':'document','density':'comfortable','contentWidth':'fluid','scroll':'page',
        'regions':[{'id':'main','role':'main','presentation':{'wide':'pane','compact':'stack'}}]},
        'widgets':[{'id':'controls','type':'ui.actions','area':'main',
                    'inputs':{'buttons':[{'id':'go','label':'Run'}]},'actions':[]}]}}}}}


def operation(value='Start'):
    return {'operation':'set','target_ref':'button:controls:go','property_path':['label'],'value':value}


def test_nested_button_patch_resolves_exactly_and_is_pure():
    source=document();before=copy.deepcopy(source)
    result=validate_patch(source,[operation()],expected_digest=digest(source),write_refs=['button:controls:go'])
    assert result['ok'],result['findings']
    button=result['document']['ui']['application']['desktop']['pageSchema']['widgets'][0]['inputs']['buttons'][0]
    assert button['label']=='Start' and source==before
    assert result['effects']=='none' and result['behavioral_acceptance']=='not_assessed'


@pytest.mark.parametrize('mutation', ['stale','scope','target','unsupported','id'])
def test_early_diagnostic_rejects_known_generation_mistakes(mutation):
    source=document();op=operation();expected=digest(source);refs=['button:controls:go']
    if mutation=='stale':expected='sha256:wrong'
    if mutation=='scope':refs=['widget:foreign']
    if mutation=='target':op['target_ref']='button:controls:absent';refs=[op['target_ref']]
    if mutation=='unsupported':op['operation']='replace_entire_application'
    if mutation=='id':op['property_path']=['id']
    result=validate_patch(source,[op],expected_digest=expected,write_refs=refs)
    assert not result['ok'] and result['document'] is None and result['findings']


def test_ternary_is_rejected_with_the_actual_expression_contract():
    source=document();op=operation({'kind':'expression','op':'ternary','args':[True,'yes','no']})
    op['property_path']=['enabledIf']
    result=validate_patch(source,[op],expected_digest=digest(source),write_refs=[op['target_ref']])
    assert not result['ok']
    finding=next(row for row in result['findings'] if row['code']=='webui.expression.contract_invalid')
    assert 'if' in finding['supported_ops'] and finding['contract_digest']


def test_unsupported_tool_keyword_is_reported_before_any_call():
    schema={'type':'object','properties':{'photo_id':{'type':'string'}},'required':['photo_id'],'additionalProperties':False}
    assert validate_tool_arguments({'photo_id':'one'},schema)['ok']
    result=validate_tool_arguments({'photo_id':'one','unsupported_argument':True},schema)
    assert not result['ok'] and not result['callable_binding_granted']
    assert 'unsupported_argument' in result['findings'][0]['message']
