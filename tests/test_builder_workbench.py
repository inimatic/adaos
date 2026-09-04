from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import builder as builder_api
from adaos.apps.api.auth import require_token
from adaos.services.builder.workbench import (
    BuilderWorkbenchService,
    dev_webspace_id_for_source,
    safe_source_webspace_id,
    source_webspace_id_for,
)
from adaos.services.builder.repair import BuilderRepairService
from adaos.services.context_control import ContextControlService
from adaos.services.development_feedback import DevelopmentFeedbackService
from adaos.services.development_tickets import DevelopmentTicketService


def test_preview_webspace_id_is_opaque_and_source_ids_are_not_parsed() -> None:
    assert safe_source_webspace_id("desktop") == "desktop"
    assert safe_source_webspace_id("Prompt IDE / Lab") == "Prompt-IDE-Lab"
    preview_id = dev_webspace_id_for_source("desktop")
    assert preview_id.startswith("preview-")
    assert preview_id != "desktop-dev"
    assert dev_webspace_id_for_source("desktop") == preview_id
    assert source_webspace_id_for("unrelated-dev") == "unrelated-dev"


@pytest.mark.asyncio
async def test_ensure_dev_webspace_creates_explicit_prompt_ide_binding(tmp_path: Path) -> None:
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
    preview_id = binding["preview_webspace_id"]
    assert preview_id.startswith("preview-")
    assert binding["dev_webspace_id"] == preview_id
    assert binding["relationship"]["target_webspace_id"] == preview_id
    assert binding["scenario_id"] == "prompt_engineer_scenario"
    assert binding["runtime_scenario_id"] == "web_desktop"
    assert binding["active_draft_id"] == "draft.shopping"
    assert binding["dialog"]["widget"] == "voice_chat"
    assert binding["dialog"]["dialog_channel_id"] == "builder"
    assert binding["dialog"]["thread_id"] == "thread.builder.desktop.draft.shopping"
    assert binding["dialog"]["topic_id"] == "builder:desktop:draft.shopping"
    assert binding["dialog"]["meta"]["thread_id"] == "thread.builder.desktop.draft.shopping"
    assert binding["dialog"]["meta"]["builder_topic"]["active_draft_id"] == "draft.shopping"
    assert calls == [(preview_id, "DEV: desktop", "web_desktop", True)]

    reused = await service.ensure_dev_webspace("desktop", active_draft_id="draft.next")
    assert reused["created"] is False
    assert reused["active_draft_id"] == "draft.next"
    assert reused["dialog"]["thread_id"] == "thread.builder.desktop.draft.next"
    assert calls == [(preview_id, "DEV: desktop", "web_desktop", True)]
    assert service.webspace_service.items[preview_id].home_scenario == "web_desktop"

    selected = await service.ensure_dev_webspace("desktop", runtime_scenario_id="demo_scenario")
    assert selected["runtime_scenario_id"] == "demo_scenario"
    assert selected["active_draft_id"] is None
    assert selected["dialog"]["thread_id"] == "prompt-project:scenario:demo_scenario"
    assert selected["dialog"]["topic_id"] == "prompt-project:scenario:demo_scenario"
    assert selected["dialog"]["meta"]["conversation_topic_id"] == "prompt-project:scenario:demo_scenario"
    assert selected["preview_webspace_id"] == preview_id

    opened = await service.open_dev_webspace_ready("desktop", base_url="http://localhost:8100")
    assert opened["url"] == f"http://localhost:8100/?webspace={preview_id}"
    assert opened["binding"]["runtime_scenario_id"] == "demo_scenario"
    assert service.webspace_service.items[preview_id].home_scenario == "web_desktop"


@pytest.mark.asyncio
async def test_ensure_dev_webspace_switches_current_without_reloading_ready_skip(monkeypatch, tmp_path: Path) -> None:
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

    async def _switch(webspace_id: str, scenario_id: str, *, set_home=None, wait_for_rebuild=True, **_kwargs):
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
    preview_id = first["preview_webspace_id"]
    assert first["runtime"]["switch"]["scenario_id"] == "demo_scenario"
    assert reload_calls == []

    second = await service.ensure_dev_webspace("desktop", runtime_scenario_id="demo_scenario")
    assert second["runtime"]["coalesced"] is True
    assert second["runtime"]["switch"]["scenario_id"] == "demo_scenario"
    assert "reload" not in second["runtime"]
    assert switch_calls == [
        (preview_id, "demo_scenario", True, True),
    ]
    assert reload_calls == []


@pytest.mark.asyncio
async def test_ensure_dev_webspace_schedules_reconcile_but_waits_inside_single_worker(monkeypatch, tmp_path: Path) -> None:
    class _Webspaces:
        def list(self, mode: str = "mixed"):
            return []

        async def create(self, requested_id: str, title: str, *, scenario_id: str, dev: bool):
            return SimpleNamespace(id=requested_id, title=title, kind="dev", source_mode="dev", home_scenario=scenario_id)

    import adaos.services.scenario.webspace_runtime as webspace_runtime

    switch_calls: list[tuple[str, str, bool | None, bool]] = []

    async def _switch(webspace_id: str, scenario_id: str, *, set_home=None, wait_for_rebuild=True, **_kwargs):
        switch_calls.append((webspace_id, scenario_id, set_home, wait_for_rebuild))
        return {"ok": True, "scenario_id": scenario_id, "background_rebuild": not wait_for_rebuild}

    monkeypatch.setattr(webspace_runtime, "WebspaceService", lambda: _Webspaces())
    monkeypatch.setattr(webspace_runtime, "switch_webspace_scenario", _switch)

    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    result = await service.ensure_dev_webspace(
        "desktop",
        runtime_scenario_id="demo_scenario",
        wait_for_rebuild=False,
    )

    await asyncio.sleep(0)
    preview_id = result["preview_webspace_id"]
    assert service.reconciler.describe("desktop")["status"] in {"ready", "running"}
    assert switch_calls == [(preview_id, "demo_scenario", True, True)]


@pytest.mark.asyncio
async def test_rapid_preview_switches_keep_one_materialization_in_flight(monkeypatch, tmp_path: Path) -> None:
    class _Webspaces:
        def __init__(self) -> None:
            self.items: dict[str, SimpleNamespace] = {}

        def list(self, mode: str = "mixed"):
            return list(self.items.values())

        async def create(self, requested_id: str, title: str, *, scenario_id: str, dev: bool):
            item = SimpleNamespace(
                id=requested_id,
                title=title,
                kind="dev",
                source_mode="dev",
                home_scenario=scenario_id,
            )
            self.items[requested_id] = item
            return item

    import adaos.services.scenario.webspace_runtime as webspace_runtime

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls: list[str] = []
    active = 0
    max_active = 0

    async def _switch(_webspace_id: str, scenario_id: str, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append(scenario_id)
        try:
            if scenario_id == "first":
                first_started.set()
                await release_first.wait()
            return {"ok": True, "accepted": True, "scenario_id": scenario_id}
        finally:
            active -= 1

    monkeypatch.setattr(webspace_runtime, "WebspaceService", _Webspaces)
    monkeypatch.setattr(webspace_runtime, "switch_webspace_scenario", _switch)

    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    await service.ensure_dev_webspace("desktop", runtime_scenario_id="first", wait_for_rebuild=False)
    await first_started.wait()
    await service.ensure_dev_webspace("desktop", runtime_scenario_id="second", wait_for_rebuild=False)

    assert calls == ["first"]
    assert max_active == 1
    release_first.set()

    for _index in range(20):
        if service.reconciler.describe("desktop").get("status") == "ready":
            break
        await asyncio.sleep(0)

    state = service.reconciler.describe("desktop")
    assert calls == ["first", "second"]
    assert max_active == 1
    assert state["observed_scenario"] == "second"
    assert state["history"][-1]["desired_scenario"] == "first"
    assert state["history"][-1]["status"] == "superseded"
    assert state["history"][-1]["superseded_by_generation"] == 2


def test_preview_observation_exposes_drift_without_changing_desired_generation(tmp_path: Path) -> None:
    from adaos.services.builder.preview_reconciler import BuilderPreviewReconciler

    reconciler = BuilderPreviewReconciler(state_dir=tmp_path / "state")
    requested, _ = reconciler.request(
        source_webspace_id="desktop",
        preview_webspace_id="preview-a",
        project_kind="scenario",
        project_id="shopping",
        desired_scenario="shopping",
    )

    drifted = reconciler.observe(
        source_webspace_id="desktop",
        preview_webspace_id="preview-a",
        observed_scenario="builder",
        observed_version="0.2.1",
        reason="manual_runtime_change",
    )

    assert drifted["generation"] == requested["generation"]
    assert drifted["desired_scenario"] == "shopping"
    assert drifted["observed_scenario"] == "builder"
    assert drifted["status"] == "drifted"
    assert drifted["drift"]["reconcile_required"] is True

    restored = reconciler.observe(
        source_webspace_id="desktop",
        preview_webspace_id="preview-a",
        observed_scenario="shopping",
    )
    assert restored["status"] == "ready"
    assert restored["drift"] is None


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
    assert "_YjsPanic" in result["runtime"]["error"]


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
    archive_root = Path(deleted["archive_root"])
    assert (archive_root / "archive.json").exists()
    assert (archive_root / "draft" / "builder.draft.json").exists()
    assert (archive_root / "artifact").is_dir()
    assert service.get_workspace_binding("desktop")["active_draft_id"] is None


def test_workbench_delete_rejects_missing_artifact_root_without_touching_repo(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    draft_dir = state_dir / "builder" / "drafts" / "draft.shopping"
    draft_dir.mkdir(parents=True)
    (draft_dir / "builder.draft.json").write_text(
        json.dumps(
            {
                "draft_id": "draft.shopping",
                "artifact": {"kind": "scenario", "id": "shopping"},
            }
        ),
        encoding="utf-8",
    )
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = BuilderWorkbenchService(state_dir=state_dir).delete_development_skill("draft.shopping", "desktop")

    assert result["ok"] is False
    assert result["error"] == "artifact_root_missing"
    assert draft_dir.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_workbench_delete_uses_draft_root_manifest_field(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    artifact_root = tmp_path / "dev" / "scenarios" / "shopping"
    artifact_root.mkdir(parents=True)
    (artifact_root / "webui.json").write_text("{}", encoding="utf-8")
    draft_dir = state_dir / "builder" / "drafts" / "draft.shopping"
    draft_dir.mkdir(parents=True)
    (draft_dir / "builder.draft.json").write_text(
        json.dumps(
            {
                "draft_id": "draft.shopping",
                "artifact": {
                    "kind": "scenario",
                    "id": "shopping",
                    "draft_root": str(artifact_root),
                },
            }
        ),
        encoding="utf-8",
    )

    listed = BuilderWorkbenchService(state_dir=state_dir).list_development_skills("desktop")
    assert listed["items"][0]["root"] == str(artifact_root)

    result = BuilderWorkbenchService(state_dir=state_dir).delete_development_skill("draft.shopping", "desktop")

    assert result["ok"] is True
    assert not artifact_root.exists()
    assert (Path(result["archive_root"]) / "artifact" / "webui.json").exists()


def test_workbench_delete_rejects_artifact_outside_kind_root(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    unsafe_root = tmp_path / "shopping"
    unsafe_root.mkdir()
    draft_dir = state_dir / "builder" / "drafts" / "draft.shopping"
    draft_dir.mkdir(parents=True)
    (draft_dir / "builder.draft.json").write_text(
        json.dumps(
            {
                "draft_id": "draft.shopping",
                "artifact": {
                    "kind": "scenario",
                    "id": "shopping",
                    "draft_root": str(unsafe_root),
                },
            }
        ),
        encoding="utf-8",
    )

    result = BuilderWorkbenchService(state_dir=state_dir).delete_development_skill("draft.shopping", "desktop")

    assert result["ok"] is False
    assert result["error"] == "unsafe_artifact_root"
    assert unsafe_root.exists()
    assert draft_dir.exists()


def test_set_active_draft_skips_unchanged_deferred_binding_write(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.builder.workbench as workbench_module

    writes: list[Path] = []
    original_write_json = workbench_module._write_json

    def _tracked_write_json(path: Path, payload):
        writes.append(path)
        original_write_json(path, payload)

    monkeypatch.setattr(workbench_module, "_write_json", _tracked_write_json)

    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    first = service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id="draft.shopping",
        runtime_scenario_id="shopping",
        persist_projection=False,
    )
    second = service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id="draft.shopping",
        runtime_scenario_id="shopping",
        persist_projection=False,
    )

    assert first == second
    assert len(writes) == 1

    changed = service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id="draft.todo",
        runtime_scenario_id="shopping",
        persist_projection=False,
    )
    assert changed["active_draft_id"] == "draft.todo"
    assert len(writes) == 2


def test_selected_project_is_persisted_without_changing_runtime_scenario(tmp_path: Path) -> None:
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id=None,
        runtime_scenario_id="shopping",
        persist_projection=False,
    )

    binding = service.set_selected_project(
        source_webspace_id="desktop",
        object_type="skill",
        object_id="builder_skill",
        title="Builder Skill",
        description="Builder tools",
    )

    assert binding["runtime_scenario_id"] == "shopping"
    assert binding["selection"] == {
        "schema": "adaos.builder.project_selection.v2",
        "object_type": "skill",
        "object_id": "builder_skill",
        "ref": "skill:builder_skill",
        "title": "Builder Skill",
        "description": "Builder tools",
        "context_topic_id": "prompt-project:skill:builder_skill",
        "context_thread_id": "prompt-project:skill:builder_skill",
    }
    assert service.get_workspace_binding("desktop")["selection"] == binding["selection"]


def test_workspace_binding_rejects_legacy_project_selection(tmp_path: Path) -> None:
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    binding_path = service.binding_path("desktop")
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(
        json.dumps(
            {
                "source_webspace_id": "desktop",
                "runtime_scenario_id": "kanban_primary",
                "selection": {
                    "object_type": "project",
                    "object_id": "kanban",
                    "ref": "project:kanban",
                    "title": "Kanban",
                    "description": "Builder test project",
                    "topic_id": "prompt-project:project:kanban",
                    "thread_id": "prompt-project:project:kanban",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reselect the Project"):
        service.get_workspace_binding("desktop")


def test_explicit_project_selection_replaces_legacy_binding(tmp_path: Path) -> None:
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    binding_path = service.binding_path("desktop")
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(
        json.dumps(
            {
                "source_webspace_id": "desktop",
                "runtime_scenario_id": "kanban_primary",
                "selection": {
                    "object_type": "project",
                    "object_id": "kanban",
                    "title": "Legacy Kanban",
                },
            }
        ),
        encoding="utf-8",
    )

    binding = service.set_selected_project(
        source_webspace_id="desktop",
        object_type="project",
        object_id="flowboard",
        title="Flowboard",
    )

    assert binding["selection"] == {
        "schema": "adaos.builder.project_selection.v2",
        "object_type": "project",
        "object_id": "flowboard",
        "ref": "project:flowboard",
        "title": "Flowboard",
        "description": "",
        "context_topic_id": "prompt-project:project:flowboard",
        "context_thread_id": "prompt-project:project:flowboard",
    }
    assert binding["preview_target"] is None


def test_explicit_runtime_selection_replaces_legacy_binding(tmp_path: Path) -> None:
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    binding_path = service.binding_path("desktop")
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(
        json.dumps(
            {
                "source_webspace_id": "desktop",
                "runtime_scenario_id": "legacy",
                "selection": {
                    "object_type": "scenario",
                    "object_id": "legacy",
                },
            }
        ),
        encoding="utf-8",
    )

    binding = service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id=None,
        runtime_scenario_id="flowboard",
        persist_projection=False,
    )

    assert binding["selection"]["schema"] == "adaos.builder.project_selection.v2"
    assert binding["selection"]["object_id"] == "flowboard"


def test_changing_runtime_project_clears_an_explicit_preview_from_the_previous_project(tmp_path: Path) -> None:
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id=None,
        runtime_scenario_id="recipes",
        persist_projection=False,
    )
    service.set_preview_target(
        source_webspace_id="desktop",
        target={
            "object_type": "scenario",
            "object_id": "recipes",
            "stage": "prototype",
            "revision": "003",
        },
    )

    binding = service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id=None,
        runtime_scenario_id="shopping",
        persist_projection=False,
    )

    assert binding["selection"]["object_id"] == "shopping"
    assert binding["preview_target"] is None


@pytest.mark.asyncio
async def test_builder_runtime_projection_is_compact_and_host_only(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.yjs.doc as ydoc_module

    published: list[tuple[str, str, dict]] = []

    class _Data:
        def set(self, _txn, key, value):
            published.append((current_webspace[0], key, value))

    class _Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Doc:
        def get_map(self, name):
            assert name == "data"
            return _Data()

        def begin_transaction(self):
            return _Transaction()

    class _Context:
        async def __aenter__(self):
            return _Doc()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    current_webspace = [""]

    def _get_ydoc(webspace_id, **_kwargs):
        current_webspace[0] = webspace_id
        return _Context()

    monkeypatch.setattr(ydoc_module, "async_get_ydoc", _get_ydoc)
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    binding = service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id=None,
        runtime_scenario_id="shopping",
        persist_projection=False,
    )
    monkeypatch.setattr(
        type(service.reconciler),
        "describe",
        lambda _self, _source: {
            "schema": "adaos.builder.preview_runtime.v1",
            "source_webspace_id": "desktop",
            "preview_webspace_id": binding["preview_webspace_id"],
            "desired_scenario": "shopping",
            "observed_scenario": "shopping",
            "generation": 4,
            "operation_id": "preview-4",
            "status": "ready",
            "result": {"materialized_payload": "x" * 100_000},
        },
    )

    result = await service.publish_projection(
        "desktop",
        preview_state={"version": "033", "page_schema": {"widgets": ["x" * 100_000]}},
    )

    assert result["published_webspaces"] == ["desktop"]
    assert [item[0] for item in published] == ["desktop"]
    projection = published[0][2]
    assert projection["selection"]["object_id"] == "shopping"
    assert projection["selection"]["context_topic_id"] == "prompt-project:scenario:shopping"
    assert projection["selection"]["conversation_topic_id"] == "prompt-project:scenario:shopping"
    assert projection["binding"]["preview_webspace_id"] == binding["preview_webspace_id"]
    assert projection["preview_runtime"]["status"] == "ready"
    assert "result" not in projection["preview_runtime"]
    assert projection["preview_state"] == {"version": "033"}
    assert len(json.dumps(projection)) < 4_096
    assert "development_skills" not in projection
    assert "dialog" not in projection


def test_project_projection_keeps_project_context_and_primary_conversation(tmp_path: Path) -> None:
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id=None,
        runtime_scenario_id="kanban_primary",
        persist_projection=False,
    )
    service.set_selected_project(
        source_webspace_id="desktop",
        object_type="project",
        object_id="kanban",
        title="Kanban",
        persist_projection=False,
    )

    projection = service.runtime_projection("desktop")

    assert projection["selection"]["object_type"] == "project"
    assert projection["selection"]["context_topic_id"] == "prompt-project:project:kanban"
    assert projection["selection"]["context_thread_id"] == "prompt-project:project:kanban"
    assert projection["selection"]["conversation_topic_id"] == "prompt-project:scenario:kanban_primary"
    assert projection["selection"]["conversation_thread_id"] == "prompt-project:scenario:kanban_primary"
    assert "topic_id" not in projection["selection"]
    assert "thread_id" not in projection["selection"]


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
    preview_id = response.json()["binding"]["preview_webspace_id"]
    assert preview_id.startswith("preview-")

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
    assert response.json()["url"] == f"http://localhost:8100/?webspace={preview_id}"
    assert response.json()["binding"]["runtime_scenario_id"] == "demo_scenario"
    assert service.webspace_service.items[preview_id].home_scenario == "web_desktop"

    response = client.get("/api/builder/workbench/dialog-widget", params={"webspace_id": "desktop"})
    assert response.status_code == 200
    assert response.json()["widget"]["widget"] == "voice_chat"
    assert response.json()["widget"]["dialog_channel_id"] == "builder"
    assert response.json()["widget"]["thread_id"] == "prompt-project:scenario:demo_scenario"
    assert response.json()["widget"]["topic_id"] == "prompt-project:scenario:demo_scenario"
    assert response.json()["binding"]["active_draft_id"] == "draft.one"

    response = client.get("/api/builder/workbench/development-skills", params={"webspace_id": "desktop"})
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_builder_context_inspector_is_project_scoped_and_metadata_only(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    service = BuilderWorkbenchService(state_dir=state_dir)
    service.set_selected_project(
        source_webspace_id="desktop",
        object_type="scenario",
        object_id="demo_metrics",
    )
    contexts = ContextControlService(state_dir=state_dir)
    capsule = contexts.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo_metrics", "scenario:demo_metrics"],
            "authority_ref": "project:demo_metrics",
            "trust_class": "accepted",
            "sensitivity": "workspace",
            "license": "internal",
            "retention_class": "project_generation",
            "summary": "Demo Metrics governed context",
            "content": {"secret_detail": "must-not-be-projected"},
        }
    )
    contexts.bind_subject(
        subject_ref="project:demo_metrics",
        capsule_id=capsule["capsule_id"],
        purpose="builder.automation",
        audience="builder",
    )
    resolution = contexts.resolve(
        {
            "subject_refs": ["project:demo_metrics"],
            "purpose": "builder.automation",
            "audience": "builder",
        }
    )
    plan = contexts.plan({"resolution": resolution, "token_budget": 2_000})
    contexts.record_receipt(
        {
            "run_ref": "builder-run:demo-metrics",
            "plan_ref": plan["plan_ref"],
            "subject_refs": ["project:demo_metrics"],
            "selected_refs": [capsule["capsule_id"]],
            "usage": {"provider_input_tokens": 900, "cached_input_tokens": 700, "output_tokens": 100},
            "execution_route": "skill_factory.local_codex",
            "validation": {"status": "passed"},
        }
    )

    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_workbench_service] = lambda: service
    response = TestClient(app).get(
        "/api/builder/workbench/context-inspector",
        params={"webspace_id": "desktop"},
    )

    assert response.status_code == 200
    inspector = response.json()["inspector"]
    assert inspector["scope"]["project_ref"] == "project:demo_metrics"
    assert inspector["summary"]["plan_count"] == 1
    assert inspector["summary"]["receipt_count"] == 1
    assert inspector["usage_by_route"]["skill_factory.local_codex"]["fresh_plus_output"] == 300
    assert inspector["privacy"]["sealed_content_disclosed"] is False
    assert "must-not-be-projected" not in json.dumps(inspector)


def test_builder_context_inspector_uses_ticket_project_not_component_id(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    service = BuilderWorkbenchService(state_dir=state_dir)
    service.set_selected_project(
        source_webspace_id="desktop",
        object_type="scenario",
        object_id="demo_metrics_scenario",
    )
    service.set_development_ticket_context(
        source_webspace_id="desktop",
        context={
            "ticket_id": "dticket.demo-metrics",
            "component_ref": "scenario:demo_metrics_scenario.header",
            "target_scope": {
                "type": "scenario",
                "id": "demo_metrics_scenario",
                "project_ref": "project:demo_metrics",
                "project_id": "demo_metrics",
            },
        },
    )
    contexts = ContextControlService(state_dir=state_dir)
    capsule = contexts.register_capsule(
        {
            "kind": "project",
            "subject_refs": ["project:demo_metrics", "scenario:demo_metrics_scenario"],
            "authority_ref": "project:demo_metrics",
            "trust_class": "accepted",
            "summary": "Demo Metrics project context",
        }
    )
    contexts.bind_subject(
        subject_ref="project:demo_metrics",
        capsule_id=capsule["capsule_id"],
        purpose="builder.automation",
        audience="builder",
    )
    resolution = contexts.resolve(
        {
            "subject_refs": ["project:demo_metrics"],
            "purpose": "builder.automation",
            "audience": "builder",
        }
    )
    plan = contexts.plan({"resolution": resolution, "token_budget": 2_000})
    for index in range(30):
        contexts.plan(
            {
                "resolution": {
                    "subject_refs": [f"project:unrelated-{index}"],
                    "purpose": "builder.automation",
                    "audience": "builder",
                    "required": [],
                },
                "token_budget": 100,
            }
        )

    inspector = service.context_inspector("desktop")

    assert inspector["scope"]["project_ref"] == "project:demo_metrics"
    assert inspector["scope"]["component_ref"] == "scenario:demo_metrics_scenario.header"
    assert inspector["summary"]["plan_count"] == 1
    assert inspector["plans"][0]["plan_id"] == plan["plan_id"]


def test_builder_context_inspector_projects_scoped_development_feedback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "state"
    service = BuilderWorkbenchService(state_dir=state_dir)
    service.set_selected_project(
        source_webspace_id="desktop",
        object_type="skill",
        object_id="demo_metrics_skill",
    )
    monkeypatch.setattr(
        "adaos.services.builder.workbench._dev_owner_project_scope",
        lambda ref: (
            {"project_id": "demo_metrics", "project_ref": "project:demo_metrics"}
            if ref == "skill:demo_metrics_skill"
            else {}
        ),
    )
    feedback = DevelopmentFeedbackService(state_dir=state_dir)
    relevant = feedback.capture(
        source="codex",
        category="inefficient_contract",
        summary="The resource query requires a full snapshot for one metric.",
        impact=["efficiency"],
        target_refs=["project:demo_metrics", "skill:demo_metrics_skill"],
        actor="codex:test",
    )["feedback"]
    feedback.capture(
        source="codex",
        category="ambiguous_contract",
        summary="An unrelated media contract is unclear.",
        target_refs=["project:media_center"],
        actor="codex:test",
    )

    inspector = service.context_inspector("desktop")

    projection = inspector["development_feedback"]
    assert inspector["scope"]["project_ref"] == "project:demo_metrics"
    assert projection["authority"] == "adaos.development_feedback"
    assert projection["read_only"] is True
    assert projection["summary"] == {
        "count": 1,
        "blocking": 0,
        "by_status": {"observed": 1},
        "by_category": {"inefficient_contract": 1},
    }
    assert [item["feedback_id"] for item in projection["items"]] == [
        relevant["feedback_id"]
    ]
    assert projection["items"][0]["routing_preview"]["recommended"][
        "owner_route"
    ] == "sdk_implementation"
    assert projection["actions"]["qualification_preview"].endswith(
        "/{feedback_id}/qualification"
    )
    assert projection["actions"]["promote"].endswith("/{feedback_id}/promote")


def test_builder_workbench_open_selects_development_ticket_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "state"
    service = BuilderWorkbenchService(state_dir=state_dir)
    monkeypatch.setattr(
        "adaos.services.builder.workbench._dev_owner_project_scope",
        lambda ref: (
            {"project_id": "legacy_app", "project_ref": "project:legacy_app"}
            if ref == "skill:legacy_skill"
            else {}
        ),
    )
    tickets = DevelopmentTicketService(state_dir=state_dir)
    signal = tickets.capture_signal(
        kind="compatibility_finding",
        summary="Legacy skill misses receiver declarations",
        target_scope={"type": "skill", "id": "legacy_skill", "source": "installed"},
        evidence_refs=[{"type": "runtime_guard", "code": "compat.stream_receiver_policy_missing"}],
    )["signal"]
    ticket = tickets.ensure_ticket_for_signal(
        signal,
        kind="runtime_compatibility_debt",
        status="accepted",
    )["ticket"]
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_workbench_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/api/builder/workbench/open",
        params={"webspace_id": "desktop", "ticket_id": ticket["ticket_id"]},
    )

    assert response.status_code == 200
    binding = response.json()["binding"]
    assert binding["selection"]["object_type"] == "skill"
    assert binding["selection"]["object_id"] == "legacy_skill"
    assert binding["development_ticket"]["ticket_id"] == ticket["ticket_id"]
    assert binding["development_ticket"]["target_scope"]["project_ref"] == (
        "project:legacy_app"
    )
    assert binding["development_ticket"]["development_source"]["status"] == "needs_materialization"
    assert "materialize_dev_source" in binding["development_ticket"]["development_source"]["options"]
    assert binding["development_ticket"]["owner_area"] == "skill"
    assert binding["development_ticket"]["component_ref"] == "skill:legacy_skill"
    assert binding["development_ticket"]["qualification"]["class"] == "needs_source"
    assert binding["development_ticket"]["qualification"]["repair_allowed"] is True
    assert binding["development_ticket"]["execution_preflight"]["status"] == (
        "qualification_required"
    )
    assert binding["development_ticket"]["execution_preflight"]["missing_fields"] == [
        "profile",
        "target_files",
        "target_refs",
        "acceptance_checks",
    ]
    assert binding["development_ticket"]["repair_batch"]["count"] == 1
    assert binding["development_ticket"]["repair_batch"]["tickets"][0]["ticket_id"] == ticket["ticket_id"]


def test_builder_workbench_qualifies_core_blocked_development_ticket(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    service = BuilderWorkbenchService(state_dir=state_dir)
    tickets = DevelopmentTicketService(state_dir=state_dir)
    signal = tickets.capture_signal(
        kind="development_request",
        summary="Modal repair needs a stable focus handoff API",
        target_scope={
            "type": "modal",
            "id": "nlu_teacher_modal",
            "source": "workspace",
            "component_ref": "modal:nlu_teacher_modal",
            "scenario_ref": "scenario:web_desktop",
        },
        owner_area="project",
        component_ref="modal:nlu_teacher_modal",
        evidence_refs=[{"type": "trace", "id": "modal.focus"}],
    )["signal"]
    project_ticket = tickets.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="accepted",
        owner_area="project",
        component_ref="modal:nlu_teacher_modal",
    )["ticket"]
    core = tickets.create_core_capability_request(
        summary="Expose focus handoff for layered Dev Ticket panels",
        component_ref="core:client",
        desired_contract="A public client/modal focus handoff API.",
        actor="builder:test",
        impact="blocker",
        blocked_ticket_ids=[project_ticket["ticket_id"]],
        evidence_refs=[{"type": "trace", "id": "modal.focus"}],
    )["ticket"]
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_workbench_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/api/builder/workbench/open",
        params={"webspace_id": "desktop", "ticket_id": project_ticket["ticket_id"]},
    )

    assert response.status_code == 200
    context = response.json()["binding"]["development_ticket"]
    assert context["ticket_id"] == project_ticket["ticket_id"]
    assert context["owner_area"] == "project"
    assert context["component_ref"] == "modal:nlu_teacher_modal"
    assert context["qualification"]["class"] == "needs_core"
    assert context["qualification"]["repair_allowed"] is False
    assert context["qualification"]["blocked_by"][0]["ticket_id"] == core["ticket_id"]
    assert context["repair_batch"]["tickets"][0]["ticket_id"] == project_ticket["ticket_id"]


def test_builder_workbench_exposes_ticket_builder_work_stream(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    service = BuilderWorkbenchService(state_dir=state_dir)
    tickets = DevelopmentTicketService(state_dir=state_dir)
    repair_service = BuilderRepairService(state_dir=state_dir)
    signal = tickets.capture_signal(
        kind="development_request",
        summary="Tune Demo Metrics workbench CRUD controls",
        target_scope={
            "type": "skill",
            "id": "demo_metrics_skill",
            "source": "workspace",
            "component_ref": "skill:demo_metrics_skill",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:demo_metrics_skill",
        evidence_refs=[{"type": "trace", "id": "demo.metrics.feedback"}],
    )["signal"]
    ticket = tickets.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="accepted",
        owner_area="skill",
        component_ref="skill:demo_metrics_skill",
    )["ticket"]
    first = tickets.handoff_ticket(
        ticket["ticket_id"],
        mode="interactive",
        repair_service=repair_service,
        actor="user:owner",
    )
    repair_service.record_acceptance(
        first["repair"]["repair_id"],
        capability_works=True,
        regression_free=True,
        evidence_refs=[{"type": "test", "id": "tests/test_demo_metrics.py"}],
        actor="builder:test",
    )
    tickets.comment_ticket(ticket["ticket_id"], body="User follow-up after first repair.", actor="user:owner")
    second = tickets.handoff_ticket(
        ticket["ticket_id"],
        mode="autonomous",
        repair_service=repair_service,
        actor="user:owner",
    )
    app = FastAPI()
    app.include_router(builder_api.router, prefix="/api/builder")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[builder_api._get_workbench_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/api/builder/workbench/open",
        params={"webspace_id": "desktop", "ticket_id": ticket["ticket_id"]},
    )

    assert response.status_code == 200
    context = response.json()["binding"]["development_ticket"]
    stream = context["work_stream"]
    assert stream["schema"] == "adaos.builder.ticket_work_stream.v1"
    assert stream["authority"]["user_ticket"] == "adaos.dev.ticket"
    assert stream["authority"]["builder_work"] == "adaos.builder.repair_task"
    assert stream["authority"]["token_usage"] == "adaos.root_mgmnt.codex_usage_event.v1"
    assert stream["lifecycle_split"]["one_user_ticket_can_spawn_many_builder_items"] is True
    assert stream["builder_work_count"] == 2
    assert len(context["builder_work_items"]) == 2
    by_id = {item["work_id"]: item for item in context["builder_work_items"]}
    assert by_id[first["repair"]["repair_id"]]["status"] == "resolved"
    assert by_id[second["repair"]["repair_id"]]["status"] == "open"
    assert all(item["read_only"] is True for item in context["builder_work_items"])
    assert all(item["human_manageable"] is False for item in context["builder_work_items"])
    assert all(
        item["token_accounting"]["subscription_resource"] == "codex.api.tokens"
        for item in context["builder_work_items"]
    )
    assert any(entry["kind"] == "user_comment" for entry in stream["entries"])


def test_get_workspace_binding_migrates_runtime_dialog_topic(tmp_path: Path) -> None:
    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    stale = service.set_active_draft(
        source_webspace_id="desktop",
        active_draft_id="draft.prototype",
        runtime_scenario_id="prototype_app",
        persist_projection=False,
    )
    path = service.binding_path("desktop")
    stale = dict(stale)
    stale["dialog"] = {
        **dict(stale.get("dialog") or {}),
        "thread_id": "thread.builder.desktop.draft.prototype",
        "topic_id": "builder:desktop:draft.prototype",
    }
    path.write_text(json.dumps(stale), encoding="utf-8")

    migrated = service.get_workspace_binding("desktop")

    assert migrated["active_draft_id"] == "draft.prototype"
    assert migrated["runtime_scenario_id"] == "prototype_app"
    assert migrated["dialog"]["thread_id"] == "prompt-project:scenario:prototype_app"
    assert migrated["dialog"]["topic_id"] == "prompt-project:scenario:prototype_app"


@pytest.mark.asyncio
async def test_builder_project_changed_does_not_trigger_duplicate_preview_reload(monkeypatch) -> None:
    import adaos.services.scenario.webspace_runtime as webspace_runtime

    calls: list[tuple[str, str, str | None]] = []

    async def _reload_preview_webspaces_for_project(object_type: str, object_id: str, *, reason: str | None = None):
        calls.append((object_type, object_id, reason))
        return {"ok": True}

    monkeypatch.setattr(
        webspace_runtime,
        "reload_preview_webspaces_for_project",
        _reload_preview_webspaces_for_project,
    )

    await webspace_runtime._on_prompt_project_changed(
        {
            "object_type": "scenario",
            "object_id": "prototype_app",
            "reason": "builder_ui_revision_written",
        }
    )
    await webspace_runtime._on_prompt_project_changed(
        {
            "object_type": "scenario",
            "object_id": "prototype_app",
            "reason": "builder_project_updated",
        }
    )
    await webspace_runtime._on_prompt_project_changed(
        {
            "object_type": "scenario",
            "object_id": "prototype_app",
            "reason": "manual_project_file_save",
        }
    )

    assert calls == [("scenario", "prototype_app", "manual_project_file_save")]


@pytest.mark.asyncio
async def test_builder_preview_selected_skips_superseded_builder_event(monkeypatch) -> None:
    import adaos.services.builder.workbench as workbench_module

    ensure_calls: list[dict] = []

    class _Workbench:
        def get_workspace_binding(self, source_webspace_id):
            assert source_webspace_id == "desktop"
            return {"runtime_scenario_id": "builder"}

        async def ensure_dev_webspace(self, source_webspace_id, **kwargs):
            ensure_calls.append({"source_webspace_id": source_webspace_id, **kwargs})

    monkeypatch.setattr(workbench_module, "BuilderWorkbenchService", _Workbench)

    await workbench_module._on_builder_preview_selected(
        {
            "source_webspace_id": "desktop",
            "object_type": "scenario",
            "object_id": "stale_prototype",
            "scenario_id": "stale_prototype",
            "reason": "builder_project_created",
        }
    )

    assert ensure_calls == []


@pytest.mark.asyncio
async def test_builder_source_reload_republishes_durable_selection(monkeypatch, tmp_path: Path) -> None:
    import adaos.services.builder.workbench as workbench_module

    service = BuilderWorkbenchService(state_dir=tmp_path / "state")
    service.set_selected_project(
        source_webspace_id="desktop",
        object_type="scenario",
        object_id="shopping",
        persist_projection=False,
    )
    scheduled: list[str] = []
    claimed: list[tuple[str, str]] = []

    monkeypatch.setattr(
        BuilderWorkbenchService,
        "resolve_action_source_webspace_id",
        lambda _self, source_webspace_id, *, current_scenario_id: claimed.append(
            (source_webspace_id, current_scenario_id)
        )
        or source_webspace_id,
    )
    monkeypatch.setattr(workbench_module, "BuilderWorkbenchService", lambda: service)
    monkeypatch.setattr(
        workbench_module,
        "_schedule_projection_publish",
        lambda _service, source_webspace_id, **_kwargs: scheduled.append(source_webspace_id),
    )

    await workbench_module._on_builder_source_webspace_reloaded(
        {"webspace_id": "desktop", "scenario_id": "builder"}
    )
    await workbench_module._on_builder_source_webspace_reloaded(
        {"webspace_id": "desktop", "scenario_id": "shopping"}
    )
    await workbench_module._on_builder_source_webspace_reloaded(
        {"webspace_id": "unbound", "scenario_id": "builder"}
    )

    assert scheduled == ["desktop"]
    assert claimed == [("desktop", "builder"), ("unbound", "builder")]
