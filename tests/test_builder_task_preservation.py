import copy

import pytest

from adaos.services.builder.task_interpretation import admit_interpretation
from adaos.services.builder.task_preservation import fixture_write_scope, fixture_revision_findings
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_assertions import history, amendment
from test_builder_task_acceptance import fixture


def prepared_change():
    before = history()
    text = 'Сервер RU: CPU 65.\nОстальные данные, UI и поведение сохрани.'
    _, _, proposal, _ = amendment(before)
    intent = capture_intent(text, project_ref='project:qualification')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal['source_digest'] = intent['digest']
    proposal['requirements'][0].update(statement='Сервер RU: CPU 65.', kind='data_state')
    proposal['facts'][0]['record_evidence'] = 'Сервер RU: CPU 65.'
    proposal['clause_roles'].append({'clause_id':'clause:002','roles':['verification']})
    proposal['requirements'].append({'id':'preserve','clause_ids':['clause:002'],
        'statement':'Остальные данные, UI и поведение сохрани.', 'kind':'verification','operation':None,
        'verification_family':'preservation','supersedes':[]})
    _, brief = admit_interpretation(task, proposal, intent, previous_brief=before)
    _, previous = fixture()
    previous['resources'][0]['records'][0]['id'] = 'RU'
    previous['representative_states'] = []
    return previous, brief


def changed(previous):
    result = copy.deepcopy(previous)
    result['resources'][0]['records'][0]['cpu'] = 65
    return result


def test_core_derives_one_fixture_write_and_keeps_idempotent_new_value():
    previous, brief = prepared_change()
    snapshot = copy.deepcopy(previous)
    scope = fixture_write_scope(previous, brief)
    assert len(scope['writes']) == 1
    assert scope['writes'][0]['field_ref'] == 'cpu'
    assert scope['writes'][0]['before'] == 92 and scope['writes'][0]['after'] == 65
    assert fixture_revision_findings(previous, changed(previous), brief)[0] == []
    assert fixture_revision_findings(changed(previous), changed(previous), brief)[0] == []
    assert previous == snapshot


def test_native_sdk_compiler_view_uses_its_verified_document_not_the_capsule_envelope():
    from adaos.sdk.builder.prototype import semantic_revision_context
    from adaos.services.builder.task_interpretation import TaskInterpretationRejected
    previous, brief = prepared_change()
    capsule = semantic_revision_context(previous,brief,source_ref='semantic.webui.json',revision='001')
    assert fixture_write_scope(capsule,brief)['writes'] == fixture_write_scope(previous,brief)['writes']
    capsule['document']['resources'][0]['records'][0]['cpu'] = 42
    with pytest.raises(TaskInterpretationRejected) as error:fixture_write_scope(capsule,brief)
    assert error.value.findings[0]['code']=='task.fixture_scope_baseline_invalid'


def test_protected_json_numbers_compare_by_value_but_booleans_are_not_numbers():
    previous,brief=prepared_change()
    current=changed(previous)
    current['resources'][0]['records'][0]['disk']=78.0
    assert fixture_revision_findings(previous,current,brief)[0]==[]
    current['resources'][0]['records'][0]['disk']=True
    assert fixture_revision_findings(previous,current,brief)[0]


def test_record_dictionary_keys_are_not_treated_as_ui_languages():
    from adaos.services.builder.prototype_context import task_output_locales
    _,brief=prepared_change()
    baseline={'title':{'ru':'Прототип'},'resources':[{'id':'records','fields':[],
        'records':[{'id':'RU','metadata':{'en':'not a translation'}}]}]}
    assert task_output_locales(brief,baseline)==('ru',)


def test_scoped_provider_ids_allow_real_bindings_and_one_core_proof_not_invented_surfaces():
    from jsonschema import Draft202012Validator
    from adaos.sdk.builder.prototype import semantic_provider_contract
    from adaos.services.builder.task_preservation import fixture_provider_contract
    previous,brief=prepared_change()
    previous['document_id']='fixture'
    scope=fixture_write_scope(previous,brief)
    schema=fixture_provider_contract(semantic_provider_contract(version='v2',brief=brief,locales=('ru',)),previous,scope)
    Draft202012Validator.check_schema(schema)
    refs=Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/semanticRef'})
    refs.validate({'kind':'view','id':'servers'})
    refs.validate({'kind':'state','id':scope['writes'][0]['state_proof_id']})
    for bad in ({'kind':'surface','id':'main'},{'kind':'state','id':'servers.section'},{'kind':'field','id':'invented'}):
        assert list(refs.iter_errors(bad))


@pytest.mark.parametrize('fault',[None,'wrong_field','wrong_record','new_view','duplicate'])
def test_only_source_linked_non_ui_field_predicate_witnesses_may_be_added(fault):
    previous,brief=prepared_change()
    previous['views'][0]['field_refs']=['name','cpu','disk']
    current=changed(previous)
    state={'id':'cpu-proof','view_ref':'servers','label':{'ru':'CPU 65'},'min_items':1,'max_items':1,
        'filters':[{'field_ref':'name','operator':'eq','value':'RU'}],
        'proof':{'kind':'field_predicate','visible_field_refs':['name','cpu']}}
    if fault=='wrong_field':state['proof']['visible_field_refs']=['name','disk']
    elif fault=='wrong_record':state['filters'][0]['value']='EU'
    elif fault=='new_view':state['view_ref']='invented'
    current['representative_states']=[state]
    if fault=='duplicate':current['representative_states'].append({**copy.deepcopy(state),'id':'duplicate'})
    findings,_=fixture_revision_findings(previous,current,brief)
    assert bool(findings)==bool(fault)


def test_compiler_reports_collateral_writes_together_with_reference_errors(monkeypatch):
    from adaos.services.builder import semantic_prototype, task_preservation
    from test_builder_semantic_prototype import _multi_resource_fixture,_multi_resource_candidate
    brief,semantic=_multi_resource_fixture()
    candidate=_multi_resource_candidate(semantic)
    candidate['requirement_bindings'][0]['semantic_refs'].append({'kind':'state','id':'undeclared'})
    monkeypatch.setattr(task_preservation,'fixture_revision_findings',lambda *args:([
        {'code':'semantic.task_write_scope_exceeded','path':'$/commands/0/guard','detail':'protected'}],{}))
    with pytest.raises(semantic_prototype.SemanticPrototypeValidationError) as error:
        semantic_prototype.compile_semantic_prototype_candidate(candidate,brief=brief,previous=semantic)
    codes={row['code'] for row in error.value.findings}
    assert {'semantic.binding_reference_missing','semantic.task_write_scope_exceeded'}<=codes


@pytest.mark.parametrize('mutation', ['guard','title','states','records','order'])
def test_literal_amendment_cannot_change_other_semantic_properties(mutation):
    previous, brief = prepared_change()
    current = changed(previous)
    if mutation == 'guard':current['commands'][0]['guard'] = {'kind':'invented'}
    elif mutation == 'title':current['views'][0]['section']['label']['en'] = 'Changed'
    elif mutation == 'states':current['representative_states'] = [{'id':'new'}]
    elif mutation == 'records':current['resources'][0]['records'][0]['disk'] = 20
    elif mutation == 'order':current['views'].reverse()
    findings, scope = fixture_revision_findings(previous,current,brief)
    assert scope and findings and all(row['code'] == 'semantic.task_write_scope_exceeded' for row in findings)


def test_broader_ui_work_cannot_claim_a_fixture_only_proof():
    previous, brief = prepared_change()
    current_rows = [row for row in brief['task_contract']['requirements'] if row['source_digest'] == brief['intent_digest'] and row['kind']=='data_state']
    current_rows[0]['kind'] = 'ui'
    assert fixture_write_scope(previous,brief) is None


def test_ambiguous_or_stale_fixture_scope_fails_before_generation():
    from adaos.services.builder.task_interpretation import TaskInterpretationRejected
    previous, brief = prepared_change()
    previous['resources'][0]['records'][0]['cpu'] = 42
    with pytest.raises(TaskInterpretationRejected) as error:fixture_write_scope(previous,brief)
    assert error.value.findings[0]['code']=='task.fixture_scope_stale'


def test_sdk_candidate_compilation_cannot_bypass_fixture_preservation(monkeypatch):
    from adaos.sdk.builder import prototype
    from adaos.services.builder import semantic_prototype
    from adaos.services.builder.semantic_revision import SemanticRevisionError
    previous, brief = prepared_change()
    current = changed(previous)
    current['commands'][0]['guard'] = {'kind':'invented'}
    monkeypatch.setattr(semantic_prototype,'compile_semantic_prototype_candidate',lambda *args,**kwargs:{'semantic_document':current})
    with pytest.raises(SemanticRevisionError):prototype.compile_semantic_candidate({},brief=brief,previous=previous)


def test_compiler_pin_includes_task_preservation_and_acceptance_sources(monkeypatch):
    from pathlib import Path
    from adaos.services.builder.semantic_admission import compiler_digest
    before=compiler_digest()
    read=Path.read_bytes
    for filename in ('task_preservation.py','task_acceptance.py'):
        monkeypatch.setattr(Path,'read_bytes',lambda path:read(path)+b'\n# pin-probe' if path.name==filename else read(path))
        assert compiler_digest()!=before
        monkeypatch.setattr(Path,'read_bytes',read)
