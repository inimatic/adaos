from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import component_updates as updates_api
from adaos.services.component_updates import ComponentUpdateService


def test_notice_projects_exact_fenced_cutover_instead_of_offering_accept_again(tmp_path):
    from adaos.domain.application import RuntimeSelection
    from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
    from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep

    digest = 'sha256:' + 'a' * 64
    channel = ApplicationRuntimeChannel(tmp_path, 'sample')
    selected = RuntimeSelection(webspace_id='desktop', application_id='sample', source='local_trial',
        release_digest=digest, runtime_root_ref='trial:candidate', revision=1)
    channel.select(selected, expected_revision=0)
    service = ComponentUpdateService(state_dir=tmp_path)
    service.record_aprobation(component_type='scenario', component_id='sample',
        aprobation={'source_kind': 'builder_local_trial', 'runtime_selection': selected.to_dict(),
            'trial': {'candidate_id': 'candidate', 'candidate_digest': digest, 'release_digest': digest}},
        webspace_id='desktop')
    from dataclasses import replace
    import pytest

    def fail(_key):
        current = service.list_notices()[0]['transition']
        assert current['state'] == 'publishing'
        assert current['requires_user_decision'] is False
        raise ValueError('private error')

    with pytest.raises(ValueError):
        ApplicationRuntimeTransition(channel).run('application-stable:sample:candidate',
            contract_digest=digest, expected=[selected],
            target=replace(selected, source='stable_installation', runtime_root_ref='workspace', revision=2),
            steps=[TransitionStep('protect_and_adopt_data', fail)])
    notice = service.list_notices()[0]
    assert notice['transition']['state'] == 'recovery_required'
    assert notice['transition']['requires_user_decision'] is False
    assert notice['transition']['progress']['phase'] == 'protect_and_adopt_data'
    assert 'private error' not in str(notice)


def test_missing_dev_or_stale_selection_does_not_hide_other_trial_notices(tmp_path, monkeypatch):
    from adaos.sdk.builder import applications, workflow
    from adaos.services.applications import store as stores

    selections = [SimpleNamespace(webspace_id="desktop", source="local_trial", application_id=name, release_digest="digest")
                  for name in ["missing-release", "missing-dev", "active"]]
    def release(application_id, _digest):
        if application_id == "missing-release":
            raise FileNotFoundError(application_id)
        return SimpleNamespace(accepted_candidate_id="candidate", release_digest="digest", project_release=SimpleNamespace(version="0.1.0"))
    def state(_kind, scenario):
        if scenario == "missing-dev":
            raise FileNotFoundError(scenario)
        return {"delivery": {"candidate_id": "candidate", "package_digest": "package", "status": "trial"},
                "change": {"request": "Private raw task prompt must never be release notes"}}
    store = SimpleNamespace(list_runtime_selections=lambda: selections, get_release=release,
        get_application=lambda identifier: SimpleNamespace(publisher_ref="subnet:local", display={"title": identifier}))
    monkeypatch.setattr(stores, "ApplicationStore", lambda _root: store)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:local")
    monkeypatch.setattr(applications, "_primary_scenario", lambda app: app.display["title"])
    monkeypatch.setattr(workflow, "get_state", state)
    service = ComponentUpdateService(state_dir=tmp_path)
    recorded = []
    monkeypatch.setattr(ComponentUpdateService, "record_aprobation", lambda self, **kwargs: recorded.append(kwargs) or {"ok": True})
    assert service.reconcile_local_trials("desktop") == 1
    assert recorded[0]["component_id"] == "active"
    assert recorded[0]["aprobation"]["runtime_selection"]["application_id"] == "active"
    assert recorded[0]["aprobation"]["changelog"]["summary"] == "Release notes have not been provided."
    recorded.clear()
    assert service.reconcile_local_trials("desktop", application_id="missing-dev") == 0
    assert recorded == []


def test_reconcile_local_trial_recovers_selected_candidate_from_immutable_evidence(
    tmp_path, monkeypatch
):
    from adaos.sdk.builder import applications, workflow
    from adaos.sdk.developer import projects
    from adaos.services.applications import store as stores
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

    selection = SimpleNamespace(
        webspace_id="desktop",
        application_id="web_desktop",
        source="local_trial",
        release_digest="sha256:selected-release",
        runtime_root_ref="trial:selected-candidate",
        revision=14,
        to_dict=lambda: {
            "webspace_id": "desktop",
            "application_id": "web_desktop",
            "source": "local_trial",
            "release_digest": "sha256:selected-release",
            "runtime_root_ref": "trial:selected-candidate",
            "revision": 14,
        },
    )
    release = SimpleNamespace(
        accepted_candidate_id="selected-candidate",
        release_digest=selection.release_digest,
        project_release=SimpleNamespace(version="0.3.48"),
    )
    store = SimpleNamespace(
        list_runtime_selections=lambda: [selection],
        get_application=lambda _application_id: SimpleNamespace(
            publisher_ref="subnet:local",
            display={"title": "Web Desktop", "summary": "Selected immutable Beta"},
        ),
        get_release=lambda _application_id, _release_digest: release,
    )
    monkeypatch.setattr(stores, "ApplicationStore", lambda _root: store)
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:local")
    monkeypatch.setattr(applications, "_primary_scenario", lambda _application: "web_desktop")
    monkeypatch.setattr(workflow, "get_state", lambda *_args: {"delivery": {
        "candidate_id": "stale-candidate",
        "package_digest": "sha256:stale-package",
        "release_digest": "sha256:stale-release",
        "status": "stale",
    }})
    monkeypatch.setattr(projects, "get_candidate", lambda _candidate_id: {"candidate": {
        "candidate_id": "selected-candidate",
        "package_digest": "sha256:selected-package",
        "release_digest": "sha256:selected-release",
        "status": "accepted",
    }})
    monkeypatch.setattr(TrialActivationStore, "load", lambda self, _candidate_id: {
        "status": "completed",
        "candidate_ref": {
            "candidate_id": "selected-candidate",
            "package_digest": "sha256:selected-package",
            "release_digest": "sha256:selected-release",
        },
        "target": {"webspace_id": "desktop"},
    })
    recorded = []
    monkeypatch.setattr(
        ComponentUpdateService,
        "record_aprobation",
        lambda self, **kwargs: recorded.append(kwargs) or {"ok": True},
    )

    assert ComponentUpdateService(state_dir=tmp_path).reconcile_local_trials("desktop") == 1
    assert recorded[0]["aprobation"]["trial"] == {
        "candidate_id": "selected-candidate",
        "candidate_digest": "sha256:selected-package",
        "release_digest": "sha256:selected-release",
        "version": "0.3.48",
        "status": "trial",
    }
    assert recorded[0]["aprobation"]["runtime_selection"]["revision"] == 14
    assert recorded[0]["webspace_id"] == "desktop"


def _aprobation(
    *,
    status: str = "trial",
    decision: str = "",
    candidate_id: str = "candidate.demo.1",
    version: str = "0.2.0",
) -> dict:
    trial = {
        "candidate_id": candidate_id,
        "candidate_digest": f"sha256:{candidate_id}",
        "release_digest": f"sha256:release-{candidate_id}",
        "version": version,
        "status": status,
        "started_at": "2026-09-01T10:00:00Z",
    }
    if decision:
        trial["decision"] = decision
    return {
        "ok": True,
        "audience": "beta",
        "source_kind": "candidate_package",
        "trial": trial,
        "changelog": {
            "title": "Demo Metrics update",
            "summary": "Metrics table now uses the Resource Workbench.",
            "changes": ["Use typed queries", "Add edit and delete actions"],
            "ticket_ids": ["dticket.demo"],
        },
    }


def test_component_update_tracks_release_and_viewer_lifecycle(tmp_path: Path) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)

    alpha = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(),
        webspace_id="desktop",
    )

    assert alpha is not None
    assert alpha["stage"] == "beta"
    assert alpha["review_state"] == "pending"
    assert alpha["version"] == "0.2.0"
    assert service.list_notices(actor="user:owner", webspace_id="desktop")[0]["auto_prompt"] is True

    presented = service.respond(
        alpha["notice_id"],
        action="dismiss_auto",
        actor="user:owner",
        webspace_id="desktop",
    )
    assert presented["unread"] is True
    assert presented["auto_prompt"] is False

    reviewed = service.respond(
        alpha["notice_id"],
        action="review_started",
        actor="user:owner",
        webspace_id="desktop",
    )
    assert reviewed["unread"] is False
    assert reviewed["viewer_state"]["review_started_at"]

    publishing = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(status="accepted", decision="accept"),
        webspace_id="desktop",
    )
    assert publishing is not None
    assert publishing["stage"] == "beta"
    assert publishing["status"] == "active"
    assert publishing["review_state"] == "publishing"

    published = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(status="published", decision="accept"),
        webspace_id="desktop",
    )
    assert published is not None
    assert published["notice_id"] == alpha["notice_id"]
    assert published["stage"] == "stable"
    assert published["status"] == "accepted"
    assert published["review_state"] == "accepted"
    assert published["title"] == "Demo Metrics update"
    assert published["transition"] == {
        "state": "accepted",
        "requires_user_decision": False,
        "workspace_committed": True,
        "workspace_version": "0.2.0",
        "release_digest": "sha256:release-candidate.demo.1",
    }
    assert service.active_component_metadata("skill", "demo_metrics_skill") is None
    current_metadata = service.current_component_metadata("skill", "demo_metrics_skill")
    assert current_metadata is not None
    assert current_metadata["stage"] == "stable"
    assert current_metadata["version"] == "0.2.0"
    assert "viewer_state" not in current_metadata
    assert service.list_notices(status="accepted")[0]["notice_id"] == alpha["notice_id"]


def test_component_update_current_view_returns_latest_changeset_per_component(
    tmp_path: Path,
) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)
    published = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(status="published", decision="accept"),
    )
    pending = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(
            candidate_id="candidate.demo.2",
            version="0.3.0",
        ),
    )
    other = service.record_aprobation(
        component_type="skill",
        component_id="subscription_status_skill",
        aprobation=_aprobation(
            candidate_id="candidate.subscription.1",
            version="0.1.0",
        ),
    )

    current = service.list_notices(status="current")

    assert published is not None
    assert pending is not None
    assert other is not None
    assert {item["notice_id"] for item in current} == {
        pending["notice_id"],
        other["notice_id"],
    }
    demo = next(item for item in current if item["component"]["id"] == "demo_metrics_skill")
    assert demo["stage"] == "beta"
    assert demo["transition"]["requires_user_decision"] is True
    assert demo["transition"]["workspace_committed"] is False


def test_presented_notice_stays_dismissed_after_reload_without_marking_it_read(tmp_path: Path) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)
    notice = service.record_aprobation(
        component_type="skill", component_id="demo", aprobation=_aprobation()
    )
    service.respond(notice["notice_id"], action="presented", actor="browser", webspace_id="homepoint")

    # Reconciliation and a fresh service must preserve the viewer's dismissal.
    service = ComponentUpdateService(state_dir=tmp_path)
    service.record_aprobation(component_type="skill", component_id="demo", aprobation=_aprobation())
    current = service.list_notices(actor="browser", webspace_id="homepoint")[0]
    assert current["auto_prompt"] is False
    assert current["unread"] is True
    assert "viewed_at" not in current["viewer_state"]
    assert current["transition"]["requires_user_decision"] is True
    assert service.list_notices(actor="other", webspace_id="homepoint")[0]["auto_prompt"] is True
    assert service.list_notices(actor="browser", webspace_id="other")[0]["auto_prompt"] is True

    newer = service.record_aprobation(
        component_type="skill", component_id="demo",
        aprobation=_aprobation(candidate_id="candidate.demo.2", version="0.3.0"),
    )
    current = service.list_notices(actor="browser", webspace_id="homepoint", status="current")[0]
    assert current["notice_id"] == newer["notice_id"]
    assert current["auto_prompt"] is True


def test_restore_auto_allows_an_unread_dismissed_notice_to_prompt_again(tmp_path: Path) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)
    notice = service.record_aprobation(
        component_type="skill", component_id="demo", aprobation=_aprobation()
    )
    service.respond(notice["notice_id"], action="dismiss_auto")
    restored = service.respond(notice["notice_id"], action="restore_auto")
    assert restored["unread"] is True
    assert restored["auto_prompt"] is True


def test_current_view_resolves_only_visible_cutovers(tmp_path, monkeypatch) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)
    old = service.record_aprobation(component_type="skill", component_id="demo",
        aprobation=_aprobation(candidate_id="old"))
    newest = service.record_aprobation(component_type="skill", component_id="demo",
        aprobation=_aprobation(candidate_id="new"))
    state = service._read()
    state["notices"][old["notice_id"]]["updated_at"] = "2026-01-01"
    state["notices"][old["notice_id"]]["status"] = "accepted"
    state["notices"][newest["notice_id"]]["updated_at"] = "2026-01-02"
    service._write(state)
    projected = []
    monkeypatch.setattr(ComponentUpdateService, "_project_core_transition",
        lambda self, notice, **kwargs: projected.append(notice["notice_id"]))
    assert [n["notice_id"] for n in service.list_notices(status="current")] == [newest["notice_id"]]
    assert projected == [newest["notice_id"]]
    # The unread view still selects the newest unread changeset, even when a
    # newer changeset for that component was already read.
    service.respond(newest["notice_id"], action="review_started")
    projected.clear()
    assert [n["notice_id"] for n in service.list_notices(status="current", unread_only=True)] == [old["notice_id"]]
    assert projected == [old["notice_id"]]


def test_component_metadata_is_scoped_to_its_runtime_webspace(tmp_path: Path) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)
    desktop = service.record_aprobation(
        component_type="scenario",
        component_id="infra_ops",
        aprobation=_aprobation(candidate_id="candidate.infra.desktop"),
        webspace_id="desktop",
    )

    assert desktop is not None
    assert service.current_component_metadata(
        "scenario", "infra_ops", placement_webspace_id="desktop"
    )["notice_id"] == desktop["notice_id"]
    assert service.current_component_metadata(
        "scenario", "infra_ops", placement_webspace_id="desktop-dev"
    ) is None
    # The global changelog remains available to callers that intentionally do
    # not select a placement boundary.
    assert service.current_component_metadata("scenario", "infra_ops") is not None


def test_component_update_keeps_failed_publication_in_beta(tmp_path: Path) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)
    aprobation = _aprobation(status="publication_failed", decision="accept")
    aprobation["trial"]["failure"] = {
        "stage": "publication",
        "error": "activation health check failed",
        "ticket_id": "dticket.failure",
    }

    failed = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=aprobation,
    )

    assert failed is not None
    assert failed["stage"] == "beta"
    assert failed["status"] == "active"
    assert failed["review_state"] == "publication_failed"
    assert failed["transition"]["workspace_committed"] is False
    assert failed["transition"]["failure"]["ticket_id"] == "dticket.failure"


def test_component_update_reconciles_builder_session_and_api(tmp_path: Path, monkeypatch) -> None:
    automation_root = tmp_path / "builder" / "automation"
    automation_root.mkdir(parents=True)
    (automation_root / "skill.demo_metrics_skill.json").write_text(
        json.dumps(
            {
                "object_type": "skill",
                "object_id": "demo_metrics_skill",
                "webspace_id": "desktop",
                "links": {"development_ticket_id": "dticket.demo"},
                "completion_readiness": {"aprobation": _aprobation()},
            }
        ),
        encoding="utf-8",
    )
    service = ComponentUpdateService(state_dir=tmp_path)
    reconciler = updates_api._NoticeReconciler()
    monkeypatch.setattr(updates_api, "_NOTICE_RECONCILER", reconciler)
    app = FastAPI()
    app.include_router(updates_api.router, prefix="/api/component-updates")
    app.dependency_overrides[updates_api._get_service] = lambda: service
    client = TestClient(app)
    headers = {"X-AdaOS-Token": "dev-local-token"}

    response = client.get(
        "/api/component-updates",
        params={"component_type": "skill", "component_id": "demo_metrics_skill"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["reconciliation_pending"] is True
    with reconciler.lock:
        worker = reconciler.worker
    if worker is not None:
        worker.join(5)
        assert not worker.is_alive()
    response = client.get(
        "/api/component-updates",
        params={"component_type": "skill", "component_id": "demo_metrics_skill"},
        headers=headers,
    )
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["ticket_ids"] == ["dticket.demo"]
    assert payload["awaiting_decision"] == 1
    assert payload["publishing"] == 0
    assert payload["workspace_committed"] == 0

    notice_id = payload["items"][0]["notice_id"]
    responded = client.post(
        f"/api/component-updates/{notice_id}/respond",
        json={"action": "review_started", "actor": "user:owner", "webspace_id": "desktop"},
        headers=headers,
    )
    assert responded.status_code == 200
    assert responded.json()["notice"]["unread"] is False


def test_local_trial_api_requires_explicit_confirmation(monkeypatch):
    from types import SimpleNamespace
    from adaos.services import personalization_runtime
    calls = []
    service = SimpleNamespace(accept_local_trial=lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True})
    monkeypatch.setattr(personalization_runtime, "current_user_id", lambda: "owner")
    app = FastAPI()
    app.include_router(updates_api.router, prefix="/api/component-updates")
    app.dependency_overrides[updates_api._get_service] = lambda: service
    client = TestClient(app)
    body = {"candidate_id": "candidate", "candidate_digest": "digest", "webspace_id": "desktop"}
    headers = {"X-AdaOS-Token": "dev-local-token"}
    assert client.post('/api/component-updates/test/accept-trial', json=body, headers=headers).status_code == 409
    assert calls == []
    assert client.post('/api/component-updates/test/accept-trial', json={**body, "confirmed": True}, headers=headers).status_code == 200
    assert calls[0][1]["actor"] == "user:owner"


def test_local_trial_api_returns_bounded_acceptance_receipt(monkeypatch):
    from types import SimpleNamespace

    from adaos.services import personalization_runtime

    large_projection = "private-workflow-state" * 100_000
    service = SimpleNamespace(accept_local_trial=lambda *_args, **_kwargs: {
        "ok": True,
        "workflow": {
            "generation": 65,
            "delivery": {"status": "accepted", "internal": large_projection},
            "publication": {"status": "published", "internal": large_projection},
        },
        "installation": {
            "application_id": "gmail_cbs_cleanroom",
            "status": "installed",
            "installed_release_digest": "sha256:release",
            "revision": 2,
            "internal": large_projection,
        },
        "runtime_selection": {
            "application_id": "gmail_cbs_cleanroom",
            "source": "stable_installation",
            "release_digest": "sha256:release",
            "revision": 2,
        },
        "runtime_refresh": {"internal": large_projection},
        "application_verification": {
            "application_id": "gmail_cbs_cleanroom",
            "release_digest": "sha256:release",
            "stage": "publication",
            "report_digest": "sha256:verification",
            "internal": large_projection,
        },
        "publication_verification": {
            "publication_allowed": True,
            "promoted": True,
            "report": {"report_digest": "sha256:publication", "internal": large_projection},
        },
    })
    monkeypatch.setattr(personalization_runtime, "current_user_id", lambda: "owner")
    app = FastAPI()
    app.include_router(updates_api.router, prefix="/api/component-updates")
    app.dependency_overrides[updates_api._get_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/component-updates/test/accept-trial",
        json={
            "candidate_id": "candidate",
            "candidate_digest": "sha256:candidate",
            "webspace_id": "desktop",
            "confirmed": True,
        },
        headers={"X-AdaOS-Token": "dev-local-token"},
    )

    assert response.status_code == 200
    assert len(response.content) < 2_000
    assert "private-workflow-state" not in response.text
    assert response.json() == {
        "schema": "adaos.component_trial_acceptance_receipt.v1",
        "ok": True,
        "workflow": {
            "generation": 65,
            "delivery_status": "accepted",
            "publication_status": "published",
        },
        "installation": {
            "application_id": "gmail_cbs_cleanroom",
            "status": "installed",
            "installed_release_digest": "sha256:release",
            "revision": 2,
        },
        "runtime_selection": {
            "application_id": "gmail_cbs_cleanroom",
            "source": "stable_installation",
            "release_digest": "sha256:release",
            "revision": 2,
        },
        "application_verification": {
            "application_id": "gmail_cbs_cleanroom",
            "release_digest": "sha256:release",
            "stage": "publication",
            "report_digest": "sha256:verification",
        },
        "publication_verification": {
            "publication_allowed": True,
            "promoted": True,
            "report_digest": "sha256:publication",
        },
    }


def test_local_trial_api_reports_direct_immutable_publication_as_published(monkeypatch):
    from adaos.services import personalization_runtime

    service = SimpleNamespace(accept_local_trial=lambda *_args, **_kwargs: {
        "ok": True,
        "workflow": {
            "generation": 338,
            "delivery": {"status": "stale"},
            "publication": {"status": "not_started"},
        },
        "publication": {
            "ok": True,
            "lifecycle_phase": "workspace",
            "candidate_id": "selected-candidate",
            "release_digest": "sha256:selected-release",
            "package_digest": "sha256:selected-package",
        },
        "runtime_selection": {
            "application_id": "web_desktop",
            "source": "stable_installation",
            "release_digest": "sha256:selected-release",
            "revision": 15,
        },
    })
    monkeypatch.setattr(personalization_runtime, "current_user_id", lambda: "owner")
    app = FastAPI()
    app.include_router(updates_api.router, prefix="/api/component-updates")
    app.dependency_overrides[updates_api._get_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/component-updates/test/accept-trial",
        json={
            "candidate_id": "selected-candidate",
            "candidate_digest": "sha256:selected-package",
            "webspace_id": "desktop",
            "confirmed": True,
        },
        headers={"X-AdaOS-Token": "dev-local-token"},
    )

    assert response.status_code == 200
    assert response.json()["workflow"] == {
        "generation": None,
        "delivery_status": "accepted",
        "publication_status": "published",
    }


def test_local_trial_api_reports_runtime_conflict_without_server_error(monkeypatch):
    from types import SimpleNamespace

    from adaos.services.applications.runtime_channel import RuntimeChannelConflict
    from adaos.services import personalization_runtime

    service = SimpleNamespace(
        accept_local_trial=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeChannelConflict("runtime remains fenced")
        )
    )
    monkeypatch.setattr(personalization_runtime, "current_user_id", lambda: "owner")
    app = FastAPI()
    app.include_router(updates_api.router, prefix="/api/component-updates")
    app.dependency_overrides[updates_api._get_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/api/component-updates/test/accept-trial",
        json={
            "candidate_id": "candidate",
            "candidate_digest": "digest",
            "webspace_id": "desktop",
            "confirmed": True,
        },
        headers={"X-AdaOS-Token": "dev-local-token"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "runtime remains fenced"}


def test_local_trial_acceptance_uses_pinned_runtime_selection(tmp_path, monkeypatch):
    from adaos.sdk.builder import applications
    from adaos.services.applications import store as stores

    pinned = SimpleNamespace(
        webspace_id="desktop",
        application_id="applications",
        source="local_trial",
        release_digest="sha256:release-applications",
        runtime_root_ref="trial:applications",
        revision=7,
        to_dict=lambda: {
            "schema": "adaos.application.runtime_selection.v1",
            "webspace_id": "desktop",
            "application_id": "applications",
            "source": "local_trial",
            "release_digest": "sha256:release-applications",
            "runtime_root_ref": "trial:applications",
            "revision": 7,
            "updated_at": "2026-09-23T00:00:00Z",
        },
    )
    # Another Application deliberately has the same accepted Candidate. The
    # review remains deterministic because it owns an exact selection identity.
    colliding = SimpleNamespace(
        webspace_id="desktop",
        application_id="other",
        release_digest="sha256:release-other",
    )
    store = SimpleNamespace(
        get_runtime_selection=lambda webspace_id, application_id: pinned,
        list_runtime_selections=lambda: [pinned, colliding],
        get_release=lambda application_id, release_digest: SimpleNamespace(
            accepted_candidate_id="candidate.applications"
        ),
    )
    monkeypatch.setattr(stores, "ApplicationStore", lambda _root: store)
    calls = []
    monkeypatch.setattr(
        applications,
        "accept_local_trial",
        lambda application_id, **kwargs: calls.append((application_id, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(ComponentUpdateService, "reconcile_local_trials", lambda self, *_args, **_kwargs: 1)
    service = ComponentUpdateService(state_dir=tmp_path)
    notice = service.record_aprobation(
        component_type="scenario",
        component_id="applications",
        aprobation={
            "source_kind": "builder_local_trial",
            "trial": {
                "candidate_id": "candidate.applications",
                "candidate_digest": "sha256:candidate-applications",
                "release_digest": pinned.release_digest,
                "version": "0.1.32",
                "status": "accepted",
            },
            "runtime_selection": pinned.to_dict(),
        },
        webspace_id="desktop",
    )

    result = service.accept_local_trial(
        notice["notice_id"],
        candidate_id="candidate.applications",
        candidate_digest="sha256:candidate-applications",
        webspace_id="desktop",
        actor="user:owner",
    )

    assert result == {"ok": True}
    assert calls[0][0] == "applications"


def test_local_trial_acceptance_uses_pinned_target_from_another_webspace(
    tmp_path, monkeypatch
):
    from adaos.sdk.builder import applications
    from adaos.services.applications import store as stores

    pinned = SimpleNamespace(
        webspace_id="desktop",
        application_id="applications",
        source="local_trial",
        release_digest="sha256:release-applications",
        runtime_root_ref="trial:applications",
        revision=7,
        to_dict=lambda: {
            "webspace_id": "desktop",
            "application_id": "applications",
            "source": "local_trial",
            "release_digest": "sha256:release-applications",
            "runtime_root_ref": "trial:applications",
            "revision": 7,
        },
    )
    reads = []
    store = SimpleNamespace(
        get_runtime_selection=lambda webspace_id, application_id: (
            reads.append((webspace_id, application_id)) or pinned
        ),
        get_release=lambda _application_id, _release_digest: SimpleNamespace(
            accepted_candidate_id="candidate.applications"
        ),
    )
    monkeypatch.setattr(stores, "ApplicationStore", lambda _root: store)
    calls = []
    monkeypatch.setattr(
        applications,
        "accept_local_trial",
        lambda application_id, **kwargs: calls.append((application_id, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(ComponentUpdateService, "reconcile_local_trials", lambda self, *_args, **_kwargs: 1)
    service = ComponentUpdateService(state_dir=tmp_path)
    notice = service.record_aprobation(
        component_type="scenario",
        component_id="applications",
        aprobation={
            "source_kind": "builder_local_trial",
            "trial": {
                "candidate_id": "candidate.applications",
                "candidate_digest": "sha256:candidate-applications",
                "release_digest": pinned.release_digest,
                "version": "0.1.32",
                "status": "accepted",
            },
            "runtime_selection": pinned.to_dict(),
        },
        webspace_id="desktop",
    )

    service.accept_local_trial(
        notice["notice_id"],
        candidate_id="candidate.applications",
        candidate_digest="sha256:candidate-applications",
        webspace_id="secondary",
        actor="user:owner",
    )

    assert reads == [("desktop", "applications")]
    assert calls[0][1]["webspace_id"] == "desktop"


def test_local_trial_acceptance_rejects_changed_pinned_selection(tmp_path, monkeypatch):
    from adaos.services.applications import store as stores

    observed = SimpleNamespace(
        release_digest="sha256:new-release",
        to_dict=lambda: {
            "webspace_id": "desktop",
            "application_id": "applications",
            "source": "local_trial",
            "release_digest": "sha256:new-release",
            "runtime_root_ref": "trial:new",
            "revision": 8,
        },
    )
    monkeypatch.setattr(
        stores,
        "ApplicationStore",
        lambda _root: SimpleNamespace(
            get_runtime_selection=lambda _webspace_id, _application_id: observed
        ),
    )
    service = ComponentUpdateService(state_dir=tmp_path)
    notice = service.record_aprobation(
        component_type="scenario",
        component_id="applications",
        aprobation={
            "source_kind": "builder_local_trial",
            "trial": {
                "candidate_id": "candidate.applications",
                "candidate_digest": "sha256:candidate-applications",
                "release_digest": "sha256:old-release",
                "version": "0.1.32",
                "status": "accepted",
            },
            "runtime_selection": {
                "webspace_id": "desktop",
                "application_id": "applications",
                "source": "local_trial",
                "release_digest": "sha256:old-release",
                "runtime_root_ref": "trial:old",
                "revision": 7,
            },
        },
        webspace_id="desktop",
    )

    try:
        service.accept_local_trial(
            notice["notice_id"],
            candidate_id="candidate.applications",
            candidate_digest="sha256:candidate-applications",
            webspace_id="desktop",
            actor="user:owner",
        )
    except ValueError as exc:
        assert str(exc) == "The reviewed Candidate RuntimeSelection changed; reopen its changelog"
    else:
        raise AssertionError("changed RuntimeSelection was accepted")


def test_legacy_local_trial_acceptance_uses_notice_release_to_disambiguate(
    tmp_path, monkeypatch
):
    from adaos.sdk.builder import applications
    from adaos.services.applications import store as stores

    selected = SimpleNamespace(
        webspace_id="desktop",
        application_id="applications",
        release_digest="sha256:reviewed-release",
    )
    collision = SimpleNamespace(
        webspace_id="desktop",
        application_id="other",
        release_digest="sha256:other-release",
    )
    store = SimpleNamespace(
        list_runtime_selections=lambda: [selected, collision],
        get_release=lambda _application_id, release_digest: SimpleNamespace(
            accepted_candidate_id="legacy-candidate",
            release_digest=release_digest,
        ),
    )
    monkeypatch.setattr(stores, "ApplicationStore", lambda _root: store)
    calls = []
    monkeypatch.setattr(
        applications,
        "accept_local_trial",
        lambda application_id, **kwargs: calls.append((application_id, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(ComponentUpdateService, "reconcile_local_trials", lambda self, *_args, **_kwargs: 1)
    service = ComponentUpdateService(state_dir=tmp_path)
    notice = service.record_aprobation(
        component_type="scenario",
        component_id="applications",
        aprobation={
            "source_kind": "builder_local_trial",
            "trial": {
                "candidate_id": "legacy-candidate",
                "candidate_digest": "sha256:legacy-package",
                "release_digest": "sha256:reviewed-release",
                "version": "0.1.0",
                "status": "trial",
            },
        },
        webspace_id="desktop",
    )

    service.accept_local_trial(
        notice["notice_id"],
        candidate_id="legacy-candidate",
        candidate_digest="sha256:legacy-package",
        webspace_id="desktop",
        actor="user:owner",
    )

    assert calls[0][0] == "applications"
    assert calls[0][1]["webspace_id"] == "desktop"
