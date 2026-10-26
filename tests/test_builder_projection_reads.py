from copy import deepcopy
from types import SimpleNamespace

import pytest

from adaos.services.builder.automation import BuilderAutomationService


@pytest.mark.parametrize('status', ['queued', 'running', 'commit_ready', 'completed', 'failed'])
@pytest.mark.parametrize('addressed', [False, True])
def test_projection_never_executes_or_rebinds(status, addressed):
    session = {'status': status, 'conversation_id': 'original',
               'conversation_binding_source': 'generated_builder_topic', 'updated_at': 'before'}
    before = deepcopy(session)

    def forbidden(*args, **kwargs):
        pytest.fail('A read attempted execution, persistence, or notification')

    service = SimpleNamespace(
        get_session=lambda *args: session,
        find_active_session=lambda **kwargs: session,
        project_session=lambda value: {'status': value['status']},
        refresh_session=forbidden, _save_session=forbidden,
        _reconcile_required_aprobation=forbidden, _notify_completed_session=forbidden,
    )
    for _ in range(2):
        result = BuilderAutomationService.projection(
            service, object_type='scenario' if addressed else None,
            object_id='example' if addressed else None,
            webspace_id='desktop', conversation_id='another-conversation')
        assert result['automation']['status'] == status
        assert session == before


def test_missing_projection_is_an_explicit_read_result():
    service = SimpleNamespace(get_session=lambda *args: None,
                              empty_projection=lambda **kwargs: {'status': 'idle'})
    result = BuilderAutomationService.projection(service, object_type='scenario', object_id='missing')
    assert result['error'] == 'automation_session_not_found'


@pytest.mark.parametrize('include_session', [False, True])
def test_status_never_executes_or_persists(include_session):
    def forbidden(*args, **kwargs):
        pytest.fail('Status must be a pure read')
    session = {'status': 'completed', 'conversation_id': 'original'}
    service = SimpleNamespace(get_session=lambda *args: session,
        _read_compact_status=lambda *args: None, project_session=lambda value: value,
        compact_session=lambda value: value, refresh_session=forbidden,
        _reconcile_required_aprobation=forbidden, _notify_completed_session=forbidden,
        _save_session=forbidden)
    assert BuilderAutomationService.status(service, object_type='scenario', object_id='example',
        include_session=include_session)['session'] == session


def test_addressed_projection_uses_compact_snapshot_without_hydrating_task_artifacts():
    def forbidden(*args, **kwargs):
        pytest.fail('Render read must not hydrate the full execution history')
    cached = {'ok': True, 'session': {'object_type': 'scenario', 'object_id': 'example'},
              'automation': {'status': 'completed'}}
    service = SimpleNamespace(_read_compact_status=lambda *args: cached, get_session=forbidden)
    assert BuilderAutomationService.projection(service, object_type='scenario', object_id='example') == cached
