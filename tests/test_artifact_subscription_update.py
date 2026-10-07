from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.domain.artifact_release import StableSubscription
from adaos.services import artifact_subscription_update as update_service
from adaos.services import conversation_interactions, conversation_store
from adaos.services.pending_action_projection import project_pending_action_from_store
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


def _qualified_update(*, desired: str = "sha256:desired", installed: str = "sha256:installed"):
    return {
        "schema": "adaos.runtime_compatibility.classification.v1",
        "code": "eligible_exact_update",
        "evidence_complete": True,
        "human_decision_required": True,
        "recommended_action": "offer_exact_update",
        "desired_package_digest": desired,
        "installed_package_digest": installed,
        "eligible_update_version": "2.0.0",
        "eligible_update_package_digest": desired,
        "eligible_update_from_package_digest": installed,
    }


def test_qualified_runtime_update_is_bound_to_exact_from_target_and_plan(
    monkeypatch,
    tmp_path,
) -> None:
    ctx = _context(tmp_path, "skill", "recipe_skill", "2.0.0")

    class _ExactRoot(_Root):
        def inspect_artifact_subscription_update(self, project_id: str):
            return {
                "ok": True,
                "project_id": project_id,
                "available": True,
                "update_plan": {
                    "schema": "adaos.artifact.subscription_update_plan.v1",
                    "plan_digest": PLAN_DIGEST,
                    "activation": {
                        "observed_components": [
                            {
                                "key": "skill:recipe_skill",
                                "version": "1.0.0",
                                "package_digest": "sha256:installed",
                            }
                        ],
                        "target_components": [
                            {
                                "key": "skill:recipe_skill",
                                "version": "2.0.0",
                                "package_digest": "sha256:desired",
                            }
                        ],
                        "component_changes": {"changed": ["skill:recipe_skill"]},
                        "permissions": {"added": ["network.egress"]},
                        "migrations": {"count": 0},
                        "rollback": {"available": True},
                        "warnings": [],
                    },
                },
            }

    monkeypatch.setattr(update_service, "RootDeveloperService", _ExactRoot)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)

    command = asyncio.run(
        coordinator.plan_qualified_runtime_update(
            "skill",
            "recipe_skill",
            qualification=_qualified_update(),
        )
    )

    assert command["from"] == {
        "version": "1.0.0",
        "package_digest": "sha256:installed",
    }
    assert command["target"] == {
        "version": "2.0.0",
        "package_digest": "sha256:desired",
    }
    assert command["plan_digest"] == PLAN_DIGEST
    assert command["consequences"]["permissions"]["added"] == ["network.egress"]
    assert command["command_digest"].startswith("sha256:")


def test_qualified_runtime_update_rejects_identity_drift_before_user_choice(
    monkeypatch,
    tmp_path,
) -> None:
    ctx = _context(tmp_path, "skill", "recipe_skill", "2.0.0")

    class _DriftedRoot(_Root):
        def inspect_artifact_subscription_update(self, project_id: str):
            return {
                "ok": True,
                "project_id": project_id,
                "available": True,
                "update_plan": {
                    "plan_digest": PLAN_DIGEST,
                    "activation": {
                        "observed_components": [
                            {"key": "skill:recipe_skill", "version": "1.0.0", "package_digest": "sha256:other"}
                        ],
                        "target_components": [
                            {"key": "skill:recipe_skill", "version": "2.0.0", "package_digest": "sha256:desired"}
                        ],
                    },
                },
            }

    monkeypatch.setattr(update_service, "RootDeveloperService", _DriftedRoot)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)

    with pytest.raises(update_service.ArtifactSubscriptionUpdateError) as raised:
        asyncio.run(
            coordinator.plan_qualified_runtime_update(
                "skill",
                "recipe_skill",
                qualification=_qualified_update(),
            )
        )

    assert raised.value.code == "runtime_compatibility_identity_changed"


def test_qualified_runtime_update_revalidates_command_before_activation(
    monkeypatch,
    tmp_path,
) -> None:
    ctx = _context(tmp_path, "skill", "recipe_skill", "2.0.0")
    monkeypatch.setattr(update_service, "RootDeveloperService", _Root)
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(ctx)
    command = {
        "schema": update_service.RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA,
        "kind": "skill",
        "project_id": "recipe_skill",
        "from": {"version": "1.0.0", "package_digest": "sha256:installed"},
        "target": {"version": "2.0.0", "package_digest": "sha256:desired"},
        "plan_digest": PLAN_DIGEST,
        "consequences": {},
        "command_digest": "sha256:command",
    }
    updates: list[dict] = []

    async def _plan(*_args, **_kwargs):
        return dict(command)

    async def _update(kind, project_id, **kwargs):
        updates.append({"kind": kind, "project_id": project_id, **kwargs})
        return {"ok": True, "updated": True}

    monkeypatch.setattr(coordinator, "plan_qualified_runtime_update", _plan)
    monkeypatch.setattr(coordinator, "update", _update)
    stale = {**command, "plan_digest": "sha256:stale"}

    with pytest.raises(update_service.ArtifactSubscriptionUpdateError) as raised:
        asyncio.run(
            coordinator.execute_qualified_runtime_update(
                stale,
                qualification=_qualified_update(),
                permission_decision=True,
            )
        )
    result = asyncio.run(
        coordinator.execute_qualified_runtime_update(
            command,
            qualification=_qualified_update(),
            permission_decision={"approved": True},
            webspace_id="desktop",
        )
    )

    assert raised.value.code == "runtime_compatibility_command_stale"
    assert result["updated"] is True
    assert updates == [
        {
            "kind": "skill",
            "project_id": "recipe_skill",
            "expected_plan_digest": PLAN_DIGEST,
            "permission_decision": {"approved": True},
            "idempotency_key": "runtime-compatibility:sha256:command",
            "webspace_id": "desktop",
        }
    ]


def test_exact_runtime_update_uses_canonical_interaction_and_projects_outcome(
    monkeypatch,
    _autocontext,
) -> None:
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(_autocontext)
    command = {
        "schema": update_service.RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA,
        "kind": "skill",
        "project_id": "recipe_skill",
        "from": {"version": "1.0.0", "package_digest": "sha256:installed"},
        "target": {"version": "2.0.0", "package_digest": "sha256:desired"},
        "plan_digest": PLAN_DIGEST,
        "consequences": {
            "permissions": {"added": ["network.egress"]},
            "migrations": {"count": 0},
            "rollback": {"available": True},
        },
    }
    canonical = json.dumps(
        command, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    command["command_digest"] = "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    executions: list[dict] = []

    async def _plan(*_args, **_kwargs):
        return dict(command)

    async def _execute(received, **kwargs):
        executions.append({"command": dict(received), **kwargs})
        return {
            "ok": True,
            "updated": True,
            "release": "recipe_skill@2.0.0",
            "release_digest": "sha256:release",
            "mode": "package_activation",
            "reviewed_plan_digest": PLAN_DIGEST,
        }

    monkeypatch.setattr(coordinator, "plan_qualified_runtime_update", _plan)
    monkeypatch.setattr(coordinator, "execute_qualified_runtime_update", _execute)
    ordinary_profile = conversation_interactions.standard_capability_profile("web")
    limited = asyncio.run(
        coordinator.publish_qualified_runtime_update_interaction(
            "skill",
            "recipe_skill",
            qualification=_qualified_update(),
            conversation_id="conv.runtime-update-limited",
            owner="skill:runtime_compatibility",
            expires_at="2099-01-01T00:00:00+00:00",
            interaction_id="interaction.runtime-update-limited",
            capability_profile=ordinary_profile,
        )
    )
    limited_actions = {
        item["action_id"]: item for item in limited["presentation"]["actions"]
    }
    assert limited["presentation"]["supported"] is True
    assert limited["presentation"]["reason_code"] == "partial_assurance_handoff"
    assert limited_actions["approve_exact_update"]["enabled"] is False
    assert limited_actions["approve_exact_update"]["token"] is None
    assert limited_actions["refuse"]["enabled"] is True
    limited_projection = project_pending_action_from_store(
        "interaction.runtime-update-limited",
        principal={"application_id": "runtime_compatibility"},
    )
    projected_choices = {item["id"]: item for item in limited_projection["choices"]}
    assert projected_choices["approve_exact_update"]["available"] is False
    assert projected_choices["approve_exact_update"]["unavailable_reason"].startswith(
        "assurance_handoff_required:"
    )
    assert projected_choices["refuse"]["available"] is True

    profile = conversation_interactions.standard_capability_profile("web")
    profile["profile_id"] = "profile.web.step-up"
    profile["capabilities"]["step_up"] = True

    published = asyncio.run(
        coordinator.publish_qualified_runtime_update_interaction(
            "skill",
            "recipe_skill",
            qualification=_qualified_update(),
            conversation_id="conv.runtime-update",
            owner="skill:runtime_compatibility",
            expires_at="2099-01-01T00:00:00+00:00",
            interaction_id="interaction.runtime-update",
            capability_profile=profile,
        )
    )
    approval = next(
        item for item in published["presentation"]["actions"]
        if item["action_id"] == "approve_exact_update"
    )
    accepted = conversation_interactions.submit_action_token(
        approval["token"],
        actor_id="user:owner",
        idempotency_key="runtime-update:approve",
    )
    abandoned = conversation_store.claim_interaction_dispatch(
        accepted["response"]["response_id"],
        lease_owner="artifact-update:crashed-worker",
        reconciliation_contract="adaos.artifact_subscription_update.dispatch_replay.v1",
        lease_seconds=10,
        now_epoch=100,
        now_iso="2026-10-07T00:00:00+00:00",
    )
    assert abandoned["status"] == "dispatching"

    result = asyncio.run(
        coordinator.execute_qualified_runtime_update_interaction(
            "interaction.runtime-update",
            accepted["response"]["response_id"],
            webspace_id="desktop",
        )
    )
    duplicate = asyncio.run(
        coordinator.execute_qualified_runtime_update_interaction(
            "interaction.runtime-update",
            accepted["response"]["response_id"],
            webspace_id="desktop",
        )
    )

    assert result["ok"] is True
    assert result["interaction"]["status"] == "completed"
    assert result["dispatch"]["status"] == "succeeded"
    assert result["dispatch"]["attempt_count"] == 2
    assert result["outcome"]["effect_assertion"]["observed"] is True
    assert result["outcome"]["effect_assertion"]["effect_ref"]["digest"] == command["command_digest"]
    assert duplicate["duplicate"] is True
    assert len(executions) == 1
    assert executions[0]["command"] == command
    assert executions[0]["permission_decision"]["response_id"] == accepted["response"]["response_id"]
    assert conversation_store.get_interaction("interaction.runtime-update")["status"] == "completed"


def test_exact_runtime_update_reconciler_resumes_only_owned_dispatches(
    monkeypatch,
    _autocontext,
) -> None:
    command_digest = "sha256:" + "a" * 64
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(_autocontext)
    command = {
        "schema": update_service.RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA,
        "kind": "skill",
        "project_id": "example",
        "from": {"version": "1.0.0", "package_digest": "sha256:old"},
        "target": {"version": "2.0.0", "package_digest": "sha256:new"},
        "plan_digest": PLAN_DIGEST,
        "command_digest": command_digest,
        "consequences": {},
    }

    async def _plan(*_args, **_kwargs):
        return dict(command)

    monkeypatch.setattr(coordinator, "plan_qualified_runtime_update", _plan)
    profile = conversation_interactions.standard_capability_profile("web")
    profile["profile_id"] = "profile.web.step-up"
    profile["capabilities"]["step_up"] = True
    presentation = asyncio.run(
        coordinator.publish_qualified_runtime_update_interaction(
            "skill",
            "example",
            qualification=_qualified_update(),
            conversation_id="conv.runtime-update-reconcile",
            owner="skill:runtime_compatibility",
            expires_at="2099-01-01T00:00:00+00:00",
            interaction_id="interaction.runtime-update-reconcile",
            capability_profile=profile,
        )
    )
    token = presentation["presentation"]["actions"][0]["token"]
    accepted = conversation_interactions.submit_action_token(
        token,
        actor_id="user:owner",
        idempotency_key="reconcile:exact-update",
        metadata={"webspace_id": "desktop-dev"},
    )
    calls: list[dict] = []

    async def _execute(self, interaction_id, response_id, **kwargs):
        calls.append(
            {
                "interaction_id": interaction_id,
                "response_id": response_id,
                **kwargs,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(
        update_service.ArtifactSubscriptionUpdateCoordinator,
        "execute_qualified_runtime_update_interaction",
        _execute,
    )

    result = asyncio.run(
        update_service.reconcile_qualified_runtime_update_interaction_dispatches(
            _autocontext,
            limit=20,
        )
    )

    assert result["errors"] == []
    assert result["executed_dispatch_ids"] == [
        accepted["dispatch"]["dispatch_id"]
    ]
    assert calls == [
        {
            "interaction_id": "interaction.runtime-update-reconcile",
            "response_id": accepted["response"]["response_id"],
            "webspace_id": "desktop-dev",
            "lease_owner": "reconciler:artifact-subscription-update",
        }
    ]


def test_exact_runtime_update_refusal_is_terminal_without_executor(
    monkeypatch,
    _autocontext,
) -> None:
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(_autocontext)
    command = {
        "schema": update_service.RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA,
        "kind": "skill",
        "project_id": "refused_skill",
        "from": {"version": "1.0.0", "package_digest": "sha256:old"},
        "target": {"version": "2.0.0", "package_digest": "sha256:new"},
        "plan_digest": PLAN_DIGEST,
        "command_digest": "sha256:" + "c" * 64,
        "consequences": {},
    }
    executions: list[dict] = []

    async def _plan(*_args, **_kwargs):
        return dict(command)

    async def _execute(*args, **kwargs):
        executions.append({"args": args, "kwargs": kwargs})
        raise AssertionError("a refusal must not invoke the update executor")

    monkeypatch.setattr(coordinator, "plan_qualified_runtime_update", _plan)
    monkeypatch.setattr(coordinator, "execute_qualified_runtime_update", _execute)
    presentation = asyncio.run(
        coordinator.publish_qualified_runtime_update_interaction(
            "skill",
            "refused_skill",
            qualification=_qualified_update(),
            conversation_id="conv.runtime-update-refused",
            owner="skill:runtime_compatibility",
            expires_at="2099-01-01T00:00:00+00:00",
            interaction_id="interaction.runtime-update-refused",
        )
    )
    refusal = next(
        item
        for item in presentation["presentation"]["actions"]
        if item["action_id"] == "refuse"
    )
    accepted = conversation_interactions.submit_action_token(
        refusal["token"],
        actor_id="user:owner",
        idempotency_key="runtime-update:refuse",
    )

    result = asyncio.run(
        coordinator.execute_qualified_runtime_update_interaction(
            "interaction.runtime-update-refused",
            accepted["response"]["response_id"],
        )
    )
    duplicate = asyncio.run(
        coordinator.execute_qualified_runtime_update_interaction(
            "interaction.runtime-update-refused",
            accepted["response"]["response_id"],
        )
    )

    assert result["ok"] is False
    assert result["refused"] is True
    assert result["dispatch"]["status"] == "cancelled"
    assert result["interaction"]["status"] == "cancelled"
    assert duplicate["duplicate"] is True
    assert duplicate["dispatch"]["status"] == "cancelled"
    assert executions == []


@pytest.mark.parametrize(
    ("reason_code", "project_id"),
    [
        ("runtime_compatibility_command_stale", "stale_skill"),
        ("artifact_subscription_not_found", "uninstalled_skill"),
        ("artifact_runtime_component_missing", "executor_lost_skill"),
    ],
)
def test_exact_runtime_update_failure_is_durable_and_not_retried(
    monkeypatch,
    _autocontext,
    reason_code: str,
    project_id: str,
) -> None:
    coordinator = update_service.ArtifactSubscriptionUpdateCoordinator(_autocontext)
    command = {
        "schema": update_service.RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA,
        "kind": "skill",
        "project_id": project_id,
        "from": {"version": "1.0.0", "package_digest": "sha256:old"},
        "target": {"version": "2.0.0", "package_digest": "sha256:new"},
        "plan_digest": PLAN_DIGEST,
        "command_digest": "sha256:" + hashlib.sha256(project_id.encode()).hexdigest(),
        "consequences": {},
    }
    executions: list[dict] = []

    async def _plan(*_args, **_kwargs):
        return dict(command)

    async def _execute(*args, **kwargs):
        executions.append({"args": args, "kwargs": kwargs})
        raise update_service.ArtifactSubscriptionUpdateError(
            "the reviewed update can no longer execute",
            code=reason_code,
        )

    monkeypatch.setattr(coordinator, "plan_qualified_runtime_update", _plan)
    monkeypatch.setattr(coordinator, "execute_qualified_runtime_update", _execute)
    interaction_id = f"interaction.runtime-update-failed.{project_id}"
    presentation = asyncio.run(
        coordinator.publish_qualified_runtime_update_interaction(
            "skill",
            project_id,
            qualification=_qualified_update(),
            conversation_id=f"conv.runtime-update-failed.{project_id}",
            owner="skill:runtime_compatibility",
            expires_at="2099-01-01T00:00:00+00:00",
            interaction_id=interaction_id,
            capability_profile={
                **conversation_interactions.standard_capability_profile("web"),
                "profile_id": "profile.web.step-up",
                "capabilities": {
                    **conversation_interactions.standard_capability_profile("web")[
                        "capabilities"
                    ],
                    "step_up": True,
                },
            },
        )
    )
    approval = next(
        item
        for item in presentation["presentation"]["actions"]
        if item["action_id"] == "approve_exact_update"
    )
    accepted = conversation_interactions.submit_action_token(
        approval["token"],
        actor_id="user:owner",
        idempotency_key=f"runtime-update:failed:{project_id}",
    )

    with pytest.raises(update_service.ArtifactSubscriptionUpdateError) as raised:
        asyncio.run(
            coordinator.execute_qualified_runtime_update_interaction(
                interaction_id,
                accepted["response"]["response_id"],
            )
        )
    duplicate = asyncio.run(
        coordinator.execute_qualified_runtime_update_interaction(
            interaction_id,
            accepted["response"]["response_id"],
        )
    )
    dispatch = conversation_store.get_interaction_dispatch(
        response_id=accepted["response"]["response_id"]
    )

    assert raised.value.code == reason_code
    assert dispatch is not None
    assert dispatch["status"] == "failed"
    assert dispatch["outcome"]["reason_code"] == reason_code
    assert duplicate["duplicate"] is True
    assert duplicate["ok"] is False
    assert duplicate["dispatch"]["status"] == "failed"
    assert len(executions) == 1
