from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import builder as builder_api
from adaos.apps.api.auth import require_token
from adaos.services.builder.workbench import BuilderWorkbenchService, dev_webspace_id_for_source, safe_source_webspace_id


def test_dev_webspace_id_uses_safe_source_suffix() -> None:
    assert safe_source_webspace_id("desktop") == "desktop"
    assert safe_source_webspace_id("Prompt IDE / Lab") == "Prompt-IDE-Lab"
    assert dev_webspace_id_for_source("desktop") == "desktop-dev"
    assert dev_webspace_id_for_source("Prompt IDE / Lab") == "Prompt-IDE-Lab-dev"


@pytest.mark.asyncio
async def test_ensure_dev_webspace_creates_deterministic_prompt_ide_binding(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str, bool]] = []

    class _Webspaces:
        def __init__(self) -> None:
            self.items: dict[str, SimpleNamespace] = {}

        def list(self, mode: str = "mixed"):
            return list(self.items.values())

        async def create(self, requested_id: str, title: str, *, scenario_id: str, dev: bool):
            calls.append((requested_id, title, scenario_id, dev))
            info = SimpleNamespace(id=requested_id, title=title, kind="dev", source_mode="dev", home_scenario=scenario_id)
            self.items[requested_id] = info
            return info

        async def set_home_scenario(self, webspace_id: str, scenario_id: str):
            info = self.items[webspace_id]
            info.home_scenario = scenario_id
            return info

    service = BuilderWorkbenchService(state_dir=tmp_path / "state", webspace_service=_Webspaces())

    binding = await service.ensure_dev_webspace("desktop", active_draft_id="draft.shopping")
    assert binding["source_webspace_id"] == "desktop"
    assert binding["dev_webspace_id"] == "desktop-dev"
    assert binding["scenario_id"] == "prompt_engineer_scenario"
    assert binding["runtime_scenario_id"] == "web_desktop"
    assert binding["active_draft_id"] == "draft.shopping"
    assert binding["dialog"]["widget"] == "voice_chat"
    assert binding["dialog"]["dialog_channel_id"] == "builder"
    assert calls == [("desktop-dev", "DEV: desktop", "web_desktop", True)]

    reused = await service.ensure_dev_webspace("desktop", active_draft_id="draft.next")
    assert reused["created"] is False
    assert reused["active_draft_id"] == "draft.next"
    assert calls == [("desktop-dev", "DEV: desktop", "web_desktop", True)]
    assert service.webspace_service.items["desktop-dev"].home_scenario == "web_desktop"

    selected = await service.ensure_dev_webspace("desktop", runtime_scenario_id="demo_scenario")
    assert selected["runtime_scenario_id"] == "demo_scenario"
    assert selected["active_draft_id"] is None
    assert service.webspace_service.items["desktop-dev"].home_scenario == "demo_scenario"

    opened = await service.open_dev_webspace_ready("desktop", base_url="http://localhost:8100")
    assert opened["url"] == "http://localhost:8100/?webspace=desktop-dev"
    assert opened["binding"]["runtime_scenario_id"] == "demo_scenario"
    assert service.webspace_service.items["desktop-dev"].home_scenario == "demo_scenario"


@pytest.mark.asyncio
async def test_ensure_dev_webspace_switches_current_and_reloads_skipped_switch(monkeypatch, tmp_path: Path) -> None:
    class _Webspaces:
        def __init__(self) -> None:
            self.items: dict[str, SimpleNamespace] = {}

        def list(self, mode: str = "mixed"):
            return list(self.items.values())

        async def create(self, requested_id: str, title: str, *, scenario_id: str, dev: bool):
            info = SimpleNamespace(id=requested_id, title=title, kind="dev", source_mode="dev", home_scenario=scenario_id)
            self.items[requested_id] = info
            return info

        async def set_home_scenario(self, webspace_id: str, scenario_id: str):
            info = self.items[webspace_id]
            info.home_scenario = scenario_id
            return info

    import adaos.services.scenario.webspace_runtime as webspace_runtime

    fake_webspaces = _Webspaces()
    switch_calls: list[tuple[str, str, bool | None, bool]] = []
    reload_calls: list[tuple[str, str | None, str]] = []
    switch_results = [
        {"ok": True, "scenario_id": "demo_scenario"},
        {"ok": True, "switch_skipped": True, "skip_reason": "already_current_ready", "scenario_id": "demo_scenario"},
    ]

    async def _switch(webspace_id: str, scenario_id: str, *, set_home=None, wait_for_rebuild=True):
        switch_calls.append((webspace_id, scenario_id, set_home, wait_for_rebuild))
        return switch_results.pop(0)

    async def _reload(webspace_id: str, *, scenario_id=None, action="reload", event_payload=None):
        reload_calls.append((webspace_id, scenario_id, action))
        return {"ok": True, "scenario_id": scenario_id, "action": action}

    monkeypatch.setattr(webspace_runtime, "WebspaceService", lambda: fake_webspaces)
    monkeypatch.setattr(webspace_runtime, "switch_webspace_scenario", _switch)
    monkeypatch.setattr(webspace_runtime, "reload_webspace_from_scenario", _reload)

    service = BuilderWorkbenchService(state_dir=tmp_path / "state")

    first = await service.ensure_dev_webspace("desktop", runtime_scenario_id="demo_scenario")
    assert first["runtime"]["switch"]["scenario_id"] == "demo_scenario"
    assert reload_calls == []

    second = await service.ensure_dev_webspace("desktop", runtime_scenario_id="demo_scenario")
    assert second["runtime"]["switch"]["skip_reason"] == "already_current_ready"
    assert second["runtime"]["reload"]["action"] == "reload"
    assert switch_calls == [
        ("desktop-dev", "demo_scenario", True, True),
        ("desktop-dev", "demo_scenario", True, True),
    ]
    assert reload_calls == [("desktop-dev", "demo_scenario", "reload")]


@pytest.mark.asyncio
async def test_ensure_dev_webspace_reports_yjs_panic_without_raising(monkeypatch, tmp_path: Path) -> None:
    class _YjsPanic(BaseException):
        pass

    class _Webspaces:
        def list(self, mode: str = "mixed"):
            return []

        async def create(self, requested_id: str, title: str, *, scenario_id: str, dev: bool):
            return SimpleNamespace(id=requested_id, title=title, kind="dev", source_mode="dev", home_scenario=scenario_id)

    import adaos.services.scenario.webspace_runtime as webspace_runtime

    async def _switch(*args, **kwargs):
        raise _YjsPanic("Defect: parent points to a block which is not a shared type")

    monkeypatch.setattr(webspace_runtime, "WebspaceService", lambda: _Webspaces())
    monkeypatch.setattr(webspace_runtime, "switch_webspace_scenario", _switch)

    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    result = await service.ensure_dev_webspace("desktop", runtime_scenario_id="demo_scenario")

    assert result["runtime"]["ok"] is False
    assert result["runtime"]["error"] == "dev_runtime_reload_failed"
    assert "_YjsPanic" in result["runtime"]["detail"]


def test_workbench_lists_sets_and_deletes_development_drafts(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    artifact_root = tmp_path / "dev" / "scenarios" / "shopping"
    artifact_root.mkdir(parents=True)
    draft_dir = state_dir / "builder" / "drafts" / "draft.shopping"
    draft_dir.mkdir(parents=True)
    draft = {
        "draft_id": "draft.shopping",
        "status": "draft",
        "artifact": {"kind": "scenario", "id": "shopping", "root": str(artifact_root)},
        "metadata": {"source_idea": "shopping list", "webspace_id": "desktop"},
        "created_at": "2026-06-29T00:00:00Z",
    }
    (draft_dir / "builder.draft.json").write_text(json.dumps(draft), encoding="utf-8")

    service = BuilderWorkbenchService(state_dir=state_dir)
    binding = service.set_active_draft(source_webspace_id="desktop", active_draft_id="draft.shopping")
    assert binding["active_draft_id"] == "draft.shopping"

    listed = service.list_development_skills("desktop")
    assert listed["active_draft_id"] == "draft.shopping"
    assert listed["items"] == [
        {
            "draft_id": "draft.shopping",
            "status": "draft",
            "kind": "scenario",
            "id": "shopping",
            "root": str(artifact_root),
            "source_idea": "shopping list",
            "active": True,
            "updated_at": "2026-06-29T00:00:00Z",
        }
    ]

    deleted = service.delete_development_skill("draft.shopping", "desktop")
    assert deleted["ok"] is True
    assert not draft_dir.exists()
    assert not artifact_root.exists()
    assert service.get_workspace_binding("desktop")["active_draft_id"] is None


def test_builder_api_exposes_workbench_endpoints(tmp_path: Path) -> None:
    class _Webspaces:
        def __init__(self) -> None:
            self.items: dict[str, SimpleNamespace] = {}

        def list(self, mode: str = "mixed"):
            return list(self.items.values())

        async def create(self, requested_id: str, title: str, *, scenario_id: str, dev: bool):
            info = SimpleNamespace(id=requested_id, title=title, kind="dev", source_mode="dev", home_scenario=scenario_id)
            self.items[requested_id] = info
            return info

        async def set_home_scenario(self, webspace_id: str, scenario_id: str):
            info = self.items[webspace_id]
            info.home_scenario = scenario_id
            return info

    service = BuilderWorkbenchService(state_dir=tmp_path / "state", webspace_service=_Webspaces())
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_workbench_service] = lambda: service
    client = TestClient(app)

    response = client.post("/api/builder/workbench/active-draft", json={"webspace_id": "desktop", "draft_id": "draft.one"})
    assert response.status_code == 200
    assert response.json()["binding"]["dev_webspace_id"] == "desktop-dev"

    response = client.get("/api/builder/workbench/binding", params={"webspace_id": "desktop"})
    assert response.status_code == 200
    assert response.json()["binding"]["active_draft_id"] == "draft.one"

    response = client.get(
        "/api/builder/workbench/open",
        params={
            "webspace_id": "desktop",
            "base_url": "http://localhost:8100",
            "runtime_scenario_id": "demo_scenario",
        },
    )
    assert response.status_code == 200
    assert response.json()["url"] == "http://localhost:8100/?webspace=desktop-dev"
    assert response.json()["binding"]["runtime_scenario_id"] == "demo_scenario"
    assert service.webspace_service.items["desktop-dev"].home_scenario == "demo_scenario"

    response = client.get("/api/builder/workbench/dialog-widget", params={"webspace_id": "desktop"})
    assert response.status_code == 200
    assert response.json()["widget"]["widget"] == "voice_chat"
    assert response.json()["widget"]["dialog_channel_id"] == "builder"

    response = client.get("/api/builder/workbench/development-skills", params={"webspace_id": "desktop"})
    assert response.status_code == 200
    assert response.json()["items"] == []
