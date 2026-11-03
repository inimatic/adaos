from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from test_builder_semantic_prototype import _multi_resource_fixture


def test_board_retains_selection_and_live_relationship_metadata_from_canonical_owner():
    brief, document = _multi_resource_fixture()
    view = next(v for v in document['views'] if v['id'] == 'work-list')
    view.update(presentation='board', presentation_options={'group_field_ref': 'result', 'draggable': False,
                'parent_field_ref': None, 'value_field_ref': None, 'order_field_ref': None},
                selection={'mode': 'single', 'indicator': 'row_accent', 'row_activation': 'select'})
    view['field_refs'].append('work_owner_id')
    document['relationships'][0]['label_field_refs'] = ['person_name']
    result = compile_semantic_prototype(document, brief=brief)
    board = next(w for w in result['webui']['ui']['application']['desktop']['pageSchema']['widgets'] if w['id'] == 'work-list')
    inputs = board['inputs']
    assert inputs['selectedStateKey']
    assert inputs['selectionMode'] == 'single'
    select = next(a for a in board['actions'] if a['on'] == 'select' and a['type'] == 'updateState')
    assert select['params'][inputs['selectedStateKey']] == '$event.id'
    owner = next(field for field in inputs['cardFields'] if field['key'] == 'work_owner_id')
    assert owner['optionsDataSource']['kind'] == 'resourceQuery'
    assert owner['optionValuePath'] == 'id' and owner['optionLabelPaths'] == ['person_name']
    assert owner['valueLabels'] and 'valueI18nPrefix' in owner
