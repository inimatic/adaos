import copy

import pytest

from adaos.services.builder.semantic_ui_constraints import source_targets, interpretation_findings
from adaos.services.builder.task_acceptance import task_candidate_findings
from adaos.services.builder.task_interpretation import admit_interpretation, TaskInterpretationRejected
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_acceptance import fixture

TEXT='Set the English title of the existing table to "Repair queue" and the English caption of its title field to "Request".'


def proposal(text=TEXT):
    intent=capture_intent(text,project_ref='project:display-targets',locale='en')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    value={'schema':'adaos.builder.task_interpretation.v1','source_digest':intent['digest'],
        'clause_roles':[{'clause_id':'clause:001','roles':['product']}],
        'requirements':[{'id':'labels','clause_ids':['clause:001'],'statement':text,
            'kind':'ui','operation':None,'verification_family':'layout','supersedes':[]}],
        'facts':[],'transitions':[],'constraints':[],'questions':[],'design_freedom':[]}
    return intent,task,value


def test_explicit_field_and_collection_labels_have_distinct_typed_source_targets():
    rows=source_targets(TEXT)
    assert [(row['kind'],row['subject'],row['values']) for row in rows]==[
        ('view_title','',['Repair queue']),('field_label','title',['Request'])]
    intent,task,value=proposal()
    _,brief=admit_interpretation(task,value,intent)
    assert {row['kind'] for row in brief['task_contract']['constraints']}=={'view_title','field_label'}


@pytest.mark.parametrize('kind,value', [('section_title','Repair queue'),('visible_text','Request')])
def test_old_misclassifications_refuse_before_buying_a_candidate(kind,value):
    intent,task,interpretation=proposal()
    interpretation['constraints']=[{'clause_ids':['clause:001'],'kind':kind,'subject':'','values':[value]}]
    with pytest.raises(TaskInterpretationRejected) as error:admit_interpretation(task,interpretation,intent)
    assert any(row['code']=='task.ui_target_kind_conflict' for row in error.value.findings)


@pytest.mark.parametrize('prefix',['Do not set ','Never set ','If approved, set ','For example, set '])
def test_negative_conditional_or_example_text_does_not_become_an_unconditional_label(prefix):
    assert source_targets(prefix+'the title of the existing table to "Forbidden".')==[]


def test_quoted_fixture_instructions_are_not_display_authority():
    assert source_targets('Record R1: note \'Set title of the existing table to "Wrong"\'.')==[]


def test_retained_clause_ids_do_not_reinterpret_an_older_source():
    value={'source':{'digest':'new'},'constraints':[{'kind':'section_title','subject':'',
        'values':['Repair queue'],'clause_ids':['clause:001'],'source_digest':'old'}]}
    assert interpretation_findings(TEXT,value)==[]
    value['constraints'][0]['source_digest']='new'
    assert interpretation_findings(TEXT,value)[0]['code']=='task.ui_target_kind_conflict'


def display_fixture():
    brief,document=fixture()
    document['views'][0].update(title={'ru':'Очередь'},field_refs=['name','cpu','disk'])
    document['resources'][0]['fields'][0]['label']={'ru':'Заявка'}
    contract=brief['task_contract']
    contract['constraints'] += [
        {'kind':'view_title','subject':'servers','values':['Очередь'],'clause_ids':['clause:004']},
        {'kind':'field_label','subject':'name','values':['Заявка'],'clause_ids':['clause:004']},
        {'kind':'field_order','subject':'servers','values':['name','CPU','disk'],'clause_ids':['clause:004']}]
    return brief,document


def test_labels_and_order_bind_actual_owners_without_creating_tabs_or_disclosures():
    brief,document=display_fixture()
    assert task_candidate_findings(document,brief)==[]


@pytest.mark.parametrize('fault,code', [
    ('view','task.view_title_mismatch'),('field','task.field_label_mismatch'),
    ('punctuation','task.field_label_mismatch'),('order','task.field_order_mismatch'),
    ('not_visible','task.field_label_not_visible'),('duplicate','task.ui_target_unresolved')])
def test_a_string_elsewhere_cannot_satisfy_the_wrong_owner(fault,code):
    brief,document=display_fixture()
    if fault=='view':document['views'][0]['title']={'ru':'Не та таблица'}
    if fault=='field':document['resources'][0]['fields'][0]['label']={'ru':'Иное'}
    if fault=='punctuation':document['resources'][0]['fields'][0]['label']={'ru':'Заявка!'}
    if fault=='order':document['views'][0]['field_refs']=['cpu','name','disk']
    if fault=='not_visible':document['views'][0]['field_refs']=['cpu','disk']
    if fault=='duplicate':
        document['resources'].append({'id':'other','fields':[copy.deepcopy(document['resources'][0]['fields'][0])],'records':[]})
    findings=task_candidate_findings(document,brief)
    assert any(row['code']==code for row in findings)


def test_checked_display_locators_survive_multiple_collections_and_graph_revalidation():
    from adaos.services.builder.semantic_ui_constraints import normalize_display_bindings
    brief,previous=display_fixture()
    display=[row for row in brief['task_contract']['constraints'] if row['kind'] in {'view_title','field_order','field_label'}]
    for index,row in enumerate(display):row.update(subject='' if row['kind']!='field_label' else 'name',assertion_ref=f'assertion:display-{index}')
    document=copy.deepcopy(previous)
    document['resources'].append({'id':'teams','fields':[{'id':'team_name','label':{'ru':'Команда'}}],'records':[]})
    document['views'].append({'id':'teams','role':'collection','resource_ref':'teams','title':{'ru':'Команды'},'field_refs':['team_name']})
    assert task_candidate_findings(document,brief,previous=previous)==[]
    changes=normalize_display_bindings(document,brief,previous=previous)
    assert len(changes)==3
    assert task_candidate_findings(document,brief)==[]
    # Replay over an already accepted multi-table baseline uses its exact IDs.
    assert task_candidate_findings(document,brief,previous=document)==[]
    bad=copy.deepcopy(document)
    bad['views'][0]['title']={'ru':'Другой заголовок'}
    bad['views'][-1]['title']=previous['views'][0]['title']
    binding=next(row for row in bad['requirement_bindings'] if row['requirement_ref']=='assertion:display-0')
    binding['semantic_refs']=['view:teams']
    assert any(row['code']=='task.ui_target_unresolved' for row in task_candidate_findings(bad,brief,previous=document))


def test_an_ambiguous_initial_display_owner_cannot_be_invented_by_normalization():
    brief,document=display_fixture()
    for row in brief['task_contract']['constraints']:
        if row['kind']=='view_title':row['subject']=''
    document['views'].append({'id':'other','role':'collection','resource_ref':'servers','title':{'ru':'Очередь'}})
    assert any(row['code']=='task.ui_target_unresolved' for row in task_candidate_findings(document,brief))
