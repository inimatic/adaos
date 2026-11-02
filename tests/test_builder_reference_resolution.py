import copy

import pytest

from adaos.services.builder.interpretation_boundary import reference_context, resolve_reference, normalize_generic_selectors
from adaos.services.builder.task_interpretation import TaskInterpretationRejected


def base():
    return {'resources': [{'id': 'jobs', 'item_label': {'en': 'Job'}}, {'id': 'teams'}],
        'views': [
            {'id': 'main', 'role': 'collection', 'presentation': 'table', 'resource_ref': 'jobs',
             'title': {'en': 'Queue'}, 'section': {'id': 'repairs', 'title': {'en': 'Repairs'}}},
            {'id': 'board', 'role': 'collection', 'presentation': 'board', 'resource_ref': 'jobs'},
            {'id': 'teams', 'role': 'collection', 'presentation': 'table', 'resource_ref': 'teams',
             'section': {'id': 'teams', 'title': {'en': 'Teams'}}},
            {'id': 'editor', 'role': 'editor', 'resource_ref': 'jobs'}]}


def test_scoped_reference_resolves_once_and_retains_exact_base_and_owner():
    context = reference_context(base())
    ref = resolve_reference(context, role='collection', presentation='table', resource='jobs')
    assert ref['ref'] == 'view:main' and ref['section_ref'] == 'section:repairs'
    assert ref['resource_ref'] == 'resource:jobs' and ref['baseline_digest'] == context['baseline_digest']
    assert resolve_reference(context, role='editor')['ref'] == 'view:editor'
    assert resolve_reference(context, presentation='board')['ref'] == 'view:board'
    assert resolve_reference(context, section='Repairs')['ref'] == 'view:main'


@pytest.mark.parametrize('mutation', ['reorder', 'rename', 'translate', 'add_peer'])
def test_frozen_resolution_does_not_follow_later_display_or_peer_changes(mutation):
    document = base()
    context = reference_context(document)
    resolved = resolve_reference(context, presentation='table', resource='jobs')
    if mutation == 'reorder':
        document['views'].reverse()
    elif mutation == 'rename':
        document['views'][0]['title'] = {'en': 'New name'}
    elif mutation == 'translate':
        document['views'][0]['title'] = {'ru': 'Очередь'}
    else:
        document['views'].append({**document['views'][0], 'id': 'peer'})
    assert resolve_reference(context, presentation='table', resource='jobs') == resolved
    if mutation == 'add_peer':
        with pytest.raises(TaskInterpretationRejected):
            resolve_reference(reference_context(document), presentation='table', resource='jobs')


@pytest.mark.parametrize('selector', [dict(presentation='table'), dict(resource='unknown'), dict(ref='view:missing'),
                                    dict(role='editor', resource='teams'), dict(section='rep')])
def test_missing_or_ambiguous_reference_never_uses_first_or_fuzzy_caption(selector):
    with pytest.raises(TaskInterpretationRejected):
        resolve_reference(reference_context(base()), **selector)


def test_scoped_constructor_uses_same_resolver_without_mutating_raw_proposal():
    text = 'Set the title of the existing table in section "Repairs" for resource "jobs" to "Tasks".'
    task = {'clauses': [{'id': 'clause:001', 'text': text}], 'reference_context': reference_context(base())}
    proposal = {'constraints': [{'kind': 'view_title', 'subject': 'existing table in section "Repairs" for resource "jobs"',
        'values': ['Tasks'], 'clause_ids': ['clause:001']}]}
    before = copy.deepcopy(proposal)
    normalized, receipts = normalize_generic_selectors(task, proposal)
    assert proposal == before and normalized['constraints'][0]['subject'] == 'view:main'
    assert receipts[0]['resolution']['source_clause_ids'] == ['clause:001']


def test_runtime_selected_record_is_not_a_fixed_view_reference():
    task = {'clauses': [{'id': 'clause:001', 'text': 'Show details for the selected record.'}],
            'reference_context': reference_context(base())}
    value = {'constraints': [{'kind': 'view_title', 'subject': 'selected record', 'values': ['Record'], 'clause_ids': ['clause:001']}]}
    assert normalize_generic_selectors(task, value) == (value, [])


def test_core_implicit_directive_and_model_scoped_selector_admit_the_same_owner():
    from test_builder_semantic_ui_constraints import proposal
    from adaos.services.builder.task_spec import seal
    from adaos.services.builder.task_interpretation import admit_interpretation
    text = 'Set the title of the existing table in section "Repairs" for resource "jobs" to "Tasks".'
    intent, task, value = proposal(text)
    task = seal({**task, 'reference_context': reference_context(base())})
    value['constraints'] = [{'kind': 'view_title', 'subject': 'existing table in section "Repairs" for resource "jobs"',
        'values': ['Tasks'], 'clause_ids': ['clause:001']}]
    _, brief = admit_interpretation(task, value, intent)
    assert {row['subject'] for row in brief['task_contract']['constraints'] if row['kind'] == 'view_title'} == {'view:main'}
    value['constraints'][0]['subject'] = 'view:board'
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_interpretation(task, value, intent)
    assert caught.value.findings[0]['code'] == 'task.display_selector_conflict'


@pytest.mark.parametrize('role', ['details', 'editor'])
def test_typed_source_reference_keeps_record_projection_role_at_compiler_owner_boundary(role):
    from adaos.services.builder.semantic_ui_constraints import display_owner
    document = base()
    document['views'][-1]['role'] = role
    context = reference_context(document)
    ref = resolve_reference(context, role=role)['ref']
    owner = display_owner({'kind': 'view_title', 'subject': ref, 'values': ['New caption']},
        views={v['id']: v for v in document['views']}, resources={}, previous=document,
        bindings=[], shown=lambda view: [], matches=lambda *args: False, literal_matches=lambda *args: False,
        bound_field=lambda *args: None)
    assert owner == ref
