from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.domain.artifact_release import StableSubscription
from adaos.services import artifact_subscription_update as update_service
from adaos.services.artifact_pipeline import SubscriptionStore


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

    def inspect_artifact_subscription_update(self, project_id: str):
        return {
            "ok": True,
            "project_id": project_id,
            "available": True,
            "reason": "channel_moved",
            "update_plan": self.plan_artifact_subscription_update(project_id),
        }

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
    assert result["update_route"]["package_required"] is True
    assert result["update_route"]["legacy_allowed"] is False


def test_update_route_is_subscription_based_and_corruption_fails_closed(
    monkeypatch,
    tmp_path,
) -> None:
    ctx = _context(tmp_path, "scenario", "recipes", "2.0.0")
    monkeypatch.setattr(update_service, "RootDeveloperService", _Root)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)

    legacy = coordinator.select_route("recipes")
    assert legacy.mode == "legacy_source_pull"
    assert legacy.legacy_allowed is True
    assert legacy.package_required is False

    SubscriptionStore(coordinator.subscription_path).save(
        StableSubscription(
            project_id="recipes",
            installed_release="recipes@1.0.0",
            installed_digest="sha256:" + "e" * 64,
        )
    )
    package = coordinator.select_route("recipes")
    assert package.mode == "package_activation"
    assert package.package_required is True
    assert package.legacy_allowed is False

    coordinator.subscription_path.write_text("{", encoding="utf-8")
    with pytest.raises(update_service.ArtifactSubscriptionUpdateError) as raised:
        coordinator.select_route("recipes")
    assert raised.value.code == "artifact_subscription_store_invalid"


def test_dry_run_returns_package_noop_when_subscription_is_current(monkeypatch, tmp_path) -> None:
    ctx = _context(tmp_path, "scenario", "recipes", "2.0.0")

    class _CurrentRoot(_Root):
        def inspect_artifact_subscription_update(self, project_id: str):
            return {
                "ok": True,
                "project_id": project_id,
                "available": False,
                "activation_allowed": False,
                "reason": "up_to_date",
                "update_plan": None,
            }

        def plan_artifact_subscription_update(self, project_id: str):
            raise AssertionError(f"planner must not run for current subscription: {project_id}")

    monkeypatch.setattr(update_service, "RootDeveloperService", _CurrentRoot)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)
    monkeypatch.setattr(coordinator, "is_subscribed", lambda _project_id: True)

    result = asyncio.run(coordinator.update("scenario", "recipes", dry_run=True))

    assert result["mode"] == "package_plan"
    assert result["updated"] is False
    assert result["update_route"]["package_required"] is True
    assert result["update_route"]["legacy_allowed"] is False
    assert result["update_plan"]["schema"] == "adaos.artifact.subscription_update_noop.v1"
    assert result["update_plan"]["project_id"] == "recipes"
    assert result["update_plan"]["status"] == "up_to_date"


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
