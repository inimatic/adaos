import json
from pathlib import Path
import pytest
from adaos.e2e.component_contract_conformance import load_slice, matched_runtime_episodes
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.ui_component_contracts import lowering_receipt

ROOT = Path(__file__).resolve().parents[1]


def test_independent_stock_consumer_preserves_state_through_real_binding_and_schema_variants(tmp_path):
    document = json.loads((ROOT/'e2e/builder/development/contract-conformance/inventory-fixture.json').read_text(encoding='utf-8'))
    compiled = compile_semantic_prototype(document, project_ref='project:independent-stock')
    rows = matched_runtime_episodes(compiled, tmp_path, episode_spec={'state_field': 'status', 'note_field': 'reserve_note',
        'before_state': 'available', 'after_state': 'reserved', 'command_id': 'stock-reserve', 'record_id': 'stock-1'})
    assert len(rows) == 7 and all(row['ok'] for row in rows), rows
    assert next(row for row in rows if row['episode'] == 'binding_swap')['binding_substitution']


@pytest.mark.parametrize('command_id', ['save', 'delete', 'complete'])
@pytest.mark.parametrize('identity', ['record-2', '$event.values.id', None])
def test_every_noncreate_command_requires_exact_selected_record_identity(command_id, identity):
    document, _ = load_slice(ROOT)
    compiled = compile_semantic_prototype(document, project_ref='project:record-identity')
    form = next(widget for modal in compiled['webui']['ui']['application']['modals'].values()
                for widget in modal['schema']['widgets'] if widget['id'] == 'record-editor')
    action = next(item for item in form['actions'] if item.get('id') == command_id)
    if identity is None:
        action['params'].pop('record_id')
    else:
        action['params']['record_id'] = identity
    with pytest.raises(ValueError, match='command_realization_missing'):
        lowering_receipt(document, compiled['webui'])
