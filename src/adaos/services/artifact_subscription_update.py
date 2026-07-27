from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from adaos.adapters.db import SqliteScenarioRegistry, SqliteSkillRegistry
from adaos.services.agent_context import AgentContext
from adaos.services.artifact_pipeline import SubscriptionStore
from adaos.services.eventbus import emit as bus_emit
from adaos.services.root.service import RootDeveloperService
from adaos.services.runtime_refresh import rebuild_webspace_projection, refresh_skill_runtime
from adaos.services.scenario.manager import ScenarioManager
from adaos.services.scenario.webspace_runtime import (
    invalidate_webspace_materialization_cache,
    rebuild_webspace_from_sources,
)
from adaos.services.skill.manager import SkillManager
from adaos.services.skills_loader_importlib import ImportlibSkillsLoader
from adaos.services.yjs.webspace import default_webspace_id


ArtifactKind = Literal["scenario", "skill"]
ARTIFACT_UPDATE_ROUTE_SCHEMA = "adaos.artifact.update_route.v1"


@dataclass(frozen=True, slots=True)
class ArtifactUpdateRoute:
    project_id: str
    mode: Literal["package_activation", "legacy_source_pull"]
    package_required: bool
    legacy_allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ARTIFACT_UPDATE_ROUTE_SCHEMA,
            "project_id": self.project_id,
            "mode": self.mode,
            "package_required": self.package_required,
            "legacy_allowed": self.legacy_allowed,
            "reason": self.reason,
        }


class ArtifactSubscriptionUpdateError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "artifact_subscription_update_failed",
        update_plan: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.update_plan = dict(update_plan) if isinstance(update_plan, Mapping) else None

    def to_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.update_plan is not None:
            detail["update_plan"] = self.update_plan
        return detail


class ArtifactSubscriptionUpdateCoordinator:
    """One reviewed package-update path for REST, WebSocket, and Builder."""

    def __init__(
        self,
        ctx: AgentContext,
        *,
        skill_manager: SkillManager | None = None,
        scenario_manager: ScenarioManager | None = None,
    ) -> None:
        self.ctx = ctx
        self.root = RootDeveloperService(ctx=ctx)
        self._skill_manager = skill_manager
        self._scenario_manager = scenario_manager

    @property
    def subscription_path(self) -> Path:
        return Path(self.ctx.paths.workspace_dir()) / ".adaos" / "subscriptions.json"

    def is_subscribed(self, project_id: str) -> bool:
        token = str(project_id or "").strip()
        if not token or not self.subscription_path.is_file():
            return False
        try:
            subscriptions = SubscriptionStore(self.subscription_path).load()
        except Exception as exc:
            raise ArtifactSubscriptionUpdateError(
                f"artifact subscription store is invalid: {exc}",
                code="artifact_subscription_store_invalid",
            ) from exc
        return token in subscriptions

    def select_route(self, project_id: str) -> ArtifactUpdateRoute:
        """Choose one update authority without failure-driven fallback."""

        token = str(project_id or "").strip()
        if not token:
            raise ArtifactSubscriptionUpdateError(
                "project_id is required",
                code="artifact_project_id_required",
            )
        if self.is_subscribed(token):
            return ArtifactUpdateRoute(
                project_id=token,
                mode="package_activation",
                package_required=True,
                legacy_allowed=False,
                reason="stable_subscription_present",
            )
        return ArtifactUpdateRoute(
            project_id=token,
            mode="legacy_source_pull",
            package_required=False,
            legacy_allowed=True,
            reason="stable_subscription_absent_bounded_compatibility",
        )

    async def plan(self, project_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.root.plan_artifact_subscription_update,
            str(project_id or "").strip(),
        )

    async def inspect(self, project_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.root.inspect_artifact_subscription_update,
            str(project_id or "").strip(),
        )

    async def update(
        self,
        kind: ArtifactKind,
        project_id: str,
        *,
        dry_run: bool = False,
        expected_plan_digest: str | None = None,
        permission_decision: bool | Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        webspace_id: str | None = None,
        defer_webspace_rebuild: bool = False,
    ) -> dict[str, Any]:
        token = str(project_id or "").strip()
        if kind not in {"scenario", "skill"}:
            raise ArtifactSubscriptionUpdateError(
                "artifact kind must be scenario or skill",
                code="artifact_kind_invalid",
            )
        if not token:
            raise ArtifactSubscriptionUpdateError(
                "project_id is required",
                code="artifact_project_id_required",
            )
        route = self.select_route(token)
        if not route.package_required:
            raise ArtifactSubscriptionUpdateError(
                f"project has no stable subscription: {token}",
                code="artifact_subscription_not_found",
            )
        if defer_webspace_rebuild and not dry_run:
            raise ArtifactSubscriptionUpdateError(
                "transactional package activation cannot defer its runtime projection",
                code="artifact_runtime_projection_required",
            )

        if dry_run:
            inspected = await self.inspect(token)
            if inspected.get("available") is not True:
                reviewed = {
                    "schema": "adaos.artifact.subscription_update_noop.v1",
                    "project_id": token,
                    "status": "up_to_date",
                    "available": False,
                    "reason": str(inspected.get("reason") or "up_to_date"),
                    "inspection": inspected,
                }
            else:
                reviewed = inspected.get("update_plan")
                if not isinstance(reviewed, Mapping):
                    raise ArtifactSubscriptionUpdateError(
                        "available package update has no reviewable plan",
                        code="artifact_update_plan_unavailable",
                    )
            return {
                "ok": True,
                "updated": False,
                "mode": "package_plan",
                "update_route": route.to_dict(),
                "update_plan": reviewed,
            }

        reviewed = await self.plan(token)
        expected = str(expected_plan_digest or "").strip().lower()
        if not expected:
            raise ArtifactSubscriptionUpdateError(
                "review the package update plan and resubmit its plan_digest",
                code="artifact_update_plan_required",
                update_plan=reviewed,
            )

        target_webspace = str(webspace_id or default_webspace_id()).strip() or default_webspace_id()
        loop = asyncio.get_running_loop()
        receipts: dict[str, dict[str, Any]] = {}
        reload_runtime, health_check = self._runtime_contract(
            kind,
            token,
            target_webspace,
            loop,
            receipts,
        )
        activated = await asyncio.to_thread(
            self.root.activate_artifact_subscription,
            token,
            idempotency_key=(str(idempotency_key or "").strip() or None),
            expected_plan_digest=expected,
            permission_decision=permission_decision,
            reload_runtime=reload_runtime,
            health_check=health_check,
        )
        self._publish_success(kind, token, target_webspace, activated)
        return {
            **activated,
            "updated": True,
            "mode": "package_activation",
            "update_route": route.to_dict(),
            "reviewed_plan_digest": expected,
            "runtime_receipts": receipts,
        }

    def _runtime_contract(
        self,
        kind: ArtifactKind,
        project_id: str,
        webspace_id: str,
        loop: asyncio.AbstractEventLoop,
        receipts: dict[str, dict[str, Any]],
    ):
        if kind == "skill":
            manager = self._skill_manager or SkillManager(
                repo=self.ctx.skills_repo,
                registry=SqliteSkillRegistry(self.ctx.sql),
                git=self.ctx.git,
                paths=self.ctx.paths,
                bus=getattr(self.ctx, "bus", None),
                caps=self.ctx.caps,
                settings=self.ctx.settings,
            )
        else:
            manager = self._scenario_manager or ScenarioManager(
                repo=self.ctx.scenarios_repo,
                registry=SqliteScenarioRegistry(self.ctx.sql),
                git=self.ctx.git,
                paths=self.ctx.paths,
                bus=self.ctx.bus,
                caps=self.ctx.caps,
            )

        def await_runtime(coroutine):
            return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=120)

        def reload_runtime(lock) -> dict[str, Any]:
            component = next(
                (
                    item
                    for item in lock.components
                    if item.kind == kind and item.artifact_id == project_id
                ),
                None,
            )
            if component is None:
                raise ArtifactSubscriptionUpdateError(
                    f"activated WorkspaceLock has no {kind}:{project_id}",
                    code="artifact_runtime_component_missing",
                )
            lock_digest = str(lock.to_dict()["lock_digest"])
            if kind == "scenario":
                sync_receipt = manager.sync_to_yjs(  # type: ignore[union-attr]
                    project_id,
                    webspace_id=webspace_id,
                    emit_event=False,
                )
                self._require_success(sync_receipt, "scenario Yjs synchronization")
                projection = await_runtime(
                    rebuild_webspace_from_sources(
                        webspace_id,
                        action="artifact_subscription_sync",
                        scenario_id=project_id,
                        source_of_truth="workspace_lock",
                    )
                )
                self._require_success(projection, "scenario webspace projection")
                receipt = {
                    "status": "reloaded",
                    "scenario": project_id,
                    "version": component.version,
                    "package_digest": component.digest,
                    "webspace_id": webspace_id,
                    "sync": sync_receipt,
                    "projection": projection,
                }
            else:
                refresh = refresh_skill_runtime(
                    manager,  # type: ignore[arg-type]
                    project_id,
                    webspace_id=webspace_id,
                    source_version=component.version,
                    migrate_runtime=True,
                    ensure_installed=False,
                    require_active_version=True,
                    disable_during_migration=True,
                    operation_id=f"artifact-subscription:{project_id}:{component.digest[-12:]}",
                )
                self._require_success(refresh, "skill runtime refresh")
                handlers = await_runtime(
                    ImportlibSkillsLoader().reload_skill_handlers(
                        self.ctx.paths.skills_dir(),
                        project_id,
                    )
                )
                self._require_success(handlers, "live skill handler reload")
                materialization_cache = invalidate_webspace_materialization_cache(
                    webspace_id,
                    reason=f"artifact_subscription:{project_id}",
                    action="artifact_subscription_sync",
                    source_of_truth="workspace_lock",
                )
                self._require_success(materialization_cache, "webspace cache invalidation")
                projection = await_runtime(
                    rebuild_webspace_projection(
                        webspace_id=webspace_id,
                        action="artifact_subscription_sync",
                        source_of_truth="workspace_lock",
                    )
                )
                self._require_success(projection, "skill webspace projection")
                receipt = {
                    "status": "reloaded",
                    "skill": project_id,
                    "version": component.version,
                    "package_digest": component.digest,
                    "runtime_refresh": refresh,
                    "handler_reload": handlers,
                    "materialization_cache": materialization_cache,
                    "webspace_projection": projection,
                }
            receipts[lock_digest] = receipt
            return receipt

        def health_check(lock) -> dict[str, Any]:
            lock_digest = str(lock.to_dict()["lock_digest"])
            receipt = receipts.get(lock_digest)
            if receipt is None:
                return {
                    "status": "failed",
                    "reason": "runtime_reload_receipt_missing",
                    "lock_digest": lock_digest,
                }
            return {
                "status": "passed",
                "check": f"{kind}_runtime_and_webspace_projection",
                "lock_digest": lock_digest,
                kind: project_id,
                "version": receipt["version"],
            }

        return reload_runtime, health_check

    @staticmethod
    def _require_success(receipt: Any, label: str) -> None:
        if not isinstance(receipt, Mapping):
            return
        status = str(receipt.get("status") or "").strip().lower()
        if receipt.get("ok") is False or status in {"error", "failed", "failure"}:
            reason = receipt.get("error") or receipt.get("reason") or status or "unknown failure"
            raise ArtifactSubscriptionUpdateError(
                f"{label} failed: {reason}",
                code="artifact_runtime_transition_failed",
            )

    def _publish_success(
        self,
        kind: ArtifactKind,
        project_id: str,
        webspace_id: str,
        result: Mapping[str, Any],
    ) -> None:
        bus = getattr(self.ctx, "bus", None)
        if bus is None:
            return
        payload = {
            "name": project_id,
            "webspace_id": webspace_id,
            "source": "artifact_subscription",
            "release": result.get("release"),
            "release_digest": result.get("release_digest"),
        }
        bus_emit(bus, f"{kind}s.updated", payload, "artifact.subscription")
        if kind == "skill":
            bus_emit(
                bus,
                "skills.activated",
                {**payload, "skill_name": project_id, "space": "default"},
                "artifact.subscription",
            )


__all__ = [
    "ARTIFACT_UPDATE_ROUTE_SCHEMA",
    "ArtifactSubscriptionUpdateCoordinator",
    "ArtifactSubscriptionUpdateError",
    "ArtifactUpdateRoute",
]
