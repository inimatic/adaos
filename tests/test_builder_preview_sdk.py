from __future__ import annotations

import pytest
from types import SimpleNamespace

from adaos.sdk.builder import preview
from adaos.services.builder.workbench import _preview_state_projection


def test_explicit_open_recreates_missing_preview_with_exact_revision(monkeypatch):
    from adaos.services.workspaces import index

    service = _Workbench(follow_active=False)
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(index, "get_workspace", lambda _: None)
    calls = []
    monkeypatch.setattr(preview, "select_target", lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True})
    result = preview.ensure_selected_target("desktop")
    assert result["recreated"] is True
    assert calls == [(("scenario", "recipes"), {
        "stage": "prototype", "revision": "003", "source_webspace_id": "desktop", "via_owner": True, "follow_active": False,
    })]


@pytest.mark.parametrize("stage,revision", [("prototype", "071"), ("automation", "task.fixed")])
@pytest.mark.parametrize("kind", ["project", "scenario"])
@pytest.mark.parametrize("materialized", [{"ok": True}, {"ok": False}, {"ok": True, "accepted": False}])
def test_revision_selection_persists_actual_target_and_keeps_project_identity(monkeypatch, kind, stage, revision, materialized):
    from adaos.services.builder.workflow import BuilderWorkflowService
    from adaos.sdk.developer import compositions

    service = _Workbench()
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(compositions, "get", lambda _: {"components": {"owned": [
        {"ref": "scenario:screen", "role": "primary"}]}})
    workflows = []
    def describe(*args):
        workflows.append(args)
        return {"capabilities": {"can_preview_prototype": True, "can_preview_automation": True},
                "prototype": {"head_revision": "072"}, "automation": {"snapshot_task_id": "task.fixed"}}
    monkeypatch.setattr(BuilderWorkflowService, "from_context", lambda: SimpleNamespace(describe=describe))
    selections, renders, events = [], [], []
    def select(*args, **kwargs):
        selections.append((args, kwargs))
        return {"ok": True, "preview_webspace_id": "desktop-dev", "binding": {
            "selection": {"object_type": kind, "object_id": "app", "title": "Example"}}}
    monkeypatch.setattr(preview, "select_project", select)
    monkeypatch.setattr(preview, "materialize_revision", lambda **kw: renders.append(kw) or materialized)
    monkeypatch.setattr("adaos.sdk.data.events.publish", lambda *args, **kwargs: events.append((args, kwargs)))
    result = preview.select_target(kind, "app", stage=stage, revision=revision)
    if materialized.get("ok") is False or materialized.get("accepted") is False:
        assert result["ok"] is False
        assert service.set_calls == []
        assert service.target["revision"] == "003"
        return
    assert result["ok"]
    assert workflows == [("scenario", "screen" if kind == "project" else "app")]
    assert selections[0][0] == (kind, "app")
    assert selections[0][1]["publish_event"] is False
    assert len(renders) == 1
    assert renders[0]["revision"] == revision
    assert renders[0]["scenario_id"] == workflows[0][1]
    assert service.target["object_type"] == kind
    assert service.target["object_id"] == "app"
    assert service.target["stage"] == stage
    assert service.target["revision"] == revision
    assert len(events) == 1
    assert events[0][0][1]["object_type"] == kind


def test_project_preview_recreation_keeps_aggregate_identity_and_pinned_revision(monkeypatch):
    from adaos.services.workspaces import index

    service = _Workbench(follow_active=False)
    service.target.update(object_type="project", object_id="builder", scenario_id="builder", revision="071")
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(index, "get_workspace", lambda _: None)
    calls = []
    monkeypatch.setattr(preview, "select_target", lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True})
    assert preview.ensure_selected_target("desktop")["recreated"]
    assert calls == [(("project", "builder"), {"stage": "prototype", "revision": "071",
        "source_webspace_id": "desktop", "via_owner": True, "follow_active": False})]


class _Workbench:
    def __init__(self, *, follow_active: bool = True) -> None:
        self.target = {
            "schema": "adaos.builder.preview_target.v1",
            "object_type": "scenario",
            "object_id": "recipes",
            "stage": "prototype",
            "revision": "003",
            "label": "proto: recipes · UI 003",
            "follow_active": follow_active,
        }
        self.selection = {"object_id": "recipes", "title": "Old title"}
        self.set_calls: list[dict[str, object]] = []
        self.inspector_calls: list[dict[str, object]] = []

    def resolve_source_webspace_id(self, value):
        return value or "desktop"

    def get_workspace_binding(self, _source):
        return {
            "preview_target": dict(self.target),
            "selection": dict(self.selection),
            "preview_webspace_id": "dev1-dev",
        }

    def context_inspector(self, source, *, run_ref=None, limit=20):
        self.inspector_calls.append({"source": source, "run_ref": run_ref, "limit": limit})
        return {
            "schema": "adaos.builder.context_inspector.v1",
            "source_webspace_id": source,
            "development_feedback": {"items": [{"feedback_id": "devfeedback.1"}]},
        }

    def set_preview_target(self, *, source_webspace_id, target):
        self.set_calls.append({"source_webspace_id": source_webspace_id, "target": dict(target)})
        self.target = dict(target)
        return {"preview_target": dict(self.target), "selection": dict(self.selection)}

    def set_selected_project(
        self,
        *,
        source_webspace_id,
        object_type,
        object_id,
        title,
        description,
        persist_projection,
    ):
        self.selection = {
            "object_type": object_type,
            "object_id": object_id,
            "title": title,
            "description": description,
        }
        return {"selection": dict(self.selection)}


def test_set_selected_project_uses_public_sdk_boundary(monkeypatch) -> None:
    service = _Workbench()
    monkeypatch.setattr(preview, "_service", lambda: service)

    result = preview.set_selected_project(
        "scenario",
        "applications",
        source_webspace_id="desktop-dev",
        title="Applications",
        description="Application catalog",
    )

    assert result["selection"] == {
        "object_type": "scenario",
        "object_id": "applications",
        "title": "Applications",
        "description": "Application catalog",
    }
    assert service.selection == result["selection"]


def test_materialize_revision_via_owner_uses_active_local_control(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"ok": True, "delivery": "owner_control_api"}

    class _Session:
        trust_env = True

        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs, "trust_env": self.trust_env})
            return _Response()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr("requests.Session", _Session)
    monkeypatch.setattr(
        "adaos.apps.cli.active_control.resolve_control_base_url",
        lambda **_kwargs: "http://127.0.0.1:8778",
    )
    monkeypatch.setattr(
        "adaos.apps.cli.active_control.resolve_control_token",
        lambda **_kwargs: "local-token",
    )

    result = preview.materialize_revision_via_owner(
        "desktop-dev",
        scenario_id="applications",
        revision="027",
        preview_stage="prototype",
        preview_label="Preview 027",
        source_fingerprint="fp-027",
        event_payload={
            "source_webspace_id": "desktop",
            "draft_id": "draft.applications",
            "_meta": {"cmd_id": "builder.ui.applications.027"},
        },
    )

    assert result == {"ok": True, "delivery": "owner_control_api"}
    assert captured["url"] == (
        "http://127.0.0.1:8778/api/node/yjs/webspaces/desktop-dev/builder-materialize"
    )
    assert captured["headers"] == {
        "X-AdaOS-Token": "local-token",
        "Accept": "application/json",
    }
    assert captured["json"]["scenario_id"] == "applications"
    assert captured["json"]["revision"] == "027"
    assert captured["json"]["preview_stage"] == "prototype"
    assert captured["json"]["preview_label"] == "Preview 027"
    assert captured["json"]["request_id"] == "builder.ui.applications.027"
    assert captured["trust_env"] is False
    assert captured["closed"] is True


def test_ensure_dev_webspace_via_owner_uses_active_local_control(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "ok": True,
                "accepted": True,
                "webspace_id": "preview-applications",
                "source_mode": "dev",
            }

    class _Session:
        trust_env = True

        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs, "trust_env": self.trust_env})
            return _Response()

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr("requests.Session", _Session)
    monkeypatch.setattr(
        "adaos.apps.cli.active_control.resolve_control_base_url",
        lambda **_kwargs: "http://127.0.0.1:8778",
    )
    monkeypatch.setattr(
        "adaos.apps.cli.active_control.resolve_control_token",
        lambda **_kwargs: "local-token",
    )

    result = preview.ensure_dev_webspace_via_owner(
        "applications",
        requested_id="preview-applications",
        title="DEV: Builder",
    )

    assert result["source_mode"] == "dev"
    assert captured["url"] == "http://127.0.0.1:8778/api/node/yjs/dev-webspaces/ensure"
    assert captured["json"] == {
        "scenario_id": "applications",
        "requested_id": "preview-applications",
        "title": "DEV: Builder",
    }
    assert captured["trust_env"] is False
    assert captured["closed"] is True


def test_refresh_follow_active_target_updates_metadata_without_materializing(monkeypatch) -> None:
    service = _Workbench()
    monkeypatch.setattr(preview, "_service", lambda: service)

    result = preview.refresh_follow_active_target(
        "scenario",
        "recipes",
        revision="005",
        source_webspace_id="desktop",
        title="Кулинарные рецепты",
        description="Каталог рецептов",
    )

    assert result["ok"] is True
    assert result["materialization"] == "deferred"
    assert result["target"]["revision"] == "005"
    assert result["target"]["label"] == "proto: recipes · UI 005"
    assert result["binding"]["selection"]["title"] == "Кулинарные рецепты"
    assert result["selection"]["description"] == "Каталог рецептов"
    assert len(service.set_calls) == 1


def test_context_inspector_is_a_bounded_public_sdk_projection(monkeypatch) -> None:
    service = _Workbench()
    monkeypatch.setattr(preview, "_service", lambda: service)

    result = preview.context_inspector("desktop-dev", run_ref="run:1", limit=1000)

    assert result["development_feedback"]["items"][0]["feedback_id"] == "devfeedback.1"
    assert service.inspector_calls == [
        {"source": "desktop-dev", "run_ref": "run:1", "limit": 100}
    ]


def test_refresh_follow_active_target_preserves_explicit_snapshot(monkeypatch) -> None:
    service = _Workbench(follow_active=False)
    monkeypatch.setattr(preview, "_service", lambda: service)

    result = preview.refresh_follow_active_target(
        "scenario",
        "recipes",
        revision="005",
        source_webspace_id="desktop",
    )

    assert result["skipped"] == "preview_target_not_following_active"
    assert result["binding"]["preview_target"]["revision"] == "003"
    assert service.set_calls == []


def test_follow_active_scenario_update_preserves_aggregate_identity(monkeypatch):
    service = _Workbench()
    service.target.update(object_type="project", object_id="recipe-app", scenario_id="recipes")
    monkeypatch.setattr(preview, "_service", lambda: service)
    result = preview.refresh_follow_active_target("scenario", "recipes", revision="006")
    assert result["target"]["object_type"] == "project"
    assert result["target"]["object_id"] == "recipe-app"
    assert result["target"]["scenario_id"] == "recipes"
    assert result["selection"]["object_id"] == "recipe-app"
    assert result["target"]["revision"] == "006"
    unrelated = preview.refresh_follow_active_target("scenario", "other", revision="007")
    assert unrelated["skipped"] == "preview_target_project_mismatch"


def test_project_navigation_uses_target_primary_scenario(monkeypatch):
    service = _Workbench()
    service.target.update(object_type="project", object_id="recipe-app", scenario_id="recipes")
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr("adaos.sdk.navigation.runtime_scope", lambda: {"zone": "lo", "subnet_id": "sn_test"})
    result = preview.navigation_link("desktop", base_url="http://127.0.0.1:8100")
    assert result["destination"]["expected_scenario_id"] == "recipes"
    assert result["destination"]["expected_revision"] == "003"


def test_automation_matches_project_target_only_through_primary_scenario():
    from adaos.services.builder.automation import BuilderAutomationService

    target = {"object_type": "project", "object_id": "recipe-app", "scenario_id": "recipes"}
    match = BuilderAutomationService._preview_target_matches_project
    assert match(target, object_type="scenario", object_id="recipes")
    assert match(target, object_type="project", object_id="recipe-app")
    assert not match(target, object_type="skill", object_id="recipes")
    assert not match(target, object_type="scenario", object_id="other")


def test_navigation_link_uses_shared_sdk_and_preserves_preview_expectations(monkeypatch) -> None:
    service = _Workbench()
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        "adaos.sdk.navigation.runtime_scope",
        lambda: {"zone": "ru", "subnet_id": "sn_6acf0c01"},
    )

    result = preview.navigation_link("dev1", base_url="https://inimatic.com")

    assert result["url"] == (
        "https://inimatic.com/?intent=webspace.open&zone=ru&subnet_id=sn_6acf0c01"
        "&webspace_id=dev1-dev&space_kind=development&expected_scenario_id=recipes"
        "&expected_revision=003&preview_stage=prototype"
    )
    assert result["destination"] == {
        "schema": "adaos.navigation.destination.v1",
        "intent": "webspace.open",
        "zone": "ru",
        "subnet_id": "sn_6acf0c01",
        "webspace_id": "dev1-dev",
        "space_kind": "development",
        "expected_scenario_id": "recipes",
        "expected_revision": "003",
        "preview_stage": "prototype",
    }


def test_skill_preview_state_keeps_component_presentation_context() -> None:
    projected = _preview_state_projection(
        {
            "selected_component_ref": "skill:tlp_research",
            "selected_component_id": "tlp_research",
            "presentation": {"presentation": "scenario:research_workbench"},
            "bindings": {"direction_ref": "skill:tlp_research"},
            "unrelated": "must-not-leak",
        }
    )

    assert projected == {
        "selected_component_ref": "skill:tlp_research",
        "selected_component_id": "tlp_research",
        "presentation": {"presentation": "scenario:research_workbench"},
        "bindings": {"direction_ref": "skill:tlp_research"},
    }


@pytest.mark.parametrize("stage", ["trial", "publication"])
@pytest.mark.parametrize("via_owner", [False, True])
def test_select_target_rejects_delivery_without_creating_preview(monkeypatch, stage, via_owner):
    monkeypatch.setattr(preview, "_service", lambda: pytest.fail("Preview service must not be consulted"))
    with pytest.raises(ValueError, match="DEV-only"):
        preview.select_target("scenario", "example", stage=stage, via_owner=via_owner)
