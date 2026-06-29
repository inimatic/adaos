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

    response = client.get("/api/builder/workbench/open", params={"webspace_id": "desktop", "base_url": "http://localhost:8100"})
    assert response.status_code == 200
    assert response.json()["url"] == "http://localhost:8100/?webspace=desktop-dev"

    response = client.get("/api/builder/workbench/dialog-widget", params={"webspace_id": "desktop"})
    assert response.status_code == 200
    assert response.json()["widget"]["widget"] == "voice_chat"
    assert response.json()["widget"]["dialog_channel_id"] == "builder"

    response = client.get("/api/builder/workbench/development-skills", params={"webspace_id": "desktop"})
    assert response.status_code == 200
    assert response.json()["items"] == []
