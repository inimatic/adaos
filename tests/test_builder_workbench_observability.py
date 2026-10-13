import copy
import json
import importlib.util
from pathlib import Path
from jsonschema import Draft202012Validator
import pytest
from scripts.update_builder_workbench_observability import audit_locales, caption_keys, upgrade

ROOT = Path(__file__).resolve().parents[1]


def test_upgrade_preserves_current_builder_commands_and_issue_trace():
    path = ROOT / '.adaos/workspace/scenarios/builder'
    if not (path / 'webui.json').is_file():
        pytest.skip('Optional live Workspace Builder is not installed in this checkout')
    before = json.loads((path / 'webui.json').read_text(encoding='utf-8-sig'))
    after = upgrade(before)
    assert after['ui']['application']['modals']['design-trace'] == before['ui']['application']['modals']['design-trace']
    def widgets(ui):
        return {
            node['id']: node
            for node in ui['ui']['application']['desktop']['pageSchema']['widgets']
        }

    for identity, widget in widgets(before).items():
        if identity not in {'design-trace-tasks', 'design-preview-commands'}:
            expected = copy.deepcopy(widget)

            def expected_lazy_menu(value):
                if isinstance(value, dict):
                    if value.get('id') == 'specimens' and value.get('optionsDataSource'):
                        value.update(loadOptionsOnOpen=True, refreshOptionsOnOpen=True)
                    for nested in value.values():
                        expected_lazy_menu(nested)
                elif isinstance(value, list):
                    for nested in value:
                        expected_lazy_menu(nested)
            expected_lazy_menu(expected)
            assert widgets(after)[identity] == expected
    assert upgrade(after) == after
    assert audit_locales(after, path)['en'] == len(caption_keys(after))
    schema = json.loads((ROOT / 'src/adaos/abi/webui.v1.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator(schema).validate(after)
    table = widgets(after)['design-trace-tasks']
    assert table['inputs']['compactPresentation'] == 'cards'
    assert next(action for action in table['actions'] if action['type'] == 'openModal')['params']['modalId'] == 'design-run-details'
    modal = after['ui']['application']['modals']['design-run-details']['schema']['widgets']
    summary = next(widget for widget in modal if widget['id'] == 'design-run-execution')
    assert len(summary['inputs']['fields']) <= 8
    assert next(widget for widget in modal if widget['id'] == 'design-run-measurements')['visibleIf'] == '$state.selectedTrace.execution_metrics'


def test_semantic_inspector_is_a_single_on_demand_read_with_local_projections():
    path = ROOT / '.adaos/workspace/scenarios/builder/webui.json'
    if not path.is_file():
        pytest.skip('Optional live Builder source')
    ui = upgrade(json.loads(path.read_text(encoding='utf-8-sig')))
    app = ui['ui']['application']
    commands = next(item for item in app['desktop']['pageSchema']['widgets'] if item['id'] == 'design-preview-commands')
    actions = [item for item in commands['actions'] if item['on'] == 'click:semantic-inspector']
    assert [item['type'] for item in actions] == ['callSkill', 'openModal']
    assert actions[0]['resultStateKey'] == 'semanticInspector'
    assert actions[0]['target'] == 'builder_sdk_control_skill.get_semantic_inspector'
    widgets = app['modals']['design-semantic-inspector']['schema']['widgets']
    assert len(widgets) == 3 and all(item['dataSource']['kind'] == 'static' for item in widgets)
    assert all(item['dataSource']['value'].startswith('$state.semanticInspector') for item in widgets)
    assert next(item for item in widgets if item['id'] == 'design-semantic-refs')['inputs']['compactPresentation'] == 'cards'


def test_semantic_inspector_control_uses_primary_execution_identity(monkeypatch):
    control = control_module()
    monkeypatch.setattr(control, '_execution_identity', lambda *_: ('scenario', 'exact-primary'))
    seen = []
    def inspect(**kwargs):
        seen.append(kwargs)
        return {'status': 'verified', 'refs': [], 'write_set': []}
    monkeypatch.setattr(control.semantic_ui, 'inspect', inspect)
    assert control.get_semantic_inspector('project', 'owner')['status'] == 'verified'
    assert seen == [{'project_ref': 'scenario:exact-primary'}]


def test_dynamic_context_keys_are_not_misclassified_as_missing_dictionary_entries():
    assert caption_keys({'title_i18n': '$state.current.summary_i18n', 'label_i18n': {'locales': {'en': 'Label', 'ru': 'Метка'}}}) == set()


def test_builder_selection_uses_the_renderer_map_contract_not_ignored_mapping_alias():
    path = ROOT / '.adaos/workspace/scenarios/builder/webui.json'
    if not path.is_file():
        pytest.skip('Optional live Builder source')
    before = json.loads(path.read_text(encoding='utf-8-sig'))
    source = before['ui']['application']['desktop']['pageSchema']['initialStateSource']
    source['mapping'] = source.pop('map', source.get('mapping', {}))
    after = upgrade(before)['ui']['application']['desktop']['pageSchema']['initialStateSource']
    assert after['map']['selectedProjectId'] == 'object_id'
    assert 'mapping' not in after


def control_module():
    path = ROOT / '.adaos/workspace/skills/builder_sdk_control_skill/handlers/main.py'
    if not path.is_file():
        pytest.skip('Optional live Workspace Builder control skill is not installed in this checkout')
    spec = importlib.util.spec_from_file_location('builder_workbench_observability_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_model_failure_does_not_invent_selectable_models(monkeypatch):
    control = control_module()
    monkeypatch.setattr(control, '_execution_identity', lambda *value: value)
    monkeypatch.setattr(control, '_context', lambda *_: {'builder_llm_model': 'saved-model'})
    def unavailable(**_):
        raise TimeoutError()
    monkeypatch.setattr(control, 'list_llm_models', unavailable)
    result = control.get_llm_options('scenario', 'one')
    assert result['options'] == [] and result['ok'] is False
    assert result['value'] == 'saved-model'


def test_development_catalog_filters_unavailable_models_and_codex_scope():
    options = control_module()._llm_options({'model_profiles': [
        {'id': 'api-model', 'scope': 'development', 'provider': 'openai'},
        {'id': 'offline-model', 'scope': 'development', 'provider': 'openai'},
        {'id': 'codex-model', 'scope': 'automation', 'provider': 'openai-codex-cli'},
    ], 'data': [{'id': 'api-model', 'available': True}, {'id': 'offline-model', 'available': False}]})
    assert [item['id'] for item in options] == ['api-model']
