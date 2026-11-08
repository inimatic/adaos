import copy
import pytest
from adaos.sdk.builder.semantic_ui import describe_patch_target, validate_patch, validate_tool_arguments
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


def list_document():
    source=document()
    source['ui']['application']['desktop']['pageSchema']['widgets']=[
        {'id':'records','type':'ui.list','area':'main','inputs':{'meta':[{'key':'status'}]}}]
    return source


def test_target_contract_supplies_the_actual_metadata_shape_without_a_write():
    source=list_document(); before=copy.deepcopy(source)
    result=describe_patch_target(source,'widget:records',['inputs','meta'],expected_digest=digest(source))
    assert result['ok'] and result['effects']=='none' and result['abi_digest']
    metadata=result['property_schema']['items']
    assert metadata['required']==['key'] and metadata['additionalProperties'] is False
    assert 'path' not in metadata['properties'] and 'date' in metadata['properties']['kind']['enum']
    assert source==before


@pytest.mark.parametrize('metadata', [[{'path':'status'}],[{'key':'status','typo':'ignored?'}]])
def test_metadata_mistakes_fail_early_with_the_exact_contract(metadata):
    source=list_document()
    op={'operation':'set','target_ref':'widget:records','property_path':['inputs','meta'],'value':metadata}
    result=validate_patch(source,[op],expected_digest=digest(source),write_refs=['widget:records'])
    assert not result['ok'] and result['document'] is None
    assert result['operation_contracts'][0]['property_schema']['items']['required']==['key']
    assert source['ui']['application']['desktop']['pageSchema']['widgets'][0]['inputs']['meta']==[{'key':'status'}]


def test_target_inspection_rejects_stale_source_and_unknown_properties():
    source=list_document()
    stale=describe_patch_target(source,'widget:records',['inputs','meta'],expected_digest='sha256:old')
    assert not stale['ok'] and 'property_schema' not in stale
    unknown=describe_patch_target(source,'widget:records',['inputs','metaa'],expected_digest=digest(source))
    assert not unknown['ok'] and 'meta' in unknown['available_properties']
