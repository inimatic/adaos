"""An old source-wide instruction cannot acquire future requests' authority."""
import copy
import pytest

from adaos.services.builder.deterministic_task import admit_facts
from adaos.services.builder.task_acceptance import task_candidate_findings
from adaos.services.builder.task_interpretation import admit_interpretation, TaskInterpretationRejected
from adaos.services.builder.source_obligations import source_table_field_scope
from test_builder_deterministic_task import task_for


def evolved():
    task, intent = task_for('Item I1: name First, amount 3.\nShow all source fields in a table.')
    _, before, _ = admit_facts(task, intent)
    task, intent = task_for('Record T1: team_name Team, enabled true.')
    value = {'schema':'adaos.builder.task_interpretation.v1', 'source_digest':intent['digest'],
        'clause_roles':[{'clause_id':'clause:001','roles':['fact']}], 'requirements':[],
        'facts':[{'clause_ids':['clause:001'],'subject':'T1','field':field,'source_field':field,'value':scalar,
            'record_evidence':intent['statement'].rstrip('.'),'field_evidence':field+' '+literal}
            for field,scalar,literal in [('team_name','Team','Team'),('enabled',True,'true')]],
        'transitions':[],'constraints':[],'questions':[],'design_freedom':[]}
    _, brief = admit_interpretation(task, value, intent, previous_brief=before)
    document = {'resources':[
        {'id':'items','fields':[{'id':'name'},{'id':'amount'}],'records':[{'id':'I1','name':'First','amount':3}]},
        {'id':'teams','fields':[{'id':'team_name'},{'id':'enabled'}],
            'records':[{'id':'T1','team_name':'Team','enabled':True}]}],
        'views':[{'id':'items','resource_ref':'items','role':'collection','presentation':'table','field_refs':['name','amount']},
                 {'id':'teams','resource_ref':'teams','role':'collection','presentation':'list','field_refs':['team_name','enabled']}],
        'commands':[], 'capability_surfaces':[], 'requirement_bindings':[
            {'requirement_ref':row['assertion_ref'],'semantic_refs':['field:'+row['field']]}
            for row in brief['task_contract']['facts']]}
    return brief, document


def test_retained_source_table_instruction_does_not_force_a_future_directory_into_a_table():
    brief, document = evolved()
    assert task_candidate_findings(document, brief) == []
    table = next(c for c in brief['task_contract']['constraints'] if c['kind']=='source_record_table')
    assert source_table_field_scope(brief['task_contract'], table) == {('I1','name'),('I1','amount')}
    wrong = copy.deepcopy(document)
    wrong['views'][0]['field_refs'].remove('amount')
    assert any(f['code']=='task.source_table_field_not_visible' for f in task_candidate_findings(wrong,brief))
    wrong = copy.deepcopy(document)
    wrong['views'][0]['presentation'] = 'list'
    assert any(f['code']=='task.source_table_field_not_visible' for f in task_candidate_findings(wrong,brief))


def test_explicit_value_amendment_does_not_erase_the_original_projection_obligation():
    brief, document = evolved()
    contract = brief['task_contract']
    old = next(f for f in contract['facts'] if f['field']=='amount')
    prior = next(r for r in contract['requirements'] if r['id']==old['assertion_ref'])
    prior['status'] = 'superseded'
    old.update(value=5, source_digest=contract['source']['digest'], assertion_ref='assertion:amended-amount')
    contract['requirements'].append({**copy.deepcopy(prior), 'id':old['assertion_ref'],'status':'active',
        'source_digest':old['source_digest'],'assertion':{**prior['assertion'],'value':5}})
    document['resources'][0]['records'][0]['amount'] = 5
    for binding in document['requirement_bindings']:
        if binding['requirement_ref']==prior['id']: binding['requirement_ref']=old['assertion_ref']
    assert task_candidate_findings(document, brief) == []
    document['views'][0]['field_refs'].remove('amount')
    assert any(f['code']=='task.source_table_field_not_visible' for f in task_candidate_findings(document,brief))


def test_exact_source_table_constructor_without_source_fact_scope_requires_readmission():
    from test_builder_semantic_ui_constraints import proposal
    intent, task, value = proposal('Show all source fields in a table.')
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, value, intent)
    assert any(f['code']=='task.source_obligation_scope_unresolved' for f in caught.value.findings)
