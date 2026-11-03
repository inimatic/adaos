import copy

import pytest

from adaos.services.builder.task_acceptance import task_candidate_findings, task_acceptance_receipt
from adaos.services.builder.task_interpretation import admit_interpretation
from test_builder_task_interpretation import prepared


def fixture(extra_transition=None):
    intent, task, proposal = prepared()
    if extra_transition:
        from adaos.services.builder_intent import capture_intent, compile_prototype_brief
        from adaos.services.builder.task_spec import compile_task_spec
        label, before, after = extra_transition
        source = f'Команда «{label}» меняет статус {before} на {after}, только это поле.'
        intent = capture_intent(intent['statement'] + '\n' + source, project_ref=intent['scope']['project_ref'])
        task = compile_task_spec(intent, compile_prototype_brief(intent))
        proposal['source_digest'] = intent['digest']
        proposal['clause_roles'].append({'clause_id':'clause:005','roles':['product']})
        proposal['requirements'].append({'id':label.lower(),'clause_ids':['clause:005'],
            'statement':source,'kind':'behavior','operation':'transition',
            'verification_family':'transition','supersedes':[]})
        proposal['transitions'].append({'clause_ids':['clause:005'],'requirement_id':label.lower(),
            'command_label':label,'field':'статус','from_value':before,'to_value':after,'preserve_other_fields':True})
    _, brief = admit_interpretation(task, proposal, intent)
    transition = brief['task_contract']['transitions'][0]
    document = {'title': {'ru': 'Инфраструктура'}, 'resources': [
        {'id': 'servers', 'fields': [{'id': 'name', 'label': {'ru': 'Имя'}, 'value_type': 'short_text'},
            {'id': 'cpu', 'label': {'en': 'CPU %'}, 'value_type': 'number'},
            {'id': 'disk', 'label': {'en': 'Disk %'}, 'value_type': 'number'}],
         'records': [{'id': 'ru', 'name': 'RU', 'cpu': 92, 'disk': 78}]},
        {'id': 'incidents', 'read_only_when': {'field_ref': 'status', 'operator': 'not_equals', 'value': 'open'},
         'fields': [{'id': 'status', 'label': {'ru': 'Статус'}, 'value_type': 'choice',
            'options': [{'value': 'open', 'label': {'en': 'open'}}, {'value': 'accepted', 'label': {'en': 'accepted'}}]}],
         'records': [{'id': 'incident-1', 'status': 'open'}]}],
        'views': [{'id': 'servers', 'resource_ref': 'servers', 'role': 'collection',
                   'section': {'id': 'backups', 'label': {'ru': 'Копии'}}},
                  {'id': 'incident-editor', 'resource_ref': 'incidents', 'role': 'editor'}],
        'commands': [{'id': 'accept', 'kind': 'transition', 'label': {'ru': 'Принять'}, 'view_ref': 'incident-editor',
                      'fixed_values': {'status': 'accepted'}, 'input_field_refs': []}],
        'requirement_bindings': [{'requirement_ref': transition['requirement_id'], 'semantic_refs': ['command:accept']}],
        'capability_surfaces': []}
    return brief, document


def test_static_source_acceptance_does_not_claim_browser_acceptance():
    brief, doc = fixture()
    assert task_candidate_findings(doc, brief) == []
    receipt = task_acceptance_receipt(doc, brief)
    assert receipt['static_status'] == 'passed'
    assert receipt['status'] == 'runtime_acceptance_pending'
    assert all(row['status'] == 'not_run' for row in receipt['runtime_checks'])
    assert receipt['source_literal_coverage']['covered_atoms'] == 2
    assert receipt['source_literal_coverage']['general_source_completeness_proven'] is False


def test_stale_incomplete_brief_cannot_bypass_literal_recall_at_candidate_acceptance():
    from adaos.services.builder.repair_diagnosis import compile_repair_diagnosis
    brief, document = fixture()
    brief['task_contract']['facts'] = brief['task_contract']['facts'][:1]
    document['resources'][0]['records'][0]['disk'] = 0
    findings = task_candidate_findings(document,brief)
    missing = [row for row in findings if row['code']=='task.source_record_attribute_unextracted']
    assert len(missing)==1 and missing[0]['gate']=='G3' and missing[0]['owner']=='task_interpretation'
    diagnosis = compile_repair_diagnosis(candidate_digest='candidate',findings=missing,
        parent={'generation_plan_id':'plan','task_spec_ref':'task'},compiler_digest='compiler')
    assert diagnosis['failure_classes']==['interpretation']
    assert diagnosis['paid_candidate_repair_allowed'] is False
    assert diagnosis['next_actions']==['readmit_task']
    receipt = task_acceptance_receipt(document,brief)
    assert receipt['static_status']=='failed'
    assert receipt['source_literal_coverage']['checked_atoms']==2
    assert receipt['source_literal_coverage']['covered_atoms']==1


def test_source_projection_context_failure_cannot_authorize_candidate_repair():
    from adaos.services.builder.repair_diagnosis import compile_repair_diagnosis
    diagnosis = compile_repair_diagnosis(candidate_digest='candidate',findings=[{
        'code':'task.source_literal_source_projection_invalid','gate':'G5','owner':'context_compilation'}],
        parent={'generation_plan_id':'plan','task_spec_ref':'task'},compiler_digest='compiler')
    assert diagnosis['failure_classes']==['context_delivery']
    assert diagnosis['paid_candidate_repair_allowed'] is False
    assert diagnosis['next_actions']==['recompile_context']


def test_persisted_retagging_cannot_skip_source_values_or_buy_candidate_repair():
    from adaos.services.builder.repair_diagnosis import compile_repair_diagnosis

    brief, document = fixture()
    brief['task_contract']['facts'] = []
    brief['task_contract']['clause_roles'][0]['roles'] = ['product']
    document['resources'][0]['records'][0]['disk'] = 0
    findings = task_candidate_findings(document, brief)
    unresolved = [row for row in findings if row['code'] == 'task.source_record_role_unresolved']
    assert len(unresolved) == 2
    diagnosis = compile_repair_diagnosis(candidate_digest='candidate', findings=unresolved,
        parent={'generation_plan_id':'plan','task_spec_ref':'task'}, compiler_digest='compiler')
    assert diagnosis['failure_classes'] == ['interpretation']
    assert diagnosis['paid_candidate_repair_allowed'] is False
    assert diagnosis['next_actions'] == ['readmit_task']
    receipt = task_acceptance_receipt(document, brief)
    assert receipt['static_status'] == 'failed'
    assert receipt['source_literal_coverage']['recognized_atoms_accounted_for'] is False


@pytest.mark.parametrize('fake_policy', ['missing', 'required_guard', 'target_only', 'wrong_field', 'wrong_value'])
def test_transition_requires_a_real_inverse_stored_state_precondition(fake_policy):
    brief, doc = fixture()
    resource = doc['resources'][1]
    if fake_policy in {'missing', 'required_guard'}:
        resource.pop('read_only_when')
        if fake_policy == 'required_guard':
            doc['commands'][0]['guard'] = {'when': {'field_ref': 'status', 'operator': 'equals', 'value': 'open'},
                                         'require_nonempty': ['status']}
    elif fake_policy == 'target_only':
        # Even a required choice may be null in persistent draft records.
        resource['fields'][0]['required'] = True
        resource['read_only_when'].update(operator='equals', value='accepted')
    elif fake_policy == 'wrong_field':
        resource['read_only_when']['field_ref'] = 'other'
    else:
        resource['read_only_when']['value'] = 'accepted'
    findings = task_candidate_findings(doc, brief)
    expected = 'unenforced' if fake_policy in {'missing', 'required_guard'} else 'mismatch'
    row = next(row for row in findings if row['code'] == 'task.transition_precondition_' + expected)
    assert row['gate'] == 'G8' and row['owner'] == 'candidate'
    assert row['source_clause_ids'] == brief['task_contract']['transitions'][0]['clause_ids']


def test_resource_lock_cannot_silently_restrict_an_unrelated_mutable_command():
    brief, doc = fixture()
    doc['commands'].append({'id': 'rename', 'kind': 'update', 'label': {'ru': 'Rename'},
        'view_ref': 'incident-editor', 'input_field_refs': ['title'], 'fixed_values': {}})
    assert any(row['code'] == 'task.transition_precondition_scope_mismatch'
               for row in task_candidate_findings(doc, brief))


def test_multiple_commands_can_share_the_same_source_owned_precondition():
    brief, doc = fixture(('Close','open','closed'))
    transition = brief['task_contract']['transitions'][-1]
    doc['commands'].append({'id': 'close', 'kind': 'transition', 'label': {'ru': 'Close'},
        'view_ref': 'incident-editor', 'input_field_refs': [], 'fixed_values': {'status': 'closed'}})
    doc['requirement_bindings'].append({'requirement_ref': transition['requirement_id'], 'semantic_refs': ['command:close']})
    assert task_candidate_findings(doc, brief) == []


def test_resource_wide_policy_does_not_claim_to_implement_different_command_preconditions():
    brief, doc = fixture(('Reopen','accepted','open'))
    transition = brief['task_contract']['transitions'][-1]
    doc['resources'][1]['records'].append({'id': 'other', 'status': 'accepted'})
    doc['commands'].append({'id': 'reopen', 'kind': 'transition', 'label': {'ru': 'Reopen'},
        'view_ref': 'incident-editor', 'input_field_refs': [], 'fixed_values': {'status': 'open'}})
    doc['requirement_bindings'].append({'requirement_ref': transition['requirement_id'], 'semantic_refs': ['command:reopen']})
    codes = {row['code'] for row in task_candidate_findings(doc, brief)}
    assert 'task.transition_precondition_mismatch' in codes
    assert 'task.transition_precondition_scope_mismatch' in codes


def test_extra_command_cannot_borrow_an_unrelated_source_transition():
    brief, doc = fixture()
    transition = copy.deepcopy(brief['task_contract']['transitions'][0])
    transition.update(command_label='Close', requirement_id='close', to_value='closed')
    brief['task_contract']['transitions'].append(transition)
    findings = task_candidate_findings(doc, brief)
    finding = next(row for row in findings if row['code']=='task.source_transition_command_mismatch')
    assert finding['gate']=='G3' and finding['owner']=='task_interpretation'


def test_other_resource_commands_do_not_collide_with_the_stored_state_lock():
    brief, doc = fixture()
    doc['commands'].append({'id': 'rename', 'kind': 'update', 'view_ref': 'servers',
        'label': {'ru': 'Rename'}, 'input_field_refs': ['name'], 'fixed_values': {}})
    assert task_candidate_findings(doc, brief) == []


@pytest.mark.parametrize('fault,code', [
    ('cpu', 'fixture_value_mismatch'), ('bool_cpu', 'fixture_value_mismatch'),
    ('effect', 'transition_effect_mismatch'), ('other_field', 'transition_preservation_mismatch'),
    ('section', 'section_title_mismatch'), ('dependency', 'external_dependency_forbidden'),
    ('duplicate_record', 'fixture_subject_unresolved'),
])
def test_mutations_rejected_even_when_model_authored_bindings_remain_intact(fault, code):
    brief, doc = fixture()
    if fault == 'cpu': doc['resources'][0]['records'][0]['cpu'] = 0
    elif fault == 'bool_cpu': doc['resources'][0]['records'][0]['cpu'] = True
    elif fault == 'effect': doc['commands'][0]['fixed_values']['status'] = 'open'
    elif fault == 'other_field': doc['commands'][0]['fixed_values']['owner'] = 'anyone'
    elif fault == 'section': doc['views'][0]['section']['label']['ru'] = 'Другое'
    elif fault == 'dependency': doc['capability_surfaces'].append({'id': 'invented-site'})
    elif fault == 'duplicate_record': doc['resources'][0]['records'].append(copy.deepcopy(doc['resources'][0]['records'][0]))
    findings = task_candidate_findings(doc, brief)
    assert any(code in row['code'] for row in findings)


def test_a_foreign_key_value_cannot_retarget_an_unrelated_resource_fact():
    brief, doc = fixture()
    doc['resources'][1]['records'][0]['server_ref'] = 'RU'
    assert task_candidate_findings(doc, brief) == []


def test_negative_field_labels_cannot_satisfy_a_source_attribute_by_token_subset():
    brief, doc = fixture()
    doc['resources'][0]['fields'][1].update(id='not_cpu', label={'en':'Not CPU'})
    doc['resources'][0]['records'][0]['not_cpu'] = doc['resources'][0]['records'][0].pop('cpu')
    assert any(row['code'] == 'task.fixture_subject_unresolved' for row in task_candidate_findings(doc, brief))


def test_dotted_field_ids_and_unit_labels_are_supported_but_not_fuzzy_names():
    brief, doc = fixture()
    doc['resources'][0]['fields'][1].update(id='server.cpu', label={'en':'CPU (%)'})
    doc['resources'][0]['records'][0]['server.cpu'] = doc['resources'][0]['records'][0].pop('cpu')
    assert task_candidate_findings(doc, brief) == []


def test_search_scope_can_identify_a_named_section_without_renaming_its_collection():
    brief, doc = fixture()
    brief['task_contract']['constraints'].append({'kind': 'search_fields', 'subject': 'Копии',
        'values': ['name'], 'clause_ids': ['clause:004']})
    doc['views'][0]['title'] = {'ru': 'Серверы'}
    doc['views'][0]['query_controls'] = [{'kind': 'search', 'field_ref': 'name'}]
    assert task_candidate_findings(doc, brief) == []
    doc['views'][0]['query_controls'][0]['field_ref'] = 'cpu'
    assert any(row['code'] == 'task.query_scope_mismatch' for row in task_candidate_findings(doc, brief))


def test_a_second_view_cannot_launder_a_conflicting_title_for_the_same_section():
    brief, doc = fixture()
    doc['views'][1]['section'] = copy.deepcopy(doc['views'][0]['section'])
    doc['views'][0]['section']['label']['ru'] = 'Другое'
    assert any(row['code']=='task.section_title_mismatch' for row in task_candidate_findings(doc,brief))


def test_search_in_a_negated_scope_cannot_satisfy_the_requested_section():
    brief, doc = fixture()
    brief['task_contract']['constraints'].append({'kind':'search_fields','subject':'Копии',
        'values':['name'],'clause_ids':['clause:004']})
    unrelated=copy.deepcopy(doc['views'][0])
    unrelated.update(id='other',section={'id':'not-backups','label':{'ru':'Не Копии'}},
        query_controls=[{'kind':'search','field_ref':'name'}])
    doc['views'].append(unrelated)
    assert any(row['code']=='task.query_scope_unresolved' for row in task_candidate_findings(doc,brief))


def test_another_language_cannot_prove_the_requested_visible_section_title():
    brief,doc=fixture()
    doc['views'][0]['section']['label']={'en':'Копии','ru':'Другое'}
    assert any(row['code']=='task.section_title_mismatch' for row in task_candidate_findings(doc,brief))


def test_disclosure_in_metadata_is_not_a_visible_ui_explanation():
    brief, doc = fixture()
    brief['task_contract']['constraints'].append({'kind': 'visible_text', 'subject': '',
        'values': ['Демонстрационные данные'], 'clause_ids': ['clause:004']})
    doc['title']['ru'] += ' · Демонстрационные данные'
    assert any(row['code'] == 'task.disclosure_text_missing' for row in task_candidate_findings(doc, brief))
    doc['views'][0]['title'] = {'ru': 'Демонстрационные данные'}
    assert task_candidate_findings(doc, brief) == []


def test_exposed_display_policy_has_a_constructive_combined_section_and_notice_witness():
    from adaos.services.builder.task_acceptance import display_assertion_contract
    from adaos.services.builder.semantic_input import authoring_contract
    rules=display_assertion_contract()['rules']
    assert rules['section_title']['whole_normalized_text'] is True
    assert rules['visible_text']['whole_normalized_text'] is False
    example=authoring_contract()['examples']['section_and_view']['view']
    brief,doc=fixture()
    doc['views'][0]['section']['label']['ru']=example['section']['title']['en']
    doc['views'][0]['title']={'ru':example['title']['en']}
    brief['task_contract']['constraints']=[
        {'kind':'section_title','subject':'','values':[example['section']['title']['en']], 'clause_ids':['clause:004']},
        {'kind':'visible_text','subject':'','values':[example['title']['en']], 'clause_ids':['clause:004']}]
    assert not task_candidate_findings(doc,brief)
    doc['views'][0]['section']['label']['ru']+=' — '+example['title']['en']
    assert any(row['code']=='task.section_title_mismatch' for row in task_candidate_findings(doc,brief))


def test_a_choice_label_cannot_redefine_a_literal_command_effect():
    brief, doc = fixture()
    options = doc['resources'][1]['fields'][0]['options']
    options[1].update(value='open', label={'en': 'accepted'})
    doc['commands'][0]['fixed_values']['status'] = 'open'
    assert any(row['code'] == 'task.transition_effect_mismatch' for row in task_candidate_findings(doc, brief))


def test_section_and_notice_text_are_not_bags_of_unordered_tokens():
    brief, doc = fixture()
    doc['views'][0]['section']['label']['ru'] = 'Не Копии'
    assert any(row['code'] == 'task.section_title_mismatch' for row in task_candidate_findings(doc, brief))
    brief['task_contract']['constraints'] = [{'kind': 'visible_text', 'subject': '',
        'values': ['Демонстрационные данные'], 'clause_ids': ['clause:004']}]
    doc['views'][0]['title'] = {'ru': 'Данные не демонстрационные'}
    assert any(row['code'] == 'task.disclosure_text_missing' for row in task_candidate_findings(doc, brief))


@pytest.mark.parametrize('kind,code', [('section_title', 'task.section_title_mismatch'),
    ('visible_text', 'task.disclosure_text_missing')])
def test_a_technical_identifier_is_not_evidence_of_visible_text(kind, code):
    brief, doc = fixture()
    brief['task_contract']['constraints'] = [{'kind': kind, 'subject': '', 'values': ['Backups'], 'clause_ids': ['clause:004']}]
    doc['views'][0].update(id='Backups', title={'en': 'Something else'}, section={'id': 'Backups', 'label': {'en': 'Wrong'}})
    assert any(row['code'] == code for row in task_candidate_findings(doc, brief))
