import json

import pytest

from adaos.services.builder.source_operators import record_head_operator, GRAMMAR_REF
from adaos.services.builder.source_literals import literal_inventory, source_preflight_findings
from adaos.services.builder.task_interpretation import admit_interpretation, TaskInterpretationRejected
from adaos.services.builder.task_pipeline import prepare_generation_request
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief


@pytest.mark.parametrize('header,operator', [
    ('Do not create Loan L1','Do not'), ('  DO\tNOT create Sensor S1','DO\tNOT'),
    ("Don't create Task T1","Don't"), ('Don’t create Task T1','Don’t'),
    ('Never create Item I1','Never'), ('Не создавай запись R1','Не'),
    ('Нельзя добавлять объект X1','Нельзя'),
])
def test_declared_header_operators_retain_exact_unicode_evidence(header, operator):
    marker=record_head_operator(header,source_start=11)
    assert marker['text']==operator and marker['grammar_ref']==GRAMMAR_REF
    assert header[marker['start']-11:marker['end']-11]==operator
    assert marker['interpretation_status']=='requires_meaning_resolution_not_positive_fixture'


@pytest.mark.parametrize('header', [
    'Loan L1', 'Show Sensor S1', 'Покажи сервер RU',
    'Неизменяемая запись R1', 'Notebook N1',
    '"Do not create" button B1', 'Loan "Never"',
])
def test_record_names_and_quoted_labels_are_not_negative_header_operators(header):
    assert record_head_operator(header,source_start=0) is None


def negative_source():
    text='Do not create Loan L1: title Atlas, borrower Maya.'
    intent=capture_intent(text,project_ref='project:polarity-audit',locale='en',
                          source={'kind':'e2e','ref':'polarity-control-no-inference'})
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    proposal={'schema':'adaos.builder.task_interpretation.v1','source_digest':intent['digest'],
        'clause_roles':[{'clause_id':'clause:001','roles':['product','fact']}],
        'requirements':[{'id':'create-loan','clause_ids':['clause:001'],'statement':text,
            'kind':'behavior','operation':'create','verification_family':'runtime','supersedes':[]}],
        'facts':[{'clause_ids':['clause:001'],'subject':'L1','field':name,'source_field':name,'value':value,
            'record_evidence':text,'field_evidence':name+' '+value}
            for name,value in [('title','Atlas'),('borrower','Maya')]],
        'transitions':[],'constraints':[],'questions':[],'design_freedom':[]}
    return intent,task,proposal


@pytest.mark.parametrize('roles', [['product','fact'], ['exclusion'], ['product']])
def test_model_roles_cannot_clear_independently_derived_negative_record_header(roles):
    intent,task,proposal=negative_source()
    proposal['clause_roles'][0]['roles']=roles
    if 'fact' not in roles:
        proposal['facts']=[]
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task,proposal,intent)
    findings=[row for row in caught.value.findings if row['code']=='task.source_record_polarity_unresolved']
    assert len(findings)==2
    assert all(row['gate']=='G3' and row['source_grammar_ref']==GRAMMAR_REF for row in findings)


def test_known_source_grammar_blocker_is_durable_before_any_paid_call(tmp_path):
    intent,task,_=negative_source()

    class NoInference:
        def submit(self,*args,**kwargs):
            pytest.fail('Known source grammar blocker reached paid interpretation')

    with pytest.raises(TaskInterpretationRejected):
        prepare_generation_request({'output_mode':'semantic_v2','base_request':{},
            'dynamic_request':{'instruction':intent['statement']}},
            metadata={'builder_task_pipeline':'1','prototype_intent':intent},model='selected',reasoning=None,
            provider=NoInference(),journal_dir=tmp_path)
    receipt=json.loads(next(tmp_path.glob('source-preflight-*.json')).read_text())
    assert receipt['task_spec']==task
    assert receipt['interpretation_submitted'] is receipt['candidate_generation_submitted'] is False
    assert not list(tmp_path.glob('builder-interpretation-*.json'))


@pytest.mark.parametrize('locale',['ru','en'])
def test_source_grammar_limit_has_a_separate_safe_action_not_missing_user_values(locale):
    from adaos.sdk.builder.prototype import task_admission_failure
    from adaos.services.builder.task_diagnostics import interpretation_refusal_text

    _,task,_=negative_source()
    failure=task_admission_failure(TaskInterpretationRejected(source_preflight_findings(task)),locale=locale)
    assert failure['next_action']=='resolve_source_semantics'
    assert failure['paid_candidate_generation'] is False
    assert interpretation_refusal_text(failure,locale=locale)==failure['detail']
    assert ('не отсутствие' if locale=='ru' else 'not missing supplied values') in failure['detail']


def test_stale_negative_fact_contract_cannot_buy_reinterpretation_or_candidate_repair():
    from adaos.services.builder.source_literals import contract_literal_coverage
    from adaos.services.builder.task_interpretation import _attribute_literal
    from adaos.services.builder.repair_diagnosis import compile_repair_diagnosis

    intent,_,proposal=negative_source()
    findings,coverage=contract_literal_coverage({'source':intent,'clause_roles':proposal['clause_roles'],
        'facts':proposal['facts']},literal_matches=_attribute_literal)
    assert len(findings)==2 and not coverage['recognized_atoms_accounted_for']
    diagnosis=compile_repair_diagnosis(candidate_digest='candidate',findings=findings,
        parent={'generation_plan_id':'plan','task_spec_ref':'task'},compiler_digest='compiler')
    assert diagnosis['failure_classes']==['source_semantics']
    assert diagnosis['next_actions']==['resolve_source_semantics']
    assert diagnosis['paid_candidate_repair_allowed'] is False


def test_operator_offsets_follow_source_sentences_not_model_clause_roles():
    text='📚 Loan L1: title "Never". Do not create Loan L2: title Atlas.'
    intent=capture_intent(text,project_ref='project:operators')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    inventory=literal_inventory(task)
    positive,negative=inventory['atoms']
    assert positive['subject']=='L1' and 'source_head_operator' not in positive
    marker=negative['source_head_operator']
    assert text[marker['start']:marker['end']]=='Do not'
    findings=source_preflight_findings(task)
    assert len(findings)==1 and findings[0]['source_atom_ref']==negative['id']


def negative_action():
    text='Do not delete loan records.'
    intent=capture_intent(text,locale='en',project_ref='project:negative-operation')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    proposal={'schema':'adaos.builder.task_interpretation.v1','source_digest':intent['digest'],
        'clause_roles':[{'clause_id':'clause:001','roles':['product']}],
        'requirements':[{'id':'remove','clause_ids':['clause:001'],'statement':text,
            'kind':'behavior','operation':'delete','verification_family':'runtime','supersedes':[]}],
        'facts':[],'transitions':[],'constraints':[],'questions':[],'design_freedom':[]}
    return intent,task,proposal


def test_negative_action_without_tuple_cannot_become_positive_delete():
    from adaos.services.builder.source_operators import CLAUSE_GRAMMAR_REF

    intent,task,proposal=negative_action()
    assert not source_preflight_findings(task)  # A non-executable interpretation may still be proposed.
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task,proposal,intent)
    finding=next(row for row in caught.value.findings if row['code']=='task.source_operation_polarity_unresolved')
    assert finding['source_grammar_ref']==CLAUSE_GRAMMAR_REF


def test_nonexecuting_negative_policy_is_not_rejected_by_the_positive_operation_guard():
    intent,task,proposal=negative_action()
    proposal['clause_roles'][0]['roles']=['exclusion']
    proposal['requirements'][0].update(kind='exclusion',operation=None,verification_family='policy')
    _,brief=admit_interpretation(task,proposal,intent)
    assert brief['operations']==[]
    # This is admission of retained source policy, not proof a compiled candidate enforces it.


def test_operator_ir_is_user_evidence_and_old_clause_ids_are_not_reinterpreted():
    from adaos.services.builder.source_operators import positive_operation_findings
    from adaos.services.builder.task_interpretation import interpretation_messages

    intent,task,proposal=negative_action()
    messages=interpretation_messages(task)
    evidence=json.loads(messages[-1]['content'])['interpretation_request']
    assert messages[-1]['role']=='user'
    assert evidence['source_clause_operators'][0]['operator']['text']=='Do not'
    old={**proposal['requirements'][0],'status':'active','source_digest':'older-source'}
    assert not positive_operation_findings(intent['statement'],[old],source_digest=intent['digest'])
    current={**old,'source_digest':intent['digest']}
    assert len(positive_operation_findings(intent['statement'],[current],source_digest=intent['digest']))==1
