"""Lossless strict-output clause references, not relaxed semantic admission."""
import copy
import json

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.interpretation_delivery import (
    request_packet, decode_proposal, CLAUSE_TYPED_NAME, BOUND_CLAUSE_TYPED_NAME,
    TYPED_NAME, QUOTE_TYPED_NAME, BOUND_QUOTE_TYPED_NAME,
)
from adaos.services.builder.task_interpretation import (
    TaskInterpretationRejected, admit_interpretation, interpretation_messages, interpretation_contract,
)
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_interpretation import prepared


def test_typed_locale_selects_literal_source_inflection_not_a_regenerated_language_name():
    from jsonschema import ValidationError
    from adaos.services.builder.task_interpretation import _constraint_literal
    intent = capture_intent('Создай прототип на русском языке.', project_ref='project:locale-source')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    packet = request_packet(task, output_mode='typed')
    validator = Draft202012Validator({'$defs': packet['wire_schema']['$defs'], '$ref': '#/$defs/constraint'})
    row = {'kind': 'locale', 'clause_ids': ['clause:001'], 'subject': '', 'values': ['русском']}
    validator.validate(row)
    for invented in ('русский язык', 'ru', 'English'):
        with pytest.raises(ValidationError):validator.validate({**row, 'values': [invented]})
        assert not _constraint_literal(task['clauses'][0]['text'], 'locale', invented)
    assert _constraint_literal(task['clauses'][0]['text'], 'locale', 'русском')
    # Legacy canonical/source admission is not relaxed to accept a paraphrase.
    assert 'properties' in interpretation_contract()['$defs']['constraint']


def test_locale_specialization_does_not_silently_translate_unsupported_source_languages():
    intent = capture_intent('Build a Prototype in Spanish.', project_ref='project:unsupported-locale')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    schema = interpretation_contract(provider=True, task=task, source_references=True)
    assert schema['$defs']['constraint']['properties']['values']['items'] == {'type': 'string'}


def wire_record(task, proposal, mode='typed'):
    packet = request_packet(task, output_mode=mode)
    value = copy.deepcopy(proposal)
    for row in value['requirements']:
        row.setdefault('reaffirms', [])
        row['source_clause'] = row['clause_ids'][0]
        row['statement_text'] = row.pop('statement')
    if mode=='bound_typed':
        value.pop('schema')
        value.pop('source_digest')
    return packet, {'task_spec':task, 'options':{'text':{'format':{'name':packet['wire_name']}}},
                    'result':{'output_text':json.dumps(value, ensure_ascii=False)}}


@pytest.mark.parametrize('mode,name', [('typed',QUOTE_TYPED_NAME),('bound_typed',BOUND_QUOTE_TYPED_NAME)])
def test_strict_reference_roundtrip_preserves_exact_quotes_newlines_and_original_bytes(mode,name):
    intent,task,proposal=prepared()
    packet,record=wire_record(task,proposal,mode)
    original=copy.deepcopy(record)
    Draft202012Validator(packet['wire_schema']).validate(json.loads(record['result']['output_text']))
    assert packet['wire_name']==name
    assert packet['wire_schema']['$defs']['sourceStatement']['enum']==[r['id'] for r in task['clauses']]
    assert 'source_clause' in packet['wire_schema']['$defs']['requirement']['anyOf'][0]['required']
    decoded=decode_proposal(record)
    assert decoded['facts']==proposal['facts']
    expected = copy.deepcopy(proposal)
    for row in expected['requirements']: row.setdefault('reaffirms', [])
    assert decoded == expected
    admitted,_=admit_interpretation(task,decoded,intent)
    assert admitted['interpretation_status']=='admitted' and record==original


def test_source_text_never_becomes_a_strict_enum_but_remains_unchanged_user_evidence():
    source='Show label "Quoted \\ path", then show line\nwith emoji 🧭 and «русский текст».'
    intent=capture_intent(source,project_ref='project:strict-transport')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    packet=request_packet(task,output_mode='typed')
    evidence=json.loads(packet['messages'][-1]['content'])['interpretation_request']
    assert evidence['source_clauses']==task['clauses']
    for row in task['clauses']:
        assert row['text'].strip() not in packet['wire_schema']['$defs']['sourceStatement']['enum']
    assert 'source_clause' in packet['messages'][0]['content']
    assert 'select one COMPLETE source clause from the schema enum' not in packet['messages'][0]['content']


@pytest.mark.parametrize('fault', ['missing','foreign','uncited','statement','both','wrong_type','duplicate'])
def test_reference_transport_cannot_guess_repair_or_accept_ambiguous_statement(fault):
    _,task,proposal=prepared()
    _,record=wire_record(task,proposal)
    value=json.loads(record['result']['output_text'])
    row=value['requirements'][0]
    if fault=='missing':row.pop('source_clause')
    elif fault=='foreign':row['source_clause']='clause:999'
    elif fault=='uncited':row['source_clause']='clause:002'
    elif fault=='statement':row['statement']=row.pop('source_clause')
    elif fault=='both':row['statement']='invented'
    elif fault=='wrong_type':row['source_clause']=[]
    record['result']['output_text']=json.dumps(value)
    if fault=='duplicate':
        record['result']['output_text']=record['result']['output_text'].replace(
            '"source_clause": "clause:001"', '"source_clause": "clause:001", "source_clause": "clause:001"',1)
    with pytest.raises(TaskInterpretationRejected) as caught:decode_proposal(record)
    assert caught.value.findings[0]['code']=='task.interpretation_response_invalid'


def test_old_typed_journal_keeps_its_old_decoder_without_rewriting_the_retained_result():
    _,_,proposal=prepared()
    record={'options':{'text':{'format':{'name':TYPED_NAME}}},
            'result':{'output_text':json.dumps(proposal)}}
    before=copy.deepcopy(record)
    assert decode_proposal(record)==proposal and record==before


@pytest.mark.parametrize('name', [CLAUSE_TYPED_NAME, BOUND_CLAUSE_TYPED_NAME])
def test_v2_retained_clause_ids_still_restore_complete_clause_not_new_atomic_text(name):
    _, task, proposal = prepared()
    value = copy.deepcopy(proposal)
    for row in value['requirements']:
        row['source_clause'] = row['clause_ids'][0]
        row.pop('statement')
    if name == BOUND_CLAUSE_TYPED_NAME:
        value.pop('schema')
        value.pop('source_digest')
    record = {'task_spec': task, 'options': {'text': {'format': {'name': name}}},
              'result': {'output_text': json.dumps(value)}}
    before = copy.deepcopy(record)
    decoded = decode_proposal(record)
    assert record == before
    for row in decoded['requirements']:
        assert row['statement'] == next(c['text'].strip() for c in task['clauses'] if c['id'] == row['clause_ids'][0])


@pytest.mark.parametrize('fault', ['missing', 'null', 'empty', 'space', 'paraphrase', 'other_clause', 'repeated'])
def test_v3_quotes_must_be_exact_nonempty_and_unique_in_their_cited_clause(fault):
    _, task, proposal = prepared()
    _, record = wire_record(task, proposal)
    value = json.loads(record['result']['output_text'])
    row = value['requirements'][0]
    if fault == 'missing': row.pop('statement_text')
    elif fault == 'null': row['statement_text'] = None
    elif fault == 'empty': row['statement_text'] = ''
    elif fault == 'space': row['statement_text'] += ' '
    elif fault == 'paraphrase': row['statement_text'] = 'Invented requirement'
    elif fault == 'other_clause': row['statement_text'] = proposal['requirements'][1]['statement']
    else: row['statement_text'] = ' '  # repeated whitespace never certifies an obligation
    record['result']['output_text'] = json.dumps(value)
    with pytest.raises(TaskInterpretationRejected) as caught: decode_proposal(record)
    assert caught.value.findings[0]['gate'] == 'G3'


def test_v3_can_express_independent_atomic_obligations_from_one_compound_clause():
    intent, task, proposal = prepared()
    # Clause004 contains BOTH the section request and a separate viewport check.
    clause = task['clauses'][3]['text'].strip()
    quote = clause.split('. ', 1)[1]
    proposal['requirements'].append({'id': 'viewport-check', 'clause_ids': ['clause:004'],
        'statement': quote, 'kind': 'verification', 'operation': None,
        'verification_family': 'layout', 'supersedes': []})
    packet, record = wire_record(task, proposal)
    Draft202012Validator(packet['wire_schema']).validate(json.loads(record['result']['output_text']))
    decoded = decode_proposal(record)
    admitted, _ = admit_interpretation(task, decoded, intent)
    assert admitted['interpretation_status'] == 'admitted'
    rows = [r for r in decoded['requirements'] if r['clause_ids'] == ['clause:004']]
    assert len(rows) == 2 and rows[0]['statement'] != rows[1]['statement']
    assert all(r['statement'] != clause for r in rows)
    assert 'COMPLETE unchanged text' not in packet['messages'][0]['content']
    assert 'statement_text' in packet['messages'][0]['content']


def test_v3_rejects_a_repeated_nonempty_quote_without_guessing_its_occurrence():
    intent = capture_intent('Show a table; show a table.', project_ref='project:ambiguous-quote')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    _, _, proposal = prepared()
    proposal['source_digest'] = intent['digest']
    proposal['requirements'] = [{**proposal['requirements'][0], 'clause_ids': ['clause:001'], 'statement': 'a table'}]
    _, record = wire_record(task, proposal)
    with pytest.raises(TaskInterpretationRejected): decode_proposal(record)


def test_reference_binding_requires_original_frozen_task_and_does_not_mask_invalid_facts():
    intent,task,proposal=prepared()
    _,record=wire_record(task,proposal)
    broken=copy.deepcopy(record)
    broken['task_spec']['source']['statement']+=' changed'
    with pytest.raises(TaskInterpretationRejected) as caught:decode_proposal(broken)
    assert caught.value.findings[0]['gate']=='G5'
    value=json.loads(record['result']['output_text'])
    value['facts'][0]['value']=0
    record['result']['output_text']=json.dumps(value)
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task,decode_proposal(record),intent)
    assert any(r['code']=='task.fact_not_source' for r in caught.value.findings)


def ui_and_authoring():
    text='Show sections Inventory and Events and the notice "Sample records".\nBuild only the Prototype; do not deploy it.'
    intent=capture_intent(text,project_ref='project:role-boundary')
    task=compile_task_spec(intent,compile_prototype_brief(intent))
    proposal={'schema':'adaos.builder.task_interpretation.v1','source_digest':intent['digest'],
        'clause_roles':[{'clause_id':'clause:001','roles':['product']},{'clause_id':'clause:002','roles':['policy']}],
        'requirements':[
            {'id':'ui','clause_ids':['clause:001'],'statement':task['clauses'][0]['text'].strip(),
             'kind':'ui','operation':None,'verification_family':'layout','supersedes':[]},
            {'id':'authoring','clause_ids':['clause:002'],'statement':task['clauses'][1]['text'].strip(),
             'kind':'authoring','operation':None,'verification_family':'policy','supersedes':[]}],
        'facts':[],'transitions':[],
        'constraints':[{'clause_ids':['clause:001'],'kind':'section_title','subject':'','values':['Inventory','Events']},
                       {'clause_ids':['clause:001'],'kind':'visible_text','subject':'','values':['Sample records']}],
        'questions':[],'design_freedom':[]}
    return intent,task,proposal


def test_disclosure_is_product_and_actual_authoring_policy_is_retained():
    intent,task,proposal=ui_and_authoring()
    admitted,brief=admit_interpretation(task,proposal,intent)
    assert admitted['interpretation_status']=='admitted'
    assert any(r['kind']=='authoring' for r in brief['task_contract']['requirements'])
    context=interpretation_messages(task)[0]['content']
    assert 'CLASSIFY THE REQUESTED ACTION' in context
    assert 'NO policy role' in context
    assert 'kind=policy or exclusion.' not in context


def test_old_inconsistent_policy_classification_is_still_refused_not_silently_removed():
    intent,task,proposal=ui_and_authoring()
    proposal['clause_roles'][0]['roles'].append('policy')
    before=copy.deepcopy(proposal)
    with pytest.raises(TaskInterpretationRejected) as caught:admit_interpretation(task,proposal,intent)
    assert any(r['code']=='task.policy_unrepresented' for r in caught.value.findings)
    assert proposal==before
