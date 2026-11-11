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
