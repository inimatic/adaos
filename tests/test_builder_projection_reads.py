import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.development_tickets import DevelopmentTicketService


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
        _with_trial_permission_review=lambda payload: payload,
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
        _with_trial_permission_review=lambda payload: payload,
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


@pytest.mark.parametrize("legacy_cache", [False, True])
def test_trial_permission_wait_is_actionable_in_the_authoring_workbench(tmp_path, monkeypatch, legacy_cache):
    service = BuilderAutomationService(tmp_path, tmp_path, tmp_path / "skills", tmp_path / "scenarios")
    tickets = DevelopmentTicketService(state_dir=tmp_path)
    digest = "sha256:" + "a" * 64
    ticket = tickets.report_publication_gate_failure(
        component_type="scenario", component_id="example", gate="live_readiness",
        error="TrialPermissionReviewRequired", session_id="automation.scenario.example",
        task_id="task.validated", webspace_id="authoring",
        autonomous_repair_eligible=False, design_time_fixable=False,
        run_policy="await_user_permission_decision",
        metadata={"requires_user_decision": True, "decision_kind": "trial_permission", "package_digest": digest},
    )["ticket"]
    ticket_id = ticket["ticket_id"]
    session = {
        "object_type": "scenario", "object_id": "example", "session_id": "automation.scenario.example",
        "status": "failed", "webspace_id": "authoring", "current_task_id": "task.validated",
        "last_failure": {"stage": "live_readiness", "message": "TrialPermissionReviewRequired"},
        "completion_readiness": {"publication_gate_failure": {
            "ticket_id": ticket_id, "requires_user_decision": True,
        }},
    }
    service._save_session(session, emit_projection=False)
    cache_path = service._compact_status_path("scenario", "example")
    if legacy_cache:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        cached["session"].pop("publication_permission_ticket_id")
        cache_path.write_text(json.dumps(cached), encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("Reading permission state must not hydrate task artifacts")

    monkeypatch.setattr(BuilderAutomationService, "get_session", forbidden)
    projection = service.projection(object_type="scenario", object_id="example", webspace_id="other")
    expected = {"type": "review_trial_permissions", "ticket_id": ticket_id}
    for result in (projection, service.status(object_type="scenario", object_id="example", include_session=False)):
        assert result["session"]["status"] == "failed"
        assert result["automation"]["status"] == "awaiting_input"
        assert result["automation"]["busy"] is False
        assert result["automation"]["can_submit"] is False
        assert result["automation"]["error"] is None
        assert result["automation"]["next_action"] == expected

    tickets.handle_publication_permission_response(
        ticket_id=ticket_id, response_action_id="refuse", object_type="scenario", object_id="example",
        task_id="task.validated", package_digest=digest, pending_action_id="pa.test", resume=False,
    )
    answered = service.projection(object_type="scenario", object_id="example")
    assert "next_action" not in answered["automation"]
