import copy

import pytest

from adaos.services.builder.source_obligations import inventory, interpretation_findings, derived_constraints
from adaos.services.builder.source_literals import literal_inventory, literal_coverage
from adaos.services.builder.task_interpretation import _attribute_literal, admit_interpretation, TaskInterpretationRejected
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief


def prepared():
    lines=['Account A1: name Alice. Account B2: name Bob.',
        'Ticket T1 belongs to account A1; status open, note "Needs review".',
        'Show a clear warning "Sample data".',
        'Copy C1 for account A1: created_at 2026-10-29, version v1. This is creation date, not verification date.',
        'Provide Resolve command changing status from open to accepted.']
    intent=capture_intent('\n'.join(lines),project_ref='project:obligations',locale='en')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    facts=[]
    for clause,subject,field,value,record,phrase in [
        (1,'A1','name','Alice','Account A1: name Alice','name Alice'),
        (1,'B2','name','Bob','Account B2: name Bob','name Bob'),
        (2,'T1','status','open',lines[1].rstrip('.'),'status open'),
        (2,'T1','note','Needs review',lines[1].rstrip('.'),'note "Needs review"'),
        (4,'C1','created_at','2026-10-29',lines[3].split('. ')[0],'created_at 2026-10-29'),
        (4,'C1','version','v1',lines[3].split('. ')[0],'version v1')]:
        facts.append({'clause_ids':[f'clause:{clause:03d}'],'subject':subject,'field':field,
            'source_field':field,'value':value,'record_evidence':record,'field_evidence':phrase})
    proposal={'schema':'adaos.builder.task_interpretation.v1','source_digest':intent['digest'],
        'clause_roles':[{'clause_id':f'clause:{i:03d}','roles':roles} for i,roles in enumerate(
            [['fact'],['fact'],['product'],['fact'],['product']],1)],
        'requirements':[
            {'id':'warning','clause_ids':['clause:003'],'statement':lines[2],'kind':'ui','operation':None,
                'verification_family':'layout','supersedes':[]},
            {'id':'resolve','clause_ids':['clause:005'],'statement':lines[4],'kind':'behavior','operation':'transition',
                'verification_family':'transition','supersedes':[]}],
        'facts':facts,'transitions':[{'clause_ids':['clause:005'],'requirement_id':'resolve','command_label':'Resolve',
            'field':'status','from_value':'open','to_value':'accepted','preserve_other_fields':False}],
        'constraints':[{'clause_ids':['clause:003'],'kind':'visible_text','subject':'','values':['Sample data']}],
        'questions':[],'design_freedom':[]}
    return intent,task,proposal


def document(brief):
    def field(identity,label):return {'id':identity,'label':{'en':label}}
    transition=brief['task_contract']['transitions'][0]
    return {'title':{'en':'Test'},'resources':[
        {'id':'accounts','fields':[field('name','Name')],'records':[{'id':'A1','name':'Alice'},{'id':'B2','name':'Bob'}]},
        {'id':'tickets','fields':[field('status','Status'),field('note','Note'),field('account_ref','Account')],
            'records':[{'id':'T1','status':'open','note':'Needs review','account_ref':'A1'}],
            'read_only_when':{'field_ref':'status','operator':'not_equals','value':'open'}},
        {'id':'copies','fields':[field('created_at','Creation date'),field('version','Version'),field('account_ref','Account')],
            'records':[{'id':'C1','created_at':'2026-10-29','version':'v1','account_ref':'A1'}]}],
        'views':[{'id':'copies','resource_ref':'copies','role':'collection','title':{'en':'Sample data'},
                  'field_refs':['created_at','version','account_ref']},
                 {'id':'edit','resource_ref':'tickets','role':'editor','field_refs':['status']}],
        'commands':[{'id':'resolve','view_ref':'edit','kind':'transition','label':{'en':'Resolve'},
                     'fixed_values':{'status':'accepted'},'input_field_refs':[]}],
        'relationships':[{'id':identity,'from_resource_ref':identity,'from_field_ref':'account_ref',
            'to_resource_ref':'accounts','to_field_ref':'id','cardinality':'many_to_one'} for identity in ['tickets','copies']],
        'requirement_bindings':[{'requirement_ref':transition['requirement_id'],'semantic_refs':['command:resolve']}]}


@pytest.mark.parametrize('mutation,code',[('reverse','direction_mismatch'),('note','source_record_attribute_unextracted'),
                                       ('disclosure','source_disclosure_unextracted')])
def test_independent_obligations_inside_one_clause_cannot_be_dropped_or_reversed(mutation,code):
    intent,task,proposal=prepared()
    admit_interpretation(task,proposal,intent)
    if mutation=='reverse':
        row=proposal['transitions'][0];row['from_value'],row['to_value']=row['to_value'],row['from_value']
    elif mutation=='note':proposal['facts']=[row for row in proposal['facts'] if row['source_field']!='note']
    else:proposal['constraints']=[]
    with pytest.raises(TaskInterpretationRejected) as caught:admit_interpretation(task,proposal,intent)
    assert any(code in row['code'] for row in caught.value.findings)


def test_core_derives_associations_and_meanings_from_source_without_new_proposal_fields():
    intent,task,proposal=prepared()
    _,brief=admit_interpretation(task,proposal,intent)
    rows=brief['task_contract']['constraints']
    assert {(row['subject'],row['values'][0]) for row in rows if row['kind']=='record_association'}=={('T1','A1'),('C1','A1')}
    assert next(row for row in rows if row['kind']=='field_display_meaning')['source_field']=='created_at'
    data=inventory(intent['statement'],intent['digest'])
    assert not data['unresolved'] and not data['general_source_completeness_proven']
    assert all(intent['statement'][row['start']:row['end']] for row in data['atoms'])


@pytest.mark.parametrize('mutation,code',[('ticket','record_association_mismatch'),('copy','record_association_mismatch'),
                                       ('label','field_display_meaning_mismatch'),('hidden','field_display_meaning_not_visible')])
def test_structurally_valid_wrong_associations_and_date_meaning_fail_independent_source_acceptance(mutation,code):
    from adaos.services.builder.task_acceptance import task_candidate_findings
    intent,task,proposal=prepared();_,brief=admit_interpretation(task,proposal,intent)
    source=document(brief)
    assert not task_candidate_findings(source,brief)
    if mutation in {'ticket','copy'}:source['resources'][1 if mutation=='ticket' else 2]['records'][0]['account_ref']='B2'
    elif mutation=='label':source['resources'][2]['fields'][0]['label']={'en':'Verification date'}
    else:source['views'][0]['field_refs'].remove('created_at')
    assert any(code in row['code'] for row in task_candidate_findings(source,brief))


@pytest.mark.parametrize('label',['Creation date','Created at','Created'])
def test_display_meaning_is_not_one_hardcoded_caption(label):
    from adaos.services.builder.task_acceptance import task_candidate_findings
    intent,task,proposal=prepared();_,brief=admit_interpretation(task,proposal,intent)
    source=document(brief);source['resources'][2]['fields'][0]['label']={'en':label}
    assert not task_candidate_findings(source,brief)


def test_reverse_relationship_declaration_preserves_the_same_source_association():
    from adaos.services.builder.task_acceptance import task_candidate_findings
    intent,task,proposal=prepared();_,brief=admit_interpretation(task,proposal,intent)
    source=document(brief)
    for row in source['relationships']:
        row['from_resource_ref'],row['to_resource_ref']=row['to_resource_ref'],row['from_resource_ref']
        row['from_field_ref'],row['to_field_ref']=row['to_field_ref'],row['from_field_ref']
        row['cardinality']='one_to_many'
    assert not task_candidate_findings(source,brief)


def test_quoted_associations_and_search_instructions_are_not_initial_record_links():
    statement='Search for account A1. Show a warning "Ticket T1 belongs to account B2".'
    rows=inventory(statement,'source:test')
    assert [row['kind'] for row in rows['atoms']]==['visible_text']


def test_ambiguous_timestamp_target_is_explicit_not_a_guessed_field():
    statement='Copy C1: created_at 2026-10-29, checked_at 2026-10-30. This is creation date, not verification date.'
    result=inventory(statement,'source:test')
    assert result['unresolved'][0]['reason']=='timestamp_field_target_unresolved'
    assert not any(row['kind']=='field_display_meaning' for row in result['atoms'])


def test_participial_russian_transition_cannot_be_dropped_by_retagging_its_clause():
    intent,task,proposal=prepared()
    lines=intent['statement'].splitlines()
    lines[-1]='Для заявки нужна локальная команда «Resolve», меняющая status open на accepted.'
    intent=capture_intent('\n'.join(lines),project_ref='project:obligations',locale='en')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    proposal['source_digest']=intent['digest'];proposal['requirements'][-1]['statement']=lines[-1]
    admit_interpretation(task,proposal,intent)
    proposal['transitions']=[];proposal['requirements'][-1]['operation']='inspect'
    with pytest.raises(TaskInterpretationRejected) as caught:admit_interpretation(task,proposal,intent)
    assert any(row['code']=='task.source_transition_relation_unextracted' for row in caught.value.findings)


def test_unproved_transition_order_is_explicitly_unresolved_not_certified_by_literal_presence():
    from adaos.services.builder.source_relations import transition_relation_findings
    row={'requirement_id':'r1','clause_ids':['clause:001'],'command_label':'Resolve','field':'status',
         'from_value':'open','to_value':'accepted','preserve_other_fields':False}
    findings,receipt=transition_relation_findings('Resolve mentions status, accepted and open.','source:scope',[row])
    assert findings[0]['code']=='task.source_transition_scope_unresolved'
    assert not receipt['recognized_slots_consistent']


def test_explicit_link_resource_can_represent_the_same_record_association():
    from adaos.services.builder.task_acceptance import task_candidate_findings
    intent,task,proposal=prepared();_,brief=admit_interpretation(task,proposal,intent)
    source=document(brief)
    source['relationships']=[row for row in source['relationships'] if row['from_resource_ref']!='tickets']
    source['resources'].append({'id':'assignments','fields':[],'records':[{'id':'L1','ticket_ref':'T1','account_ref':'A1'}]})
    source['relationships'] += [{'id':name,'from_resource_ref':'assignments','from_field_ref':field,
        'to_resource_ref':target,'to_field_ref':'id','cardinality':'many_to_one'}
        for name,field,target in [('link-ticket','ticket_ref','tickets'),('link-account','account_ref','accounts')]]
    assert not task_candidate_findings(source,brief)
    source['resources'][-1]['records'][0]['account_ref']='B2'
    assert any(row['code']=='task.record_association_mismatch' for row in task_candidate_findings(source,brief))


def test_new_source_expectations_are_not_hidden_when_reusing_an_old_admitted_brief():
    from adaos.services.builder.source_obligations import contract_obligation_findings
    intent,task,proposal=prepared();_,brief=admit_interpretation(task,proposal,intent)
    assert not contract_obligation_findings(brief['task_contract'],require_derived=True)
    contract=copy.deepcopy(brief['task_contract'])
    contract['constraints']=[row for row in contract['constraints'] if row['kind'] not in {'record_association','field_display_meaning'}]
    assert len(contract_obligation_findings(contract,require_derived=True))==3
