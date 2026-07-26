from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services import artifact_subscription_update as update_service


PLAN_DIGEST = "sha256:" + "a" * 64
LOCK_DIGEST = "sha256:" + "b" * 64


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def workspace_dir(self) -> Path:
        return self.root / "workspace"

    def skills_dir(self) -> Path:
        return self.root / "workspace" / "skills"


class _Root:
    def __init__(self, *, ctx) -> None:
        self.ctx = ctx
        self.activations: list[dict] = []

    def plan_artifact_subscription_update(self, project_id: str):
        return {"ok": True, "project_id": project_id, "plan_digest": PLAN_DIGEST}

    def activate_artifact_subscription(self, project_id: str, **kwargs):
        self.activations.append({"project_id": project_id, **kwargs})
        lock = self.ctx.test_lock
        reload_receipt = kwargs["reload_runtime"](lock)
        health_receipt = kwargs["health_check"](lock)
        assert reload_receipt["status"] == "reloaded"
        assert health_receipt["status"] == "passed"
        return {
            "ok": True,
            "project_id": project_id,
            "release": f"{project_id}@{lock.components[0].version}",
            "release_digest": "sha256:" + "c" * 64,
        }


def _context(tmp_path: Path, kind: str, project_id: str, version: str):
    component = SimpleNamespace(
        kind=kind,
        artifact_id=project_id,
        version=version,
        digest="sha256:" + "d" * 64,
    )
    lock = SimpleNamespace(
        components=(component,),
        to_dict=lambda: {"lock_digest": LOCK_DIGEST},
    )
    return SimpleNamespace(
        paths=_Paths(tmp_path),
        skills_repo=object(),
        scenarios_repo=object(),
        sql=object(),
        git=object(),
        bus=None,
        caps=object(),
        settings=object(),
        test_lock=lock,
    )


def test_scenario_update_uses_one_runtime_contract(monkeypatch, tmp_path) -> None:
    ctx = _context(tmp_path, "scenario", "recipes", "2.0.0")
    syncs: list[dict] = []
    projections: list[dict] = []

    class _ScenarioManager:
        def __init__(self, **_kwargs):
            pass

        def sync_to_yjs(self, project_id: str, **kwargs):
            syncs.append({"project_id": project_id, **kwargs})
            return {"ok": True, "status": "synced"}

    async def _rebuild(webspace_id: str, **kwargs):
        projections.append({"webspace_id": webspace_id, **kwargs})
        return {"ok": True, "status": "completed"}

    monkeypatch.setattr(update_service, "RootDeveloperService", _Root)
    monkeypatch.setattr(update_service, "ScenarioManager", _ScenarioManager)
    monkeypatch.setattr(update_service, "SqliteScenarioRegistry", lambda _sql: object())
    monkeypatch.setattr(update_service, "rebuild_webspace_from_sources", _rebuild)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)
    monkeypatch.setattr(coordinator, "is_subscribed", lambda _project_id: True)

    result = asyncio.run(
        coordinator.update(
            "scenario",
            "recipes",
            expected_plan_digest=PLAN_DIGEST,
            webspace_id="desktop",
        )
    )

    assert result["mode"] == "package_activation"
    assert result["runtime_receipts"][LOCK_DIGEST]["version"] == "2.0.0"
    assert syncs == [{"project_id": "recipes", "webspace_id": "desktop", "emit_event": False}]
    assert projections[0]["source_of_truth"] == "workspace_lock"
    assert coordinator.root.activations[0]["expected_plan_digest"] == PLAN_DIGEST


def test_skill_update_requires_runtime_and_webspace_projection(monkeypatch, tmp_path) -> None:
    ctx = _context(tmp_path, "skill", "recipe_skill", "3.0.0")
    calls: list[str] = []

    class _SkillManager:
        def __init__(self, **_kwargs):
            pass

    class _Loader:
        async def reload_skill_handlers(self, _skills_dir, _project_id):
            calls.append("handlers")
            return {"ok": True, "status": "reloaded"}

    async def _rebuild(**_kwargs):
        calls.append("projection")
        return {"ok": True, "status": "completed"}

    def _refresh(*_args, **_kwargs):
        calls.append("runtime")
        return {"ok": True, "status": "completed"}

    def _invalidate(*_args, **_kwargs):
        calls.append("cache")
        return {"ok": True, "status": "completed"}

    monkeypatch.setattr(update_service, "RootDeveloperService", _Root)
    monkeypatch.setattr(update_service, "SkillManager", _SkillManager)
    monkeypatch.setattr(update_service, "SqliteSkillRegistry", lambda _sql: object())
    monkeypatch.setattr(update_service, "ImportlibSkillsLoader", _Loader)
    monkeypatch.setattr(update_service, "refresh_skill_runtime", _refresh)
    monkeypatch.setattr(update_service, "invalidate_webspace_materialization_cache", _invalidate)
    monkeypatch.setattr(update_service, "rebuild_webspace_projection", _rebuild)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)
    monkeypatch.setattr(coordinator, "is_subscribed", lambda _project_id: True)

    result = asyncio.run(
        coordinator.update(
            "skill",
            "recipe_skill",
            expected_plan_digest=PLAN_DIGEST,
        )
    )

    assert result["runtime_receipts"][LOCK_DIGEST]["version"] == "3.0.0"
    assert calls == ["runtime", "handlers", "cache", "projection"]


def test_update_requires_reviewed_plan_before_activation(monkeypatch, tmp_path) -> None:
    ctx = _context(tmp_path, "scenario", "recipes", "2.0.0")
    monkeypatch.setattr(update_service, "RootDeveloperService", _Root)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)
    monkeypatch.setattr(coordinator, "is_subscribed", lambda _project_id: True)

    with pytest.raises(update_service.ArtifactSubscriptionUpdateError) as raised:
        asyncio.run(coordinator.update("scenario", "recipes"))

    assert raised.value.code == "artifact_update_plan_required"
    assert raised.value.update_plan["plan_digest"] == PLAN_DIGEST
    assert coordinator.root.activations == []


def test_update_rejects_deferred_projection_before_planning(monkeypatch, tmp_path) -> None:
    ctx = _context(tmp_path, "skill", "recipe_skill", "3.0.0")
    monkeypatch.setattr(update_service, "RootDeveloperService", _Root)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)
    monkeypatch.setattr(coordinator, "is_subscribed", lambda _project_id: True)

    with pytest.raises(update_service.ArtifactSubscriptionUpdateError) as raised:
        asyncio.run(
            coordinator.update(
                "skill",
                "recipe_skill",
                expected_plan_digest=PLAN_DIGEST,
                defer_webspace_rebuild=True,
            )
        )

    assert raised.value.code == "artifact_runtime_projection_required"
    assert coordinator.root.activations == []
