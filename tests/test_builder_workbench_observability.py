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
    widgets = lambda ui: {node['id']: node for node in ui['ui']['application']['desktop']['pageSchema']['widgets']}
    for identity, widget in widgets(before).items():
        if identity != 'design-trace-tasks':
            assert widgets(after)[identity] == widget
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


def test_dynamic_context_keys_are_not_misclassified_as_missing_dictionary_entries():
    assert caption_keys({'title_i18n': '$state.current.summary_i18n', 'label_i18n': {'locales': {'en': 'Label', 'ru': 'Метка'}}}) == set()


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
