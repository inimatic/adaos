import copy
import json
from pathlib import Path
import pytest
from adaos.e2e.component_contract_conformance import load_slice
from adaos.services.builder.semantic_graph import reduce_graph, guard_condition, guard_values, operation_write_set
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.builder.workflow import BuilderWorkflowError
from adaos.services.builder.semantic_admission import SemanticAdmissionService, PROPOSAL_SCHEMA
from test_builder_semantic_inspector import graph_project  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_guard_value_edit_preserves_complete_source_and_has_exact_inverse():
    source, _ = load_slice(ROOT)
    operation = {'seq': 1, 'kind': 'command.guard.value.set', 'ref': 'command:complete', 'expected': 'open', 'value': 'done'}
    changed = reduce_graph(source, [operation], write_refs=['command:complete'])
    expected = copy.deepcopy(source)
    guard_condition(expected, 'command:complete')['value'] = 'done'
    assert changed['semantic_document'] == expected
    assert operation_write_set([operation])[0]['property'] == 'guard.when.value'
    assert reduce_graph(expected, changed['inverse_ops'], write_refs=['command:complete'])['semantic_document'] == source
    compile_semantic_prototype(expected, project_ref='project:guard-qualified')


@pytest.mark.parametrize('value', ['other-state', '', True, 1])
def test_guard_edit_rejects_undeclared_or_coerced_domain(value):
    source, _ = load_slice(ROOT)
    with pytest.raises(BuilderWorkflowError, match='typed domain'):
        reduce_graph(source, [{'seq': 1, 'kind': 'command.guard.value.set', 'ref': 'command:complete',
            'expected': 'open', 'value': value}], write_refs=['command:complete'])


@pytest.mark.parametrize('mode', ['bounded_ops', 'full_document'])
def test_core_admission_guards_one_property_in_bounded_and_full_document_modes(graph_project, mode):
    graph, _, root, source = graph_project
    # The shared graph fixture has an existing conditional complete command.
    command = next(item for item in source['commands'] if item['id'] == 'complete')
    before = command['guard']['when']['value']
    value = next(value for value in guard_values(source, 'command:complete') if value != before)
    service = SemanticAdmissionService(graph)
    admission = service.begin(project_ref='scenario:'+root.name, instruction='Apply the note guard only to done records',
        source_message_id='guard.authoring', write_refs=['command:complete'], write_properties={'command:complete': ['guard.when.value']})
    proposed = {'schema': PROPOSAL_SCHEMA, 'status': 'proposed', 'message': '', 'ops': [
        {'kind': 'command.guard.value.set', 'ref': 'command:complete', 'value': value}]}
    expected = copy.deepcopy(source)
    guard_condition(expected, 'command:complete')['value'] = value
    result = (service.prepare(project_ref='scenario:'+root.name, admission_id=admission['admission_id'], proposal=proposed)
        if mode == 'bounded_ops' else service.prepare_document(project_ref='scenario:'+root.name, admission_id=admission['admission_id'],
            candidate={'status': 'proposed', 'message': '', 'semantic_document': expected}))
    assert result['request']['ops'][0]['expected'] == before
    applied = graph.apply(result['request'])
    assert graph.inspect(project_ref='scenario:'+root.name)['write_set'][0]['property'] == 'guard.when.value'
    changed = json.loads((root/'semantic.webui.json').read_text())
    assert changed == expected
