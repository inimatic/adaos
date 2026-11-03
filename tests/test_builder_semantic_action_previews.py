import copy

import pytest

from adaos.services.builder.semantic_prototype import compile_semantic_prototype_candidate, compile_semantic_prototype
from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_semantic_prototype import _multi_resource_fixture, _multi_resource_candidate, _automation_requirement


def candidate():
    brief, semantic = _multi_resource_fixture()
    result = _multi_resource_candidate(semantic)
    reference = brief['principal_jobs'][0]['id']
    result['automation_requirements'] = [{**_automation_requirement(reference), 'reason': 'external_integration'}]
    next(row for row in result['requirement_bindings'] if row['requirement_ref'] == reference)['semantic_refs'] = [
        {'kind': 'view', 'id': result['views'][0]['id']}]
    result['views'][0]['preview_actions'] = [
        {'id': str(index), 'label': {'en': label, 'ru': label},
         'description': {'en': 'Review the external action.', 'ru': 'Просмотр внешнего действия.'},
         'primary': index < 2, 'requirement_ref': reference}
        for index, label in enumerate(['Open', 'Review', 'Remove'])]
    return brief, result


def test_external_action_previews_compile_to_disclosed_navigation_and_preserve_data():
    brief, source = candidate()
    original = copy.deepcopy(source)
    result = compile_semantic_prototype_candidate(source, brief=brief)
    assert source == original
    application = result['webui']['ui']['application']
    page = application['desktop']['pageSchema']
    collection = next(row for row in page['widgets'] if row['id'] == source['views'][0]['id'])
    preview_buttons = [row for row in collection['inputs']['buttons'] if row['id'].startswith('preview-')]
    assert len(preview_buttons) == 3 and preview_buttons[-1]['icon'] == 'ellipsis-horizontal'
    preview_events = [row for row in collection['actions'] if row['on'].startswith('click:preview-')]
    assert len(preview_events) == 3 and all(row['type'] == 'navigateModal' for row in preview_events)
    reviews = [modal for name, modal in application['modals'].items() if name.startswith('preview-')]
    assert len(reviews) == 4
    notices = [field for modal in reviews for widget in modal['schema']['widgets']
               for field in widget.get('inputs', {}).get('fields', []) if field['id'] == 'notice']
    assert len(notices) == 3 and all('No operation was executed' in row['value'] for row in notices)
    assert page['meta']['builder']['external_action_previews'] is True
    replay = compile_semantic_prototype(result['semantic_document'], brief=brief)
    assert replay['webui'] == result['webui']
    assert replay['prototype_resources'] == result['prototype_resources']
    assert result['automation_requirements'][0]['status'] == 'pending_automation'


@pytest.mark.parametrize('fault', ['missing_obligation', 'wrong_reason', 'duplicate', 'too_many_primary', 'editor'])
def test_external_action_previews_refuse_unbound_or_ambiguous_controls(fault):
    brief, source = candidate()
    actions = source['views'][0]['preview_actions']
    if fault == 'missing_obligation':
        actions[0]['requirement_ref'] = 'missing'
    elif fault == 'wrong_reason':
        source['automation_requirements'][0]['reason'] = 'business_rule'
    elif fault == 'duplicate':
        actions[1]['id'] = actions[0]['id']
    elif fault == 'too_many_primary':
        actions[2]['primary'] = True
    else:
        source['views'][1]['preview_actions'] = source['views'][0].pop('preview_actions')
    with pytest.raises(BuilderWorkflowError):
        compile_semantic_prototype_candidate(source, brief=brief)
