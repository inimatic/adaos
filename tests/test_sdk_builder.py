from __future__ import annotations

from types import SimpleNamespace

from adaos.sdk.builder import artifacts, automation, preview


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
        self.selected: list[dict] = []

    def set_active_draft(self, **kwargs):
        self.selected.append(kwargs)
        return {"ok": True, "runtime_scenario_id": kwargs["runtime_scenario_id"]}

    async def ensure_dev_webspace(self, source_webspace_id, **kwargs):
        return {"ok": True, "source_webspace_id": source_webspace_id, **kwargs}

    def get_workspace_binding(self, source_webspace_id):
        return {"ok": True, "source_webspace_id": source_webspace_id}

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
    assert result["dev_webspace_id"] == "desktop-dev"
    assert result["ensure"]["runtime_scenario_id"] == "builder"
    assert service.selected == [
        {
            "source_webspace_id": "desktop",
            "active_draft_id": None,
            "runtime_scenario_id": "builder",
            "persist_projection": True,
        }
    ]


def test_preview_facade_canonicalizes_current_dev_webspace(monkeypatch) -> None:
    service = _PreviewService()
    monkeypatch.setattr(preview, "_service", lambda: service)

    binding = preview.get_binding("dev1-dev")
    opened = preview.open_workspace("dev1-dev")

    assert preview.canonical_source_webspace_id("dev1-dev") == "dev1"
    assert binding["source_webspace_id"] == "dev1"
    assert opened["source_webspace_id"] == "dev1"


def test_preview_facade_does_not_bind_skill_project(monkeypatch) -> None:
    monkeypatch.setattr(preview, "_service", lambda: (_ for _ in ()).throw(AssertionError("service must not load")))

    result = preview.select_project("skill", "builder_skill", publish_event=False)

    assert result == {
        "ok": True,
        "selected": False,
        "object_type": "skill",
        "object_id": "builder_skill",
        "source_webspace_id": "desktop",
    }


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
