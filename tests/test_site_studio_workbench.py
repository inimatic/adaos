"""Guard the review composition boundary and retained optimistic-write contracts."""
import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location('studio_workbench', Path(__file__).parents[1] / 'e2e/stand/site_studio_workbench.py')
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)


def test_layout_move_preserves_nested_writes_confirmations_and_revision_guards():
    ids = ['v_sections', 'site_preview', 'site_discussion', 'target_description', 'description_proposals',
           'e_section', 'd_section', 'e_assets', 'material_intake', 'block_remark', 'block_feedback',
           'publication_commands', 'publication_domain_commands', 'component_upgrade_commands']
    command = {'on': 'submit', 'type': 'callSkill', 'target': 'studio.mutate_records',
               'params': {'project_id': '$state.site_project_id', 'selection_revision': '$state.project_selection_revision',
                          'expected_revision': '$event.record.revision', 'payload': '$event.values'},
               'confirmation': {'message': 'Confirm deletion'}, 'invalidates': ['site-studio:preview'],
               'followups': [{'type': 'callSkill', 'target': 'studio.query_publication', 'params': {}}]}
    legacy = {'ui': {'application': {'desktop': {'pageSchema': {'id': 'studio', 'widgets': [
        {'id': identifier, 'type': 'ui.form', 'inputs': {}, 'dataSource': {'kind': 'skill', 'name': 'studio.query_sections'},
         'actions': [command]} for identifier in ids]}}, 'modals': {}}}}
    before = studio.command_contracts(legacy)
    result = studio.compose(legacy)
    assert studio.command_contracts(result) == before
    assert legacy['ui']['application']['desktop']['pageSchema']['widgets'][0]['type'] == 'ui.form'
    app = result['ui']['application']
    assert app['modals']['section_properties']['title'] == 'Свойства'
    area = app['desktop']['pageSchema']['layout']['regions'][0]
    assert area['size']['resizable'] and area['size']['remember']
    identity = next(w for w in app['desktop']['pageSchema']['widgets'] if w['id'] == 'site_identity')
    assert [button['id'] for button in identity['inputs']['buttons']] == ['site-name', 'open-projects', 'knowledge']
    assert all(button['label'] == '' and button['title'] for button in identity['inputs']['buttons'][1:])
    assert 'site_version' not in app['modals']
    assert any(w['id'] == 'site_version_details' for w in app['modals']['site_knowledge']['schema']['widgets'])


def test_collection_properties_are_scoped_typed_fields_and_keep_parent_context():
    source = {'resources': [{'id': 'users-data', 'data': {'title': 'Users', 'items': [
        {'id': 'u-one', 'name': 'Anna', 'enabled': True, 'children': [{'name': 'Assistant', 'limit': 3}]}]}}],
        'routes': [{'sections': [{'id': 'users', 'resource_ref': 'users-data'}]}]}
    state = {}
    widgets = studio.property_copy(source, state)
    assert widgets[0]['area'] == 'main'
    tree = next(w for w in widgets if w['type'] == 'collection.tree')
    assert tree['visibleIf'] == "$state.selected_sections_id === 'users' && $state.property_tab === 'items'"
    fields = [field for w in widgets for field in w.get('inputs', {}).get('fields', [])]
    assert next(f for f in fields if f['id'] == 'enabled')['type'] == 'toggle'
    assert next(f for f in fields if f['id'] == 'limit')['type'] == 'number'
    assert state['component_drafts.users.block.items.0.children.0.name'] == 'Assistant'
    assert all(f['stateKey'].startswith('component_drafts.users.') for f in fields)


def test_structure_discussion_uses_the_same_agent_and_continuation_as_chat(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'e2e/stand'))
    from site_studio_beta import repair_structure_authoring, SKILL
    send = {'on': 'send', 'type': 'callSkill', 'target': SKILL + '.discuss',
            'params': {'text': '$event.text', 'project_id': '$state.site_project_id',
                       'selection_revision': '$state.project_selection_revision'},
            'resultStateKey': 'chat_send_result', 'invalidates': ['site-studio:discussion'],
            'followups': [{'type': 'updateState', 'params': {'chat_run': '$state.chat_send_result', 'chat_run_pending': True}}]}
    remark = {'id': 'block_remark', 'inputs': {'fields': [{'id': 'remark_text', 'title_i18n': 'old'}],
              'buttons': [{'id': 'send_block_remark', 'label_i18n': 'old'}]},
              'actions': [{'id': 'send_block_remark', 'type': 'callSkill', 'target': SKILL + '.post_remark'}]}
    editor = {'id': 'component_property_editor', 'inputs': {'buttons': []}, 'actions': []}
    tree = {'id': 'component_property_tree', 'inputs': {'expanded': True}}
    page = {'initialState': {'property_node_path': ''}, 'widgets': [
        {'id': 'site_discussion', 'actions': [send]},
        {'id': 'v_sections', 'actions': [{'type': 'openModal', 'params': {'modalId': 'section_properties', 'statePatch': {'selected_sections_id': '$event.id'}}}]},
    ]}
    app = {'modals': {'section_properties': {'schema': {'widgets': [tree, editor]}},
                      'block_discussion': {'schema': {'widgets': [remark]}}}}
    repair_structure_authoring(page, app)
    action = remark['actions'][0]
    assert action['target'] == SKILL + '.discuss'
    assert action['params']['subject'] == 'block' and action['params']['block_id'] == '$event.record.id'
    assert action['params']['text'] == '$event.values.remark_text'
    assert action['params']['project_id'] == '$state.site_project_id'
    assert action['followups'][0] == send['followups'][0]
    assert any(item.get('type') == 'closeModal' for item in action['followups'])
    assert page['initialState']['property_node_path'] == '__block__'
    assert page['widgets'][1]['actions'][0]['params']['statePatch']['property_node_path'] == '__block__'
    assert app['modals']['section_properties']['schema']['widgets'][0] is editor
    assert tree['inputs']['expanded'] is False
    repair_structure_authoring(page, app)
    assert len(editor['inputs']['buttons']) == 1 and len(editor['actions']) == 1
    assert editor['actions'][0]['type'] == 'copyToClipboard'


def test_review_version_counts_saved_ancestry_not_numeric_revision_gaps():
    history = {'revisions': [
        {'revision_id': 'draft-213', 'parent_revision_id': 'draft-150'},
        {'revision_id': 'draft-150', 'parent_revision_id': 'published'},
    ]}
    publication = {'active_revision_id': 'draft-213', 'published_revision_id': 'published', 'changes_pending': True}
    result = studio.review_version(publication, history)
    assert result['header'] == 'v0.1.2 · черновик'
    assert result['pending_count'] == 2
    publication['active_revision_id'] = 'draft-150'
    assert studio.review_version(publication, history)['header'] == 'v0.1.1 · черновик'
    publication.update(active_revision_id='published', changes_pending=False)
    assert studio.review_version(publication, history)['header'] == 'v0.1.0 · опубликовано'


def test_review_version_does_not_invent_numbers_for_missing_or_cyclic_history():
    publication = {'active_revision_id': 'a', 'published_revision_id': 'published', 'changes_pending': True}
    history = {'revisions': [{'revision_id': 'a', 'parent_revision_id': 'b'},
                             {'revision_id': 'b', 'parent_revision_id': 'a'}]}
    result = studio.review_version(publication, history)
    assert result['pending_count'] is None
    assert result['header'] == 'a · черновик'
    assert 'недоступна' in result['pending']


def test_source_review_declares_real_attachment_storage_and_revision_guarded_operations():
    from adaos.sdk.developer.prototypes import derive_resource_spec
    page = {'id': 'review', 'widgets': [], 'initialState': {'selected_source_material_id': ''}}
    app = {'desktop': {'pageSchema': page}, 'modals': {}}
    studio.add_source_review(page, app)
    webui = {'ui': {'application': app}}
    spec = derive_resource_spec(webui, studio.source_review_seed(b'<html>Private original</html>'), resource_type=studio.REVIEW_SOURCE_RESOURCE)
    schema = spec['resource_definition']['record_schema']
    assert schema['properties']['file_ref']['format'] == 'adaos-attachment'
    form = app['modals']['source_details']['schema']['widgets'][0]
    writes = {action['params']['operation_id']: action for action in form['actions']}
    assert writes['update']['params']['expected_revision'] == '$event.record.revision'
    assert writes['delete']['confirmation']
    assert all(action['target'] == studio.REVIEW_SOURCE_RESOURCE for action in writes.values())


def test_connected_preview_uses_one_mode_binding_and_retries_project_hydration(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'e2e/stand'))
    from site_studio_beta import connected_beta
    ids = ['v_sections', 'site_preview', 'site_discussion', 'target_description', 'description_proposals',
           'e_section', 'd_section', 'e_assets', 'material_intake', 'block_remark', 'block_feedback',
           'publication_commands', 'publication_domain_commands', 'component_upgrade_commands', 'change_proposals', 'site_structure']
    page = {'id': 'studio', 'widgets': [{'id': identifier, 'type': 'ui.form',
        'inputs': {'conversation': {}}, 'actions': [], 'dataSource': {'kind': 'skill', 'name': 'studio.query', 'params': {}}} for identifier in ids],
        'autoActions': [{'id': 'hydrate-active-project', 'once': True, 'action': {'type': 'callSkill', 'target': 'studio.query_active_project'}}]}
    result = connected_beta({'ui': {'application': {'desktop': {'pageSchema': page}, 'modals': {}}}})
    connected = result['ui']['application']['desktop']['pageSchema']
    preview = next(widget for widget in connected['widgets'] if widget['id'] == 'site_preview')['inputs']
    assert preview['interactionMode'] == '$state.' + preview['interactionModeStateKey']
    assert preview['inspectorModeStateKey'] != 'authoring_tool'
    hydration = next(action for action in connected['autoActions'] if action['id'] == 'hydrate-active-project')
    assert hydration['once'] is False and hydration['intervalMs'] >= 3000
    assert hydration['enabledIf'] == '$state.project_hydrated !== true'


def test_domain_actions_show_and_freeze_the_confirmed_target_and_expose_dns_details(monkeypatch):
    from copy import deepcopy
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'e2e/stand'))
    from site_studio_beta import repair_domain_controls
    scope = {'project_id': '$state.site_project_id', 'selection_revision': '$state.project_selection_revision'}
    def call(name):
        return {'on': 'click:' + name, 'type': 'callSkill', 'target': 'studio.' + name,
                'params': {**scope, 'hostname': '$state.selected_domain_hostname'}}
    activation = {'on': 'click:activate-domain', 'type': 'updateState', 'params': {},
                  'confirmation': {'message': 'Activate?'}, 'followups': [call('activate-domain')]}
    commands = {'id': 'publication_domain_commands', 'type': 'ui.actions', 'inputs': {'buttons': [
        {'id': name} for name in ['refresh-domains', 'activate-domain', 'set-primary-domain']]},
        'actions': [call('refresh-domains'), activation, call('set-primary-domain')]}
    domains = {'id': 'publication_domains', 'type': 'ui.list', 'inputs': {}, 'actions': []}
    dns = {'id': 'publication_domain_dns', 'type': 'ui.list', 'inputs': {'buttons': [{'id': 'copy-name'}, {'id': 'copy-value'}]},
           'actions': [{'on': 'click:copy-value', 'type': 'copyToClipboard', 'params': {'text': '$event.value'}}]}
    feedback = {'id': 'publication_domain_feedback', 'type': 'feedback.taskProgress',
                'dataSource': {'kind': 'static', 'value': {'message': {'then': 'Failed', 'else': 'Ready'}}}}
    page = {'widgets': [domains, commands, dns, feedback], 'initialState': {}}
    app = {'modals': {}}
    repair_domain_controls(page, app)
    assert domains['actions'][0]['params']['selected_domain_context'] == '$event'
    assert domains['actions'][0]['followups'][0]['params']['modalId'] == 'publication_domain_details'
    assert '$state.selected_domain_hostname' in activation['confirmation']['message']
    assert activation['params']['domain_action_hostname'] == '$state.selected_domain_hostname'
    assert activation['followups'][0]['params'] == {**scope, 'hostname': '$state.domain_action_hostname'}
    primary = next(action for action in commands['actions'] if action['on'] == 'click:set-primary-domain')
    assert '$state.selected_domain_hostname' in primary['confirmation']['message']
    assert primary['followups'][0]['params'] == {**scope, 'hostname': '$state.domain_action_hostname'}
    details = {widget['id']: widget for widget in app['modals']['publication_domain_details']['schema']['widgets']}
    assert details['publication_domain_details_dns']['dataSource']['value'] == '$state.selected_domain_context.dns_records'
    copy = details['publication_domain_details_dns']['actions'][0]
    assert copy['type'] == 'copyToClipboard' and copy['params']['text'] == '$event.value'
    assert details['publication_domain_details_feedback']['visibleIf'] == '$state.domain_feedback_visible === true'
    before = deepcopy((page, app))
    repair_domain_controls(page, app)
    assert (page, app) == before


def test_publication_refreshes_revision_before_prepare_and_preserves_reviewed_publish_candidate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'e2e/stand'))
    from site_studio_beta import _publication_feedback
    def operation(name, params):
        return {'on': 'click:' + name, 'type': 'callSkill', 'target': 'studio.' + name,
                'params': params, 'resultStateKey': 'publication_context', 'timeoutMs': 600000}
    page = {'initialState': {}, 'widgets': [
        {'id': 'publication_commands', 'inputs': {'buttons': [
            {'id': 'prepare', 'enabledIf': '$state.publication_context.item.can_prepare === true'},
            {'id': 'publish', 'enabledIf': '$state.publication_context.item.can_publish === true'}]},
         'actions': [operation('prepare', {'expected_source_digest': '$state.publication_context.item.active_source_digest'}),
                     operation('publish', {'plan_digest': '$state.publication_context.item.candidate_plan_digest'})]},
        {'id': 'authoring_tools', 'actions': [{'on': 'click:publication', 'type': 'updateState', 'params': {'authoring_tool': 'publication'}}]}],
        'autoActions': []}
    _publication_feedback(page)
    commands = next(w for w in page['widgets'] if w['id'] == 'publication_commands')
    assert commands['inputs']['variant'] == 'adaptiveToolbar'
    prepare, publish = commands['actions']
    assert prepare['type'] == 'updateState' and prepare['params']['publication_pending'] is True
    fresh = prepare['followups'][0]
    assert fresh['target'].endswith('.query_publication') and fresh['resultStateKey'] == 'publication_context'
    actual = fresh['followups'][1]
    assert actual['params']['expected_source_digest'] == '$state.publication_context.item.active_source_digest'
    assert actual['errorStateKey'] == fresh['errorStateKey'] == 'publication_error'
    assert actual['followups'][0]['params']['publication_pending'] is False
    assert publish['followups'][0]['target'] == 'studio.publish'
    assert publish['followups'][0]['params']['plan_digest'] == '$state.publication_context.item.candidate_plan_digest'
    feedback = next(w for w in page['widgets'] if w['id'] == 'publication_feedback')
    assert feedback['type'] == 'feedback.taskProgress'
    error_status = feedback['dataSource']['value']['status']['then']
    assert error_status['then'] == 'failed' and error_status['else'] == 'unknown'
    assert 'progress' not in feedback['dataSource']['value']
    assert all('publication_pending' in button['enabledIf'] for button in commands['inputs']['buttons'])
