import copy

import pytest

from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder.task_interpretation import admit_interpretation, TaskInterpretationRejected
from adaos.services.builder_intent import capture_intent, compile_prototype_brief, merge_prototype_briefs
from adaos.sdk.builder import prototype


TEXT = ('Покажи сервер RU: CPU 92, disk 78.\n'
        'Команда «Принять» меняет статус open на accepted, только это поле.\n'
        'Без внешних вызовов и device_control.\n'
        'Раздел «Копии». Проверь ширину 390 px.')


def prepared():
    intent = capture_intent(TEXT, project_ref='project:qualification')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal = {
        'schema': 'adaos.builder.task_interpretation.v1', 'source_digest': intent['digest'],
        'clause_roles': [{'clause_id': f'clause:{n:03d}', 'roles': roles} for n, roles in enumerate(
            [['product', 'fact'], ['product'], ['policy'], ['product', 'verification']], 1)],
        'requirements': [
            {'id': 'servers', 'clause_ids': ['clause:001'], 'statement': 'Покажи сервер RU',
             'kind': 'behavior', 'operation': 'inspect', 'verification_family': 'runtime', 'supersedes': []},
            {'id': 'accept', 'clause_ids': ['clause:002'], 'statement': 'Команда «Принять» меняет статус open на accepted, только это поле.',
             'kind': 'behavior', 'operation': 'transition', 'verification_family': 'transition', 'supersedes': []},
            {'id': 'policy', 'clause_ids': ['clause:003'], 'statement': 'Без внешних вызовов и device_control.',
             'kind': 'policy', 'operation': None, 'verification_family': 'policy', 'supersedes': []},
            {'id': 'copies', 'clause_ids': ['clause:004'], 'statement': 'Раздел «Копии».',
             'kind': 'ui', 'operation': None, 'verification_family': 'layout', 'supersedes': []}],
        'facts': [{'clause_ids': ['clause:001'], 'subject': 'RU', 'field': 'CPU', 'source_field': 'CPU', 'value': 92,
                   'record_evidence': 'Покажи сервер RU: CPU 92, disk 78.', 'field_evidence': 'CPU 92'},
                  {'clause_ids': ['clause:001'], 'subject': 'RU', 'field': 'disk', 'source_field': 'disk', 'value': 78,
                   'record_evidence': 'Покажи сервер RU: CPU 92, disk 78.', 'field_evidence': 'disk 78'}],
        'transitions': [{'clause_ids': ['clause:002'], 'requirement_id': 'accept', 'command_label': 'Принять',
                         'field': 'статус', 'from_value': 'open', 'to_value': 'accepted', 'preserve_other_fields': True}],
        'constraints': [{'clause_ids': ['clause:003'], 'kind': 'forbid_external', 'subject': '', 'values': []},
                        {'clause_ids': ['clause:004'], 'kind': 'section_title', 'subject': '', 'values': ['Копии']},
                        {'clause_ids': ['clause:004'], 'kind': 'viewport', 'subject': '', 'values': ['390']}],
        'questions': [], 'design_freedom': ['таблица или карточки'],
    }
    return intent, task, proposal


def test_interpretation_preserves_literals_policy_and_verification_owner():
    intent, task, proposal = prepared()
    accepted, brief = admit_interpretation(task, proposal, intent)
    assert accepted['interpretation_status'] == 'admitted'
    context = prototype.model_context(brief, compact=True)
    assert [row['kind'] for row in context['operations']] == ['inspect', 'transition']
    assert context['task_acceptance']['facts'][0]['value'] == 92
    assert 'device_control' in context['process_constraints'][0]['statement']
    assert not any('device_control' in row['statement'] for row in context['required_references'])
    assert brief['representative_states']['state'] == 'unknown'
    assert merge_prototype_briefs(brief) == brief
    assert proposal == prepared()[2]


@pytest.mark.parametrize('fault,code', [
    ('source', 'source_coverage_incomplete'), ('value', 'fact_not_source'),
    ('statement', 'statement_not_source'), ('operation', 'interpretation_shape_invalid'),
    ('transition', 'transition_no_effect'), ('question', 'clarification_required'),
    ('unextracted_fact', 'fact_unextracted'), ('missing_transition', 'transition_unextracted'),
])
def test_bad_interpretation_blocks_before_candidate_generation(fault, code):
    intent, task, proposal = prepared()
    if fault == 'source': proposal['clause_roles'].pop()
    elif fault == 'value': proposal['facts'][0]['value'] = 9
    elif fault == 'statement': proposal['requirements'][0]['statement'] = 'Удалить сервер'
    elif fault == 'operation': proposal['requirements'][2]['operation'] = 'delete'
    elif fault == 'transition': proposal['transitions'][0]['to_value'] = 'open'
    elif fault == 'question': proposal['questions'] = [{'clause_ids': ['clause:002'], 'question': 'Кому разрешено действие?'}]
    elif fault == 'unextracted_fact': proposal['facts'] = []
    elif fault == 'missing_transition': proposal['transitions'] = []
    with pytest.raises(TaskInterpretationRejected) as raised:
        admit_interpretation(task, proposal, intent)
    assert any(code in row['code'] for row in raised.value.findings)


def test_interpreted_history_cannot_be_silently_unioned_with_raw_extraction():
    intent, task, proposal = prepared()
    _, brief = admit_interpretation(task, proposal, intent)
    with pytest.raises(ValueError, match='next turn'):
        merge_prototype_briefs(brief, compile_prototype_brief('Теперь поиск только по имени.'))


def test_provider_schema_pins_source_and_discriminates_operation_owners():
    from jsonschema import Draft202012Validator
    from adaos.services.builder.task_interpretation import interpretation_contract
    _, task, proposal = prepared()
    contract = interpretation_contract(provider=True, task=task)
    Draft202012Validator.check_schema(contract)
    validator = Draft202012Validator(contract)
    for row in proposal['requirements']:
        row['statement'] = next(clause['text'].strip() for clause in task['clauses'] if clause['id'] == row['clause_ids'][0])
    validator.validate(proposal)
    for kind in ('information', 'policy', 'verification'):
        wrong = copy.deepcopy(proposal)
        wrong['requirements'][0]['kind'] = kind
        assert list(validator.iter_errors(wrong))
    wrong = copy.deepcopy(proposal)
    wrong['clause_roles'][0]['clause_id'] = 'invented'
    assert list(validator.iter_errors(wrong))


def test_provider_selects_source_clauses_instead_of_rewriting_punctuation():
    from jsonschema import Draft202012Validator
    from adaos.services.builder.task_interpretation import interpretation_contract
    _, task, proposal = prepared()
    validator = Draft202012Validator(interpretation_contract(provider=True, task=task))
    for row in proposal['requirements']:
        row['statement'] = next(clause['text'].strip() for clause in task['clauses'] if clause['id'] == row['clause_ids'][0])
    validator.validate(proposal)
    proposal['requirements'][0]['statement'] = proposal['requirements'][0]['statement'].rstrip('.') + ';'
    assert list(validator.iter_errors(proposal))


def test_more_than_three_questions_are_rejected_not_silently_truncated():
    intent, task, proposal = prepared()
    proposal['questions'] = [{'clause_ids': ['clause:002'], 'question': 'Question '+str(index)} for index in range(4)]
    with pytest.raises(TaskInterpretationRejected) as error:
        admit_interpretation(task, proposal, intent)
    assert error.value.findings[0]['code'] == 'task.interpretation_shape_invalid'
    assert len(proposal['questions']) == 4


@pytest.mark.parametrize('kind',['too_many_clauses','too_large_source'])
def test_complete_provider_source_enums_fail_before_submission_when_over_capacity(kind):
    from adaos.services.builder.task_interpretation import interpretation_contract
    _,task,_=prepared()
    if kind=='too_many_clauses':task['clauses']=[{'id':f'clause:{index:04d}','text':f'Clause {index}'} for index in range(350)]
    else:task['clauses']=[{'id':'clause:001','text':'x'*120000}]
    snapshot=copy.deepcopy(task)
    with pytest.raises(TaskInterpretationRejected) as error:interpretation_contract(provider=True,task=task)
    assert error.value.findings[0]['code']=='task.interpretation_schema_capacity'
    assert task==snapshot


def test_locale_and_viewport_normalization_preserve_source_words():
    intent, task, proposal = prepared()
    _, brief = admit_interpretation(task, proposal, intent)
    width = next(row for row in brief['task_contract']['constraints'] if row['kind'] == 'viewport')
    assert width['values'] == ['390'] and width['widths_px'] == [390]


@pytest.mark.parametrize('text', ['390px','390 px','390пкс','390 пикселей'])
def test_canonical_width_is_grounded_by_a_source_pixel_dimension(text):
    from adaos.services.builder.task_interpretation import _constraint_literal
    assert _constraint_literal('On a '+text+' viewport.','viewport','390')


@pytest.mark.parametrize('text', ['1390px','3900px','390.5px','-390px','390dp','w390px'])
def test_pixel_projection_does_not_relax_other_numeric_or_unit_boundaries(text):
    from adaos.services.builder.task_interpretation import _constraint_literal
    assert not _constraint_literal('On a '+text+' viewport.','viewport','390')


def test_dimension_projection_is_not_a_generic_fact_or_label_substring_match():
    from adaos.services.builder.task_interpretation import _constraint_literal
    assert not _constraint_literal('On 390px.','visible_text','390')
    assert not _constraint_literal('On 390px.','search_fields','390')


def test_normalized_identifier_is_distinct_from_the_source_field_label():
    intent, task, proposal = prepared()
    proposal['facts'][0]['field'] = 'cpu_percent'
    _, brief = admit_interpretation(task, proposal, intent)
    fact = brief['task_contract']['facts'][0]
    assert fact['field'] == 'cpu_percent' and fact['source_field'] == 'CPU'


def test_literal_ui_evidence_is_not_misclassified_as_a_missing_record_fact():
    intent, task, proposal = prepared()
    proposal['clause_roles'][3]['roles'].append('fact')
    _, brief = admit_interpretation(task, proposal, intent)
    assert not any(row['subject'] == 'Копии' for row in brief['task_contract']['facts'])
    assert any(row['kind'] == 'section_title' for row in brief['task_contract']['constraints'])


def test_positive_authoring_does_not_become_an_application_operation():
    text = 'Создай локальный Prototype на русском языке.'
    intent = capture_intent(text, project_ref='project:qualification')
    task = compile_task_spec(intent, compile_prototype_brief(intent))
    proposal = {'schema': 'adaos.builder.task_interpretation.v1', 'source_digest': intent['digest'],
        'clause_roles': [{'clause_id': 'clause:001', 'roles': ['product', 'policy']}],
        'requirements': [{'id': 'authoring', 'clause_ids': ['clause:001'], 'statement': text,
            'kind': 'authoring', 'operation': None, 'verification_family': 'policy', 'supersedes': []}],
        'facts': [], 'transitions': [], 'constraints': [{'kind': 'locale', 'subject': '',
            'values': ['русском языке'], 'clause_ids': ['clause:001']}], 'questions': [], 'design_freedom': []}
    _, brief = admit_interpretation(task, proposal, intent)
    context = prototype.model_context(brief, compact=True)
    assert text == context['process_constraints'][0]['statement']
    assert not context['operations']


def test_fact_cannot_swap_values_between_neighbouring_source_fields():
    intent, task, proposal = prepared()
    fact = proposal['facts'][0]
    fact.update(source_field='disk', field='disk', value=92,
                field_evidence='CPU 92, disk 78')
    with pytest.raises(TaskInterpretationRejected) as raised:
        admit_interpretation(task, proposal, intent)
    assert any(row['code'] == 'task.fact_not_source' for row in raised.value.findings)
