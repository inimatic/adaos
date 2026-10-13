import pytest

from test_builder_semantic_graph import make_graph_project


@pytest.fixture
def graph_project(tmp_path):
    return make_graph_project(tmp_path)


def test_edit_context_matches_the_full_authority_projection(graph_project):
    _service, workflow, root, _document = graph_project
    full = workflow.describe('scenario', root.name)
    bounded = workflow.prototype_edit_context('scenario', root.name)
    assert bounded['generation'] == full['generation']
    assert bounded['change']['change_id'] == full['change']['change_id']
    assert bounded['capabilities']['can_edit_prototype'] == full['capabilities']['can_edit_prototype']
    assert 'runs' not in bounded['change'] and 'process' not in bounded and 'workflow_inspection' not in bounded


def test_edit_admission_never_constructs_display_projections(graph_project, monkeypatch):
    service, workflow, root, document = graph_project
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Display-only projections are outside edit admission')
    monkeypatch.setattr(type(workflow), '_workflow_inspection', forbidden)
    monkeypatch.setattr(type(workflow), '_process_projection', forbidden)
    title = document['views'][0]['title']['en']
    prepared = service.prepare_request(project_ref='scenario:' + root.name,
        instruction=f'Rename view "{title}" to "Work queue" in English', source_message_id='real-source')
    assert prepared['status'] == 'planned'
    monkeypatch.undo()  # Commit notifications still retain their full public projection contract.
    assert service.apply(prepared['request'])['revision'] == '002'


def test_archived_edit_context_matches_full_refusal(graph_project, monkeypatch):
    _service, workflow, root, _document = graph_project
    state = workflow._read_state('scenario', root.name)
    monkeypatch.setattr(type(workflow), '_read_state', lambda *_args: {**state, 'archived': True})
    bounded = workflow.prototype_edit_context('scenario', root.name)
    assert bounded['archived']
    assert not bounded['capabilities']['can_edit_prototype']
