from __future__ import annotations

from types import SimpleNamespace

from adaos.sdk.builder import artifacts, automation, preview
from adaos.services.builder.automation import BuilderAutomationService


class _AutomationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def start_from_execute(self, **kwargs):
        self.calls.append(("start", kwargs))
        return {"ok": True, "status": "queued", "automation": {"status": "queued"}}

    def submit_turn(self, **kwargs):
        self.calls.append(("submit", kwargs))
        return {"ok": True, "status": "automation_queued"}

    def projection(self, **kwargs):
        self.calls.append(("projection", kwargs))
        return {"ok": True, "automation": {"status": "running", "iteration": 2}}


def test_automation_service_runs_inline_for_one_shot_dev_tool(monkeypatch) -> None:
    service = _AutomationService()
    calls: list[dict] = []

    def from_context(*, background: bool = True):
        calls.append({"background": background})
        return service

    monkeypatch.setattr(BuilderAutomationService, "from_context", from_context)
    monkeypatch.setenv("ADAOS_DEV_TOOL_EXECUTION_MODE", "oneshot")

    assert automation._service() is service
    assert calls == [{"background": False}]


def test_automation_facade_returns_projection_without_exposing_service(monkeypatch) -> None:
    service = _AutomationService()
    monkeypatch.setattr(automation, "_service", lambda: service)

    started = automation.start(
        object_type="scenario",
        object_id="builder",
        implementation_brief="Implement the approved brief",
        webspace_id="desktop-dev",
    )
    submitted = automation.submit("Add tests", object_type="scenario", object_id="builder")
    state = automation.get_state(object_type="scenario", object_id="builder")

    assert started["automation"]["status"] == "queued"
    assert submitted["automation"]["iteration"] == 2
    assert state["automation"]["status"] == "running"
    assert state["session_present"] is True
    assert [name for name, _kwargs in service.calls] == ["start", "submit", "projection", "projection"]


def test_automation_facade_treats_missing_session_as_idle_state(monkeypatch) -> None:
    class _IdleAutomationService:
        def projection(self, **kwargs):
            return {
                "ok": False,
                "error": "automation_session_not_found",
                "automation": {
                    "schema": "adaos.builder.automation_projection.v1",
                    "status": "idle",
                    "phase": "idle",
                    "webspace_id": kwargs["webspace_id"],
                },
            }

    monkeypatch.setattr(automation, "_service", _IdleAutomationService)

    state = automation.get_state(
        object_type="scenario",
        object_id="builder",
        webspace_id="builder-dev",
    )

    assert state["ok"] is True
    assert state["session_present"] is False
    assert state["automation"]["status"] == "idle"
    assert "error" not in state


class _PreviewService:
    def __init__(self) -> None:
        self.active_drafts: list[dict] = []
        self.selections: list[dict] = []
        self.ensure_calls: list[dict] = []
        self.preview_targets: list[dict] = []

    def set_active_draft(self, **kwargs):
        self.active_drafts.append(kwargs)
        return {
            "ok": True,
            "runtime_scenario_id": kwargs["runtime_scenario_id"],
            "preview_webspace_id": "preview-alpha",
        }

    def set_selected_project(self, **kwargs):
        self.selections.append(kwargs)
        return {
            "ok": True,
            "selection": {
                "object_type": kwargs["object_type"],
                "object_id": kwargs["object_id"],
                "title": kwargs["title"],
            },
            "preview_webspace_id": "preview-alpha",
        }

    async def ensure_dev_webspace(self, source_webspace_id, **kwargs):
        self.ensure_calls.append({"source_webspace_id": source_webspace_id, **kwargs})
        return {"ok": True, "source_webspace_id": source_webspace_id, **kwargs}

    def get_workspace_binding(self, source_webspace_id):
        return {"ok": True, "source_webspace_id": source_webspace_id, "preview_webspace_id": "preview-alpha"}

    def set_preview_target(self, **kwargs):
        self.preview_targets.append(kwargs)
        return {
            "ok": True,
            "source_webspace_id": kwargs["source_webspace_id"],
            "preview_webspace_id": "preview-alpha",
            "preview_target": kwargs["target"],
        }

    def resolve_source_webspace_id(self, webspace_id):
        return "dev1-builder" if webspace_id == "dev1-builder" else str(webspace_id or "desktop")

    def open_dev_webspace(self, source_webspace_id, *, base_url=None):
        return {"ok": True, "source_webspace_id": source_webspace_id, "base_url": base_url}


def test_preview_facade_selects_and_ensures_scenario(monkeypatch) -> None:
    service = _PreviewService()
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(preview, "dev_webspace_id", lambda source=None: f"{source or 'desktop'}-dev")

    result = preview.select_project(
        "scenario",
        "builder",
        source_webspace_id="desktop",
        publish_event=False,
    )

    assert result["ok"] is True
    assert result["dev_webspace_id"] == "preview-alpha"
    assert result["ensure"]["runtime_scenario_id"] == "builder"
    assert service.active_drafts == [
        {
            "source_webspace_id": "desktop",
            "active_draft_id": None,
            "runtime_scenario_id": "builder",
            "persist_projection": False,
        }
    ]
    assert service.selections[0]["object_type"] == "scenario"
    assert service.selections[0]["object_id"] == "builder"


def test_preview_facade_uses_explicit_service_topology(monkeypatch) -> None:
    service = _PreviewService()
    monkeypatch.setattr(preview, "_service", lambda: service)

    binding = preview.get_binding("dev1-builder")
    opened = preview.open_workspace("dev1-builder")

    assert preview.canonical_source_webspace_id("dev1-builder") == "dev1-builder"
    assert binding["source_webspace_id"] == "dev1-builder"
    assert opened["source_webspace_id"] == "dev1-builder"


def test_preview_facade_persists_skill_context_without_changing_preview(monkeypatch) -> None:
    service = _PreviewService()
    monkeypatch.setattr(preview, "_service", lambda: service)

    result = preview.select_project("skill", "builder_skill", publish_event=False)

    assert result["selected"] is True
    assert result["object_type"] == "skill"
    assert result["object_id"] == "builder_skill"
    assert service.active_drafts == []
    assert service.ensure_calls == []
    assert service.selections[0]["object_type"] == "skill"
    assert service.selections[0]["object_id"] == "builder_skill"


def test_preview_facade_never_blocks_event_driven_selection_on_rebuild(monkeypatch) -> None:
    service = _PreviewService()
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        "adaos.sdk.data.events.publish",
        lambda topic, payload, **_kwargs: events.append((topic, payload)),
    )

    result = preview.select_project(
        "scenario",
        "builder",
        source_webspace_id="desktop",
        wait_for_rebuild=True,
        publish_event=True,
    )

    assert result["ensure"]["scheduled"] is True
    assert service.ensure_calls == []
    desired = next(payload for topic, payload in events if topic == "builder.preview.desired")
    assert desired["reconciled"] is False
    assert desired["wait_for_rebuild"] is False


def test_preview_target_materializes_exact_prototype_without_changing_workflow(monkeypatch) -> None:
    service = _PreviewService()
    materializations: list[dict] = []
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        "adaos.services.builder.workflow.BuilderWorkflowService.from_context",
        lambda: SimpleNamespace(
            describe=lambda kind, project_id: {
                "active_phase": "automation",
                "prototype": {"head_revision": "003"},
                "automation": {"head_task_id": "task.current", "result_version": "0.2.0"},
                "publication": {"current_version": "0.2.1"},
                "capabilities": {
                    "can_preview_prototype": True,
                    "can_preview_automation": True,
                    "can_preview_publication": True,
                },
            }
        ),
    )
    monkeypatch.setattr(
        preview,
        "materialize_revision",
        lambda **kwargs: materializations.append(dict(kwargs)) or {"ok": True},
    )

    result = preview.select_target(
        "scenario",
        "recipes",
        stage="prototype",
        revision="002",
        source_webspace_id="desktop",
    )

    assert result["target"]["stage"] == "prototype"
    assert result["target"]["revision"] == "002"
    assert result["target"]["label"] == "proto: recipes · UI 002"
    assert materializations[0]["preview_stage"] == "prototype"
    assert materializations[0]["revision"] == "002"
    assert service.preview_targets[0]["target"]["follow_active"] is False


def test_preview_target_materializes_current_content_when_a_scenario_has_no_ui_revision(monkeypatch) -> None:
    service = _PreviewService()
    materializations: list[dict] = []
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        "adaos.services.builder.workflow.BuilderWorkflowService.from_context",
        lambda: SimpleNamespace(
            describe=lambda kind, project_id: {
                "active_phase": "prototype",
                "prototype": {"head_revision": None},
                "automation": {},
                "publication": {},
                "capabilities": {
                    "can_preview_prototype": True,
                    "can_preview_automation": False,
                    "can_preview_publication": False,
                },
            }
        ),
    )
    monkeypatch.setattr(
        preview,
        "materialize_revision",
        lambda **kwargs: materializations.append(dict(kwargs)) or {"ok": True},
    )

    result = preview.select_target(
        "scenario",
        "recipes",
        stage="prototype",
        source_webspace_id="desktop",
    )

    assert result["target"]["revision"] is None
    assert result["target"]["label"] == "proto: recipes · current"
    assert materializations[0]["revision"] is None


def test_preview_follow_active_resolves_current_automation(monkeypatch) -> None:
    service = _PreviewService()
    materializations: list[dict] = []
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        "adaos.services.builder.workflow.BuilderWorkflowService.from_context",
        lambda: SimpleNamespace(
            describe=lambda kind, project_id: {
                "active_phase": "automation",
                "prototype": {"head_revision": "003"},
                "automation": {
                    "head_task_id": "task.adaptation",
                    "snapshot_task_id": "task.current",
                    "result_version": "0.2.0",
                },
                "publication": {"current_version": None},
                "capabilities": {
                    "can_preview_prototype": True,
                    "can_preview_automation": True,
                    "can_preview_publication": False,
                },
            }
        ),
    )
    monkeypatch.setattr(
        preview,
        "materialize_revision",
        lambda **kwargs: materializations.append(dict(kwargs)) or {"ok": True},
    )

    result = preview.select_target(
        "scenario",
        "recipes",
        stage="prototype",
        source_webspace_id="desktop",
        follow_active=True,
    )

    assert result["target"]["stage"] == "automation"
    assert result["target"]["revision"] == "task.current"
    assert result["target"]["label"] == "active: recipes · 0.2.0"
    assert materializations[0]["preview_stage"] == "automation"


def test_preview_target_materializes_only_current_automation_with_active_prefix(monkeypatch) -> None:
    service = _PreviewService()
    materializations: list[dict] = []
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        "adaos.services.builder.workflow.BuilderWorkflowService.from_context",
        lambda: SimpleNamespace(
            describe=lambda kind, project_id: {
                "active_phase": "automation",
                "prototype": {"head_revision": "003"},
                "automation": {
                    "head_task_id": "task.current",
                    "snapshot_task_id": "task.snapshot",
                    "result_version": "0.2.0",
                },
                "publication": {},
                "capabilities": {
                    "can_preview_prototype": True,
                    "can_preview_automation": True,
                    "can_preview_publication": False,
                },
            }
        ),
    )
    monkeypatch.setattr(
        preview,
        "materialize_revision",
        lambda **kwargs: materializations.append(dict(kwargs)) or {"ok": True},
    )

    result = preview.select_target(
        "scenario",
        "recipes",
        stage="automation",
        revision="task.snapshot",
        source_webspace_id="desktop",
    )

    assert result["target"]["label"] == "active: recipes · 0.2.0"
    assert result["target"]["revision"] == "task.snapshot"
    assert materializations[0]["preview_stage"] == "automation"


def test_preview_target_materializes_only_current_publication_with_public_prefix(monkeypatch) -> None:
    service = _PreviewService()
    materializations: list[dict] = []
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        "adaos.services.builder.workflow.BuilderWorkflowService.from_context",
        lambda: SimpleNamespace(
            describe=lambda kind, project_id: {
                "active_phase": "automation",
                "prototype": {"head_revision": "003"},
                "automation": {"result_version": "0.2.0"},
                "publication": {"current_version": "0.2.1"},
                "capabilities": {
                    "can_preview_prototype": True,
                    "can_preview_automation": True,
                    "can_preview_publication": True,
                },
            }
        ),
    )
    monkeypatch.setattr(
        preview,
        "materialize_revision",
        lambda **kwargs: materializations.append(dict(kwargs)) or {"ok": True},
    )

    result = preview.select_target(
        "scenario",
        "recipes",
        stage="publication",
        revision="0.2.1",
        source_webspace_id="desktop",
    )

    assert result["target"]["label"] == "public: recipes · 0.2.1"
    assert result["target"]["revision"] == "0.2.1"
    assert materializations[0]["preview_stage"] == "publication"


def test_artifact_checkpoint_forwards_public_metadata(monkeypatch) -> None:
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "commit": "abc123"}

    monkeypatch.setattr(
        "adaos.services.builder.workspace.BuilderWorkspaceService",
        _Workspace,
    )

    result = artifacts.checkpoint(
        kind="scenario",
        artifact_id="builder",
        message="Builder revision 001",
        metadata={"change_id": "change-1"},
    )

    assert result["commit"] == "abc123"
    assert calls[0]["metadata"] == {"change_id": "change-1"}
