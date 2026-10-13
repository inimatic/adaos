import copy
import json

import pytest

from adaos.services.builder import semantic_graph as graph
from adaos.services.builder import semantic_admission
from adaos.services.builder.workflow import BuilderWorkflowError
from test_builder_semantic_graph import graph_project  # noqa: F401


def test_cached_compilation_still_checks_actual_runtime_and_is_not_mutable(graph_project, monkeypatch):
    _service, _workflow, root, document = graph_project
    graph._BASELINE_COMPILE_CACHE.clear()
    ui = json.loads((root / 'webui.json').read_text(encoding='utf-8'))
    compile_original = graph.compile_semantic_prototype
    calls = []
    def compile_counted(*args, **kwargs):
        calls.append(kwargs['primary_locale'])
        return compile_original(*args, **kwargs)
    monkeypatch.setattr(graph, 'compile_semantic_prototype', compile_counted)
    first = graph.verify_baseline(document, ui, project_ref='project:semantic_labels')
    first['compiled']['webui']['tampered'] = True
    assert 'tampered' not in graph.verify_baseline(document, ui, project_ref='project:semantic_labels')['compiled']['webui']
    assert calls == ['en']
    drift = copy.deepcopy(ui)
    drift['ui']['application']['desktop']['pageSchema']['title'] = 'Unadmitted renderer edit'
    with pytest.raises(BuilderWorkflowError, match='drift'):
        graph.verify_baseline(document, drift, project_ref='project:semantic_labels')
    assert calls == ['en', 'ru']
    monkeypatch.setattr(semantic_admission, 'compiler_digest', lambda: 'changed-compiler-dependencies')
    graph.verify_baseline(document, ui, project_ref='project:semantic_labels')
    assert calls == ['en', 'ru', 'en']
