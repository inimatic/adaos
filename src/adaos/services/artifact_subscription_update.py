from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Literal

from packaging.version import InvalidVersion, Version

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
RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA = "adaos.runtime_compatibility.exact_update_command.v1"


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

    async def plan_qualified_runtime_update(
        self,
        kind: ArtifactKind,
        project_id: str,
        *,
        qualification: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind one compatibility decision to an exact package update plan."""

        token = str(project_id or "").strip()
        qualified = dict(qualification or {})
        if qualified.get("schema") != "adaos.runtime_compatibility.classification.v1":
            raise ArtifactSubscriptionUpdateError(
                "runtime compatibility classification is required",
                code="runtime_compatibility_classification_required",
            )
        if (
            qualified.get("code") != "eligible_exact_update"
            or qualified.get("evidence_complete") is not True
            or qualified.get("human_decision_required") is not True
            or qualified.get("recommended_action") != "offer_exact_update"
        ):
            raise ArtifactSubscriptionUpdateError(
                "classification does not admit an exact runtime update",
                code="runtime_compatibility_update_not_eligible",
            )
        inspected = await self.inspect(token)
        plan = inspected.get("update_plan")
        if inspected.get("available") is not True or not isinstance(plan, Mapping):
            raise ArtifactSubscriptionUpdateError(
                "the exact subscription update is no longer available",
                code="runtime_compatibility_update_unavailable",
            )
        activation = dict(plan.get("activation") or {})
        component_key = f"{kind}:{token}"
        observed = next(
            (
                dict(item)
                for item in activation.get("observed_components") or ()
                if isinstance(item, Mapping) and str(item.get("key") or "") == component_key
            ),
            None,
        )
        target = next(
            (
                dict(item)
                for item in activation.get("target_components") or ()
                if isinstance(item, Mapping) and str(item.get("key") or "") == component_key
            ),
            None,
        )
        installed_digest = str(qualified.get("installed_package_digest") or "").strip()
        desired_digest = str(qualified.get("desired_package_digest") or "").strip()
        eligible_digest = str(qualified.get("eligible_update_package_digest") or "").strip()
        eligible_from = str(qualified.get("eligible_update_from_package_digest") or "").strip()
        if observed is None or target is None:
            raise ArtifactSubscriptionUpdateError(
                "the reviewed activation plan does not contain the exact runtime component",
                code="runtime_compatibility_component_missing",
                update_plan=plan,
            )
        if (
            str(observed.get("package_digest") or "").strip() != installed_digest
            or eligible_from != installed_digest
            or str(target.get("package_digest") or "").strip() != desired_digest
            or eligible_digest != desired_digest
        ):
            raise ArtifactSubscriptionUpdateError(
                "the reviewed update plan no longer matches the classified package identities",
                code="runtime_compatibility_identity_changed",
                update_plan=plan,
            )
        current_version = str(observed.get("version") or "").strip()
        target_version = str(target.get("version") or "").strip()
        eligible_version = str(qualified.get("eligible_update_version") or "").strip()
        if eligible_version and eligible_version != target_version:
            raise ArtifactSubscriptionUpdateError(
                "the eligible update version differs from the reviewed target",
                code="runtime_compatibility_version_changed",
                update_plan=plan,
            )
        try:
            if Version(target_version) < Version(current_version):
                raise ArtifactSubscriptionUpdateError(
                    "runtime compatibility recovery cannot downgrade the component",
                    code="runtime_compatibility_downgrade_rejected",
                    update_plan=plan,
                )
        except InvalidVersion as exc:
            raise ArtifactSubscriptionUpdateError(
                "exact runtime update requires comparable release versions",
                code="runtime_compatibility_version_invalid",
                update_plan=plan,
            ) from exc
        command: dict[str, Any] = {
            "schema": RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA,
            "kind": kind,
            "project_id": token,
            "from": {
                "version": current_version,
                "package_digest": installed_digest,
            },
            "target": {
                "version": target_version,
                "package_digest": desired_digest,
            },
            "plan_digest": str(plan.get("plan_digest") or "").strip(),
            "consequences": {
                "component_changes": dict(activation.get("component_changes") or {}),
                "permissions": dict(activation.get("permissions") or {}),
                "migrations": dict(activation.get("migrations") or {}),
                "rollback": dict(activation.get("rollback") or {}),
                "warnings": list(activation.get("warnings") or ())[:20],
            },
        }
        if not command["plan_digest"]:
            raise ArtifactSubscriptionUpdateError(
                "exact runtime update has no reviewed plan digest",
                code="runtime_compatibility_plan_digest_missing",
            )
        canonical = json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        command["command_digest"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return command

    async def execute_qualified_runtime_update(
        self,
        command: Mapping[str, Any],
        *,
        qualification: Mapping[str, Any],
        permission_decision: bool | Mapping[str, Any],
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Revalidate and execute the exact command that a person reviewed."""

        supplied = dict(command or {})
        if supplied.get("schema") != RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA:
            raise ArtifactSubscriptionUpdateError(
                "unsupported runtime compatibility update command",
                code="runtime_compatibility_command_invalid",
            )
        kind = str(supplied.get("kind") or "").strip()
        if kind not in {"skill", "scenario"}:
            raise ArtifactSubscriptionUpdateError(
                "runtime compatibility update kind is invalid",
                code="runtime_compatibility_command_invalid",
            )
        project_id = str(supplied.get("project_id") or "").strip()
        current = await self.plan_qualified_runtime_update(
            kind,  # type: ignore[arg-type]
            project_id,
            qualification=qualification,
        )
        if current != supplied:
            raise ArtifactSubscriptionUpdateError(
                "runtime compatibility update changed after review",
                code="runtime_compatibility_command_stale",
                update_plan=current,
            )
        return await self.update(
            kind,  # type: ignore[arg-type]
            project_id,
            expected_plan_digest=str(supplied["plan_digest"]),
            permission_decision=permission_decision,
            idempotency_key=f"runtime-compatibility:{supplied['command_digest']}",
            webspace_id=webspace_id,
        )

    async def publish_qualified_runtime_update_interaction(
        self,
        kind: ArtifactKind,
        project_id: str,
        *,
        qualification: Mapping[str, Any],
        conversation_id: str,
        owner: str,
        expires_at: str,
        interaction_id: str | None = None,
        thread_id: str | None = None,
        task_ref: Mapping[str, Any] | None = None,
        webspace_id: str | None = None,
        channel_id: str = "general",
        route_id: str = "dialog",
        capability_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Present one exact classified update through canonical Interaction."""

        deadline = str(expires_at or "").strip()
        if not deadline:
            raise ArtifactSubscriptionUpdateError(
                "an exact runtime update decision requires an expiry",
                code="runtime_compatibility_update_expiry_required",
            )
        command = await self.plan_qualified_runtime_update(
            kind,
            project_id,
            qualification=qualification,
        )
        target = dict(command["target"])
        current = dict(command["from"])
        effect_ref = {
            "kind": "artifact_subscription_update",
            "id": f"{kind}:{project_id}",
            "digest": str(command["command_digest"]),
        }
        specification = {
            "interaction_id": str(interaction_id or "").strip() or None,
            "prompt": (
                f"Update {kind} {project_id} from {current.get('version')} "
                f"to {target.get('version')}?"
            ),
            "input_spec": {
                "kind": "choice",
                "required_fields": [],
                "choices": [
                    {
                        "value": str(command["command_digest"]),
                        "label": f"Update to {target.get('version')}",
                        "description": "Apply only the reviewed package digest and plan.",
                    },
                    {
                        "value": "refuse",
                        "label": "Do not update",
                        "description": "Keep the current admitted package.",
                    },
                ],
                "sensitive": False,
            },
            "actions": [
                {
                    "action_id": "approve_exact_update",
                    "label": f"Update to {target.get('version')}",
                    "command": "runtime_compatibility.execute_exact_update",
                    "value": str(command["command_digest"]),
                    "risk": "registry",
                    "confirmation_required": True,
                    "target_ref": effect_ref,
                    "semantics": {
                        "preset": "custom",
                        "effect_class": "artifact_update",
                        "operation": "execute_exact_runtime_update",
                        "executor": "artifact_subscription_update",
                        "mutates_domain": True,
                        "records_consent": True,
                        "terminal": True,
                        "effect_ref": effect_ref,
                        "assertion_required": True,
                    },
                },
                {
                    "action_id": "refuse",
                    "label": "Do not update",
                    "command": "conversation.interaction.refuse",
                    "value": "refuse",
                    "risk": "read",
                    "confirmation_required": False,
                    "preset": "refuse",
                },
            ],
            "required_capabilities": ["buttons", "trusted_interface"],
            "optional_capabilities": ["rich_view", "deep_link", "progress"],
            "fallbacks": ["unsupported"],
            "task_ref": dict(task_ref) if isinstance(task_ref, Mapping) else None,
            "expires_at": deadline,
            "metadata": {
                "pending_action_kind": "runtime_compatibility_exact_update",
                "runtime_update_command": command,
                "runtime_compatibility_qualification": dict(qualification),
                "decision_context": {
                    "kind": kind,
                    "project_id": str(project_id or "").strip(),
                    "from": current,
                    "target": target,
                    "plan_digest": command["plan_digest"],
                    "command_digest": command["command_digest"],
                    "consequences": command["consequences"],
                },
            },
        }
        from adaos.sdk import chat

        return chat.request(
            specification,
            conversation_id=str(conversation_id or "").strip(),
            owner=str(owner or "").strip(),
            webspace_id=webspace_id,
            channel_id=channel_id,
            route_id=route_id,
            thread_id=thread_id,
            capability_profile=capability_profile,
            bus=getattr(self.ctx, "bus", None),
        )

    async def execute_qualified_runtime_update_interaction(
        self,
        interaction_id: str,
        response_id: str,
        *,
        webspace_id: str | None = None,
        lease_owner: str = "artifact_subscription_update",
    ) -> dict[str, Any]:
        """Consume one durable decision and project the exact update outcome."""

        from adaos.services import conversation_interactions, conversation_store

        identifier = str(interaction_id or "").strip()
        response_identifier = str(response_id or "").strip()
        interaction = conversation_store.get_interaction(identifier)
        response = conversation_store.get_interaction_response(response_identifier)
        dispatch = conversation_store.get_interaction_dispatch(response_id=response_identifier)
        if interaction is None or response is None or dispatch is None:
            raise ArtifactSubscriptionUpdateError(
                "the exact update decision is unavailable",
                code="runtime_compatibility_interaction_unavailable",
            )
        if str(response.get("interaction_id") or "") != identifier:
            raise ArtifactSubscriptionUpdateError(
                "the exact update response belongs to another interaction",
                code="runtime_compatibility_response_mismatch",
            )
        existing_status = str(dispatch.get("status") or "")
        if existing_status in {"succeeded", "failed", "cancelled", "rejected", "outcome_unknown"}:
            return {
                "ok": existing_status == "succeeded",
                "duplicate": True,
                "interaction": interaction,
                "dispatch": dispatch,
                "outcome": dict(dispatch.get("outcome") or {}),
            }
        metadata = dict(interaction.get("metadata") or {})
        command = dict(metadata.get("runtime_update_command") or {})
        qualification = dict(metadata.get("runtime_compatibility_qualification") or {})
        consumed = dict(response.get("consumed_command") or {})
        action_id = str(consumed.get("action_id") or "").strip()
        if action_id not in {"approve_exact_update", "refuse"}:
            raise ArtifactSubscriptionUpdateError(
                "the exact update response has no admitted action",
                code="runtime_compatibility_response_action_invalid",
            )
        if action_id == "approve_exact_update" and (
            str(consumed.get("value") or "") != str(command.get("command_digest") or "")
            or str(consumed.get("command") or "")
            != "runtime_compatibility.execute_exact_update"
        ):
            raise ArtifactSubscriptionUpdateError(
                "the exact update response changed after presentation",
                code="runtime_compatibility_response_command_stale",
            )
        if str(interaction.get("status") or "") == "answered":
            interaction = conversation_interactions.accept_response(
                identifier,
                response_identifier,
                expected_generation=int(interaction.get("generation") or 0),
            )
        elif str(interaction.get("status") or "") != "accepted":
            raise ArtifactSubscriptionUpdateError(
                "the exact update interaction is not executable",
                code="runtime_compatibility_interaction_state_invalid",
            )
        claimed = conversation_store.claim_interaction_dispatch(
            response_identifier,
            lease_owner=lease_owner,
            reconciliation_contract="adaos.artifact_subscription_update.dispatch_replay.v1",
        )
        if claimed is None:
            raise ArtifactSubscriptionUpdateError(
                "the exact update dispatch is unavailable",
                code="runtime_compatibility_dispatch_unavailable",
            )
        if str(claimed.get("status") or "") != "dispatching":
            return {
                "ok": str(claimed.get("status") or "") == "succeeded",
                "duplicate": True,
                "interaction": interaction,
                "dispatch": claimed,
                "outcome": dict(claimed.get("outcome") or {}),
            }
        if action_id == "refuse":
            completed = conversation_store.complete_interaction_dispatch(
                str(claimed["dispatch_id"]),
                lease_owner=lease_owner,
                status="cancelled",
                outcome={"reason_code": "user_refused_exact_update"},
            )
            interaction = conversation_interactions.transition_interaction(
                identifier,
                "cancel",
                expected_generation=int(interaction.get("generation") or 0),
                reason="user_refused_exact_update",
            )
            return {
                "ok": False,
                "refused": True,
                "interaction": interaction,
                "dispatch": completed,
            }

        try:
            result = await self.execute_qualified_runtime_update(
                command,
                qualification=qualification,
                permission_decision={
                    "approved": True,
                    "interaction_id": identifier,
                    "response_id": response_identifier,
                    "actor_id": response.get("actor_id"),
                    "assurance_receipt": dict(response.get("assurance_receipt") or {}),
                },
                webspace_id=webspace_id,
            )
            safe_result = {
                key: result.get(key)
                for key in (
                    "ok",
                    "updated",
                    "release",
                    "release_digest",
                    "mode",
                    "reviewed_plan_digest",
                )
                if result.get(key) is not None
            }
            result_digest = "sha256:" + hashlib.sha256(
                json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
                    "utf-8"
                )
            ).hexdigest()
            semantics = dict(consumed.get("semantics") or {})
            outcome = {
                "result": safe_result,
                "result_digest": result_digest,
                "effect_assertion": {
                    "schema": "adaos.conversation.action_effect_assertion.v1",
                    "preset": "custom",
                    "operation": "execute_exact_runtime_update",
                    "observed": True,
                    "domain_mutated": True,
                    "effect_ref": dict(semantics.get("effect_ref") or {}),
                },
            }
            completed = conversation_store.complete_interaction_dispatch(
                str(claimed["dispatch_id"]),
                lease_owner=lease_owner,
                status="succeeded",
                outcome=outcome,
            )
            interaction = conversation_interactions.transition_interaction(
                identifier,
                "complete",
                expected_generation=int(interaction.get("generation") or 0),
                reason="exact_runtime_update_succeeded",
            )
            return {
                "ok": True,
                "interaction": interaction,
                "dispatch": completed,
                "outcome": outcome,
            }
        except Exception as exc:
            conversation_store.complete_interaction_dispatch(
                str(claimed["dispatch_id"]),
                lease_owner=lease_owner,
                status="failed",
                outcome={
                    "reason_code": getattr(exc, "code", "runtime_compatibility_update_failed"),
                    "error_type": type(exc).__name__,
                },
            )
            raise

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
                    emit_activation=False,
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


async def reconcile_qualified_runtime_update_interaction_dispatches(
    ctx: AgentContext,
    *,
    now_epoch: float | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Resume only exact update dispatches with an admitted replay contract."""

    from adaos.services import conversation_store

    observed_epoch = float(time.time() if now_epoch is None else now_epoch)
    safe_limit = max(1, min(int(limit or 100), 500))
    records = conversation_store.list_interaction_dispatches(
        statuses=("pending", "dispatching"),
        limit=safe_limit + 1,
    )
    has_more = len(records) > safe_limit
    coordinator = ArtifactSubscriptionUpdateCoordinator(ctx)
    executed: list[str] = []
    waiting: list[str] = []
    errors: list[dict[str, str]] = []
    for dispatch in records[:safe_limit]:
        command = dict(dispatch.get("command") or {})
        semantics = dict(command.get("semantics") or {})
        if (
            str(semantics.get("executor") or "") != "artifact_subscription_update"
            or str(command.get("command") or "")
            != "runtime_compatibility.execute_exact_update"
        ):
            continue
        dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
        if (
            str(dispatch.get("status") or "") == "dispatching"
            and float(dispatch.get("lease_until") or 0.0) > observed_epoch
        ):
            waiting.append(dispatch_id)
            continue
        interaction_id = str(dispatch.get("interaction_id") or "").strip()
        response_id = str(dispatch.get("response_id") or "").strip()
        response = conversation_store.get_interaction_response(response_id) or {}
        response_meta = dict(response.get("metadata") or {})
        try:
            await coordinator.execute_qualified_runtime_update_interaction(
                interaction_id,
                response_id,
                webspace_id=str(response_meta.get("webspace_id") or "").strip() or None,
                lease_owner="reconciler:artifact-subscription-update",
            )
        except Exception as exc:
            errors.append(
                {
                    "dispatch_id": dispatch_id,
                    "reason_code": getattr(
                        exc, "code", "artifact_subscription_update_reconciliation_failed"
                    ),
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
            continue
        executed.append(dispatch_id)
    return {
        "schema": "adaos.artifact_subscription_update.dispatch_reconciliation.v1",
        "observed_epoch": observed_epoch,
        "scanned_total": min(len(records), safe_limit),
        "executed_dispatch_ids": executed,
        "waiting_dispatch_ids": waiting,
        "errors": errors,
        "complete": not has_more,
    }


__all__ = [
    "ARTIFACT_UPDATE_ROUTE_SCHEMA",
    "RUNTIME_COMPATIBILITY_UPDATE_COMMAND_SCHEMA",
    "ArtifactSubscriptionUpdateCoordinator",
    "ArtifactSubscriptionUpdateError",
    "ArtifactUpdateRoute",
    "reconcile_qualified_runtime_update_interaction_dispatches",
]
