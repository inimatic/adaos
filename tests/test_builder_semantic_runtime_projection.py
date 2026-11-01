"""Saved Builder output must remain editable without ignoring executable drift."""
import copy
import json

import pytest
import yaml

from adaos.services.builder.semantic_graph import verify_baseline
from adaos.services.builder.semantic_graph import SemanticGraphService
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService
from adaos.services.interface_deprecations import migrate_deprecated_interfaces
from test_builder_semantic_graph import make_graph_project, operation


def saved_projection(tmp_path):
    _service, _workflow, root, document = make_graph_project(tmp_path)
    editor = next(view for view in document['views'] if view['role'] == 'editor')
    editor['surface'] = 'modal'
    compiled = compile_semantic_prototype(document, project_ref='project:semantic_labels')
    document = compiled['semantic_document']
    webui, _ = migrate_deprecated_interfaces(compiled['webui'], owner=document['document_id'])

    def externalize(value):
        if isinstance(value, list):
            return [externalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {key: item['key'] if key.endswith('_i18n') and isinstance(item, dict)
                and item.get('key') else externalize(item) for key, item in value.items()}

    webui = externalize(webui)
    page = webui['ui']['application']['desktop']['pageSchema']
    binding = {'key': 'scenario.semantic_labels.title', 'fallback': 'Initial scenario title'}
    page['title_i18n'] = copy.deepcopy(binding)
    (root / 'scenario.yaml').write_text(yaml.safe_dump({'id': root.name,
        'title': page['title'], 'title_i18n': binding}), encoding='utf-8')
    return root, document, webui


def test_saved_public_views_and_external_locale_keys_match_canonical_source(tmp_path):
    root, document, webui = saved_projection(tmp_path)
    result = verify_baseline(document, webui, project_ref='project:semantic_labels', runtime_root=root)
    assert result['compiled']['semantic_document'] == document


@pytest.mark.parametrize('tamper', ['title_key', 'title_fallback', 'label', 'route', 'interface', 'legacy_interface', 'descriptor_fallback'])
def test_saved_representation_does_not_mask_runtime_drift(tmp_path, tamper):
    root, document, webui = saved_projection(tmp_path)
    app = webui['ui']['application']
    page = app['desktop']['pageSchema']
    if tamper == 'title_key':
        page['title_i18n']['key'] = 'scenario.another.title'
    elif tamper == 'title_fallback':
        page['title_i18n']['fallback'] = 'Unapproved title'
    elif tamper == 'label':
        page['widgets'][0]['title'] = 'Unapproved collection'
    elif tamper == 'route':
        def actions(value):
            if isinstance(value, dict):
                if value.get('type') == 'navigateModal':
                    yield value
                for item in value.values():
                    yield from actions(item)
            elif isinstance(value, list):
                for item in value:
                    yield from actions(item)
        action = next(actions(webui))
        assert action['params']['modalId'] in app['modals']
        action['params']['modalId'] = 'foreign.unapproved'
    elif tamper == 'interface':
        assert app['modals']
        app['modals']['foreign.unapproved'] = copy.deepcopy(next(iter(app['modals'].values())))
    elif tamper == 'legacy_interface':
        app.setdefault('interfaces', {})['foreign'] = {'views': {'foreign.unapproved': {'surfaces': ['modal']}}}
    else:
        page['widgets'][0]['title_i18n'] = {'key': page['widgets'][0]['title_i18n'],
                                          'fallback': 'Unapproved fallback'}
    with pytest.raises(BuilderWorkflowError, match='baseline drift'):
        verify_baseline(document, webui, project_ref='project:semantic_labels', runtime_root=root)


def test_scenario_binding_requires_its_own_canonical_manifest(tmp_path):
    root, document, webui = saved_projection(tmp_path)
    manifest = yaml.safe_load((root / 'scenario.yaml').read_text(encoding='utf-8'))
    manifest['id'] = 'another'
    (root / 'scenario.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')
    with pytest.raises(BuilderWorkflowError, match='baseline drift'):
        verify_baseline(document, webui, project_ref='project:semantic_labels', runtime_root=root)


def test_saved_projection_can_be_edited_and_its_new_baseline_verified(tmp_path):
    root, document, webui = saved_projection(tmp_path)
    (root / 'semantic.webui.json').write_text(json.dumps(document), encoding='utf-8')
    (root / 'webui.json').write_text(json.dumps(webui), encoding='utf-8')
    service = SemanticGraphService(BuilderWorkflowService(tmp_path / 'skills', root.parent,
                                                        state_dir=tmp_path / 'state'))
    result = service.apply(operation(service, document))
    assert result['revision'] == '002'
    updated = json.loads((root / 'semantic.webui.json').read_text(encoding='utf-8'))
    actual = json.loads((root / 'webui.json').read_text(encoding='utf-8'))
    verify_baseline(updated, actual, project_ref='project:semantic_labels', runtime_root=root)
