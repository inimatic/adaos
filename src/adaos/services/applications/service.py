from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Callable, Mapping

from adaos.domain.application import (
    Application,
    ApplicationContractError,
    ApplicationInstallation,
    ApplicationOperation,
    ApplicationRelease,
    ApplicationSubscription,
    RuntimeSelection,
    utc_now,
)
from adaos.domain.artifact_release import canonical_payload_digest

from .store import ApplicationStore


class ApplicationServiceError(RuntimeError):
    pass


class ApplicationPlanConflict(ApplicationServiceError):
    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        super().__init__("Application plan contains active component conflicts")
        self.conflicts = conflicts


ApplicationExecutor = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class ApplicationService:
    """Product-level Application lifecycle over Artifact/Deployment evidence."""

    def __init__(
        self,
        store: ApplicationStore,
        *,
        executor: ApplicationExecutor | None = None,
    ) -> None:
        self.store = store
        self.executor = executor

    def register(self, application: Application, *, expected_revision: int = 0) -> Application:
        return self.store.save_application(application, expected_revision=expected_revision)

    def register_release(self, release: ApplicationRelease) -> ApplicationRelease:
        return self.store.put_release(release)

    def list_releases(self, application_id: str) -> list[dict[str, Any]]:
        channels = self.store.get_channels(application_id).get("channels") or {}
        return [
            {
                **release.to_dict(),
                "channels": [name for name, digest in channels.items() if digest == release.release_digest],
            }
            for release in self.store.list_releases(application_id)
        ]

    def move_channel(
        self,
        application_id: str,
        channel: str,
        release_digest: str,
        *,
        publisher_ref: str,
        expected_release_digest: str | None,
    ) -> dict[str, Any]:
        application = self.store.get_application(application_id)
        if application.publisher_ref != publisher_ref:
            raise ApplicationServiceError("only the Application publisher may move channels")
        release = self.store.get_release(application_id, release_digest)
        if release.publisher_ref != publisher_ref:
            raise ApplicationServiceError("release publisher does not own Application")
        channels = self.store.get_channels(application_id).get("channels") or {}
        channel_id = str(channel or "").strip().lower()
        if channel_id == "prerelease" and not channels.get("stable"):
            raise ApplicationServiceError("public prerelease requires an existing stable release")
        if channel_id == "stable" and channels.get("stable"):
            if channels.get("prerelease") != release_digest:
                raise ApplicationServiceError("later stable must promote the exact current prerelease digest")
        updated = self.store.set_channel(
            application_id,
            channel_id,
            release_digest,
            expected_release_digest=expected_release_digest,
        )
        if channel_id == "stable" and updated.get("channels", {}).get("prerelease") == release_digest:
            updated = self.store.set_channel(
                application_id,
                "prerelease",
                None,
                expected_release_digest=release_digest,
            )
        return updated

    def set_subscription(
        self,
        application_id: str,
        *,
        update_track: str,
        update_policy: str,
        paused: bool,
        expected_revision: int,
        observed_release_digest: str | None = None,
        pinned_release_digest: str | None = None,
    ) -> ApplicationSubscription:
        try:
            current = self.store.get_subscription(application_id)
        except FileNotFoundError:
            current = None
        if (current.revision if current else 0) != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(
                expected=expected_revision, observed=current.revision if current else 0
            )
        value = ApplicationSubscription(
            application_id=application_id,
            update_track=update_track,  # type: ignore[arg-type]
            update_policy=update_policy,  # type: ignore[arg-type]
            observed_release_digest=observed_release_digest,
            pinned_release_digest=pinned_release_digest,
            paused=paused,
            revision=expected_revision + 1,
        )
        return self.store.save_subscription(value, expected_revision=expected_revision)

    def effective_release(self, application_id: str) -> dict[str, Any]:
        channels = dict(self.store.get_channels(application_id).get("channels") or {})
        try:
            subscription = self.store.get_subscription(application_id)
        except FileNotFoundError:
            subscription = ApplicationSubscription(
                application_id=application_id,
                update_track="stable",
                update_policy="notify",
                revision=1,
            )
        if subscription.update_policy == "pinned" and subscription.pinned_release_digest:
            digest = subscription.pinned_release_digest
            effective_channel = "pinned"
        elif subscription.update_track == "prerelease" and channels.get("prerelease"):
            digest = str(channels["prerelease"])
            effective_channel = "prerelease"
        else:
            digest = str(channels.get("stable") or "")
            effective_channel = "stable"
        if not digest:
            return {
                "application_id": application_id,
                "update_track": subscription.update_track,
                "effective_channel": None,
                "release_digest": None,
                "reason": "channel_unavailable",
            }
        return {
            "application_id": application_id,
            "update_track": subscription.update_track,
            "effective_channel": effective_channel,
            "release_digest": digest,
            "reason": "resolved",
            "release": self.store.get_release(application_id, digest).to_dict(),
        }

    def select_runtime(
        self,
        *,
        webspace_id: str,
        application_id: str,
        source: str,
        release_digest: str,
        runtime_root_ref: str,
        expected_revision: int,
    ) -> RuntimeSelection:
        self.store.get_release(application_id, release_digest)
        try:
            current = self.store.get_runtime_selection(webspace_id, application_id)
        except FileNotFoundError:
            current = None
        observed_revision = current.revision if current is not None else 0
        if observed_revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(
                expected=expected_revision, observed=observed_revision
            )
        if current is None:
            value = RuntimeSelection(
                webspace_id=webspace_id,
                application_id=application_id,
                source=source,  # type: ignore[arg-type]
                release_digest=release_digest,
                runtime_root_ref=runtime_root_ref,
                revision=1,
            )
        else:
            value = current.advance(
                expected_revision=expected_revision,
                source=source,
                release_digest=release_digest,
                runtime_root_ref=runtime_root_ref,
            )
        return self.store.save_runtime_selection(value, expected_revision=expected_revision)

    def reconcile_runtime_selection(
        self,
        webspace_id: str,
        application_id: str,
        *,
        runtime_root_exists: Callable[[str], bool],
        rematerialize: Callable[[RuntimeSelection], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        selection = self.store.get_runtime_selection(webspace_id, application_id)
        if runtime_root_exists(selection.runtime_root_ref):
            return {"status": "ready", "selection": selection.to_dict(), "repaired": False}
        if rematerialize is None:
            return {
                "status": "missing_runtime_root",
                "selection": selection.to_dict(),
                "repaired": False,
                "recovery_reason": "immutable_release_rematerialization_required",
            }
        result = dict(rematerialize(selection))
        if not runtime_root_exists(selection.runtime_root_ref):
            return {
                "status": "recovery_failed",
                "selection": selection.to_dict(),
                "repaired": False,
                "result": result,
            }
        return {"status": "ready", "selection": selection.to_dict(), "repaired": True, "result": result}

    def component_references(self) -> dict[str, Any]:
        references: dict[str, list[dict[str, Any]]] = {}
        for installation in self.store.list_installations():
            if installation.status == "removed":
                continue
            for component in installation.component_refs:
                key = str(component["component_ref"])
                references.setdefault(key, []).append(
                    {
                        "application_id": installation.application_id,
                        "installation_id": installation.installation_id,
                        "package_digest": component["package_digest"],
                        "lifecycle": component["lifecycle"],
                        "active_runtime_leases": list(installation.active_runtime_leases),
                        "rollback_holds": list(installation.rollback_holds),
                        "uncertain_operation_refs": list(installation.uncertain_operation_refs),
                    }
                )
        return {
            "schema": "adaos.application.component_reference_index.v1",
            "components": {key: references[key] for key in sorted(references)},
        }

    def _release_components(self, release: ApplicationRelease) -> list[dict[str, Any]]:
        lifecycle_by_ref: dict[str, str] = {}
        composition = release.project_release.composition_lock
        if composition is not None:
            lifecycle_by_ref = {member.ref: member.lifecycle for member in composition.members}
        return [
            {
                "component_ref": component.key,
                "package_digest": component.digest,
                "lifecycle": lifecycle_by_ref.get(component.key, "bound"),
            }
            for component in release.project_release.components
        ]

    def _component_conflicts(
        self,
        application_id: str,
        components: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        target = {item["component_ref"]: item["package_digest"] for item in components}
        conflicts: list[dict[str, Any]] = []
        for installation in self.store.list_installations():
            if installation.application_id == application_id or installation.status == "removed":
                continue
            for current in installation.component_refs:
                requested = target.get(current["component_ref"])
                if requested and requested != current["package_digest"]:
                    conflicts.append(
                        {
                            "component_ref": current["component_ref"],
                            "requested_digest": requested,
                            "active_digest": current["package_digest"],
                            "active_application_id": installation.application_id,
                            "reason": "side_by_side_component_versions_not_supported",
                        }
                    )
        return sorted(conflicts, key=lambda item: (item["component_ref"], item["active_application_id"]))

    @staticmethod
    def _compatibility_summary(release: ApplicationRelease) -> dict[str, Any]:
        composition = release.project_release.composition_lock
        compatibility = dict(composition.compatibility) if composition is not None else {}
        return {
            "platform": compatibility,
            "permissions": list(release.project_release.permissions),
            "migration": {
                "required": bool(release.project_release.migrations),
                "count": len(release.project_release.migrations),
                "rollback_mode": "snapshot_restore",
            },
            "contract_locks_present": release.project_release.contract_locks_present,
            "validation_evidence_count": len(release.project_release.validation_evidence),
        }

    def plan_operation(
        self,
        application_id: str,
        kind: str,
        *,
        actor_ref: str,
        subnet_ref: str,
        idempotency_key: str,
        expected_revision: int,
        release_digest: str | None = None,
        data_policy: str = "retain",
        update_track: str | None = None,
        update_policy: str = "notify",
        paused: bool = False,
        pinned_release_digest: str | None = None,
    ) -> ApplicationOperation:
        operation_kind = str(kind or "").strip().lower()
        if operation_kind not in {"install", "update", "remove", "select_track"}:
            raise ApplicationServiceError(
                "Core operation kind must be install, update, remove, or select_track"
            )
        if data_policy not in {"retain", "delete", "snapshot_then_delete"}:
            raise ApplicationServiceError("data_policy is invalid")
        application = self.store.get_application(application_id)
        if operation_kind == "select_track":
            try:
                current_subscription = self.store.get_subscription(application_id)
            except FileNotFoundError:
                current_subscription = None
            current = None
            observed_revision = current_subscription.revision if current_subscription is not None else 0
        else:
            try:
                current = self.store.get_installation(application_id)
            except FileNotFoundError:
                current = None
            observed_revision = current.revision if current is not None else 0
        if observed_revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(expected=expected_revision, observed=observed_revision)
        if operation_kind == "install" and current is not None and current.status != "removed":
            raise ApplicationServiceError("Application is already installed")
        if operation_kind in {"update", "remove"} and (current is None or current.status == "removed"):
            raise ApplicationServiceError(f"Application must be installed before {operation_kind}")
        release: ApplicationRelease | None = None
        components: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        compatibility: dict[str, Any] = {}
        if operation_kind in {"install", "update"}:
            if not release_digest:
                effective = self.effective_release(application_id)
                release_digest = str(effective.get("release_digest") or "")
            if not release_digest:
                raise ApplicationServiceError("no exact release is available for operation")
            release = self.store.get_release(application_id, release_digest)
            components = self._release_components(release)
            conflicts = self._component_conflicts(application_id, components)
            compatibility = self._compatibility_summary(release)
        removal = self.simulate_removal(application_id, data_policy=data_policy) if operation_kind == "remove" else None
        subscription_change = None
        if operation_kind == "select_track":
            subscription_change = ApplicationSubscription(
                application_id=application_id,
                update_track=str(update_track or "stable"),  # type: ignore[arg-type]
                update_policy=update_policy,  # type: ignore[arg-type]
                observed_release_digest=(
                    current_subscription.observed_release_digest
                    if current_subscription is not None
                    else None
                ),
                pinned_release_digest=pinned_release_digest,
                paused=paused,
                revision=expected_revision + 1,
            ).to_dict()
        snapshot = {
            "required": operation_kind == "update",
            "mode": "snapshot_restore" if operation_kind == "update" else "none",
            "consistency_boundary": "artifact_activation_transaction" if operation_kind == "update" else None,
            "source_release_digest": current.installed_release_digest if current is not None and operation_kind == "update" else None,
            "retention": "until_successor_release_verified" if operation_kind == "update" else None,
        }
        plan = {
            "schema": "adaos.application.operation_plan.v1",
            "application_id": application_id,
            "legacy_project_id": application.legacy_project_id,
            "kind": operation_kind,
            "expected_revision": expected_revision,
            "release_digest": release_digest,
            "components": components,
            "conflicts": conflicts,
            "compatibility": compatibility,
            "snapshot": snapshot,
            "removal": removal,
            "data_policy": data_policy,
            "subscription_change": subscription_change,
        }
        plan_digest = canonical_payload_digest(plan)
        identity = hashlib.sha256(f"{plan_digest}:{idempotency_key}".encode("utf-8")).hexdigest()[:32]
        operation = ApplicationOperation(
            operation_id=f"appop.{identity}",
            application_id=application_id,
            kind=operation_kind,
            status="planned",
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            plan_digest=plan_digest,
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
            revision=1,
            plan=plan,
        )
        return self.store.put_operation(operation)

    def _transition_operation(
        self,
        operation: ApplicationOperation,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        recovery_reason: str | None = None,
    ) -> ApplicationOperation:
        updated = replace(
            operation,
            status=status,
            result=dict(result or operation.result),
            recovery_reason=recovery_reason,
            revision=operation.revision + 1,
            updated_at=utc_now(),
        )
        return self.store.save_operation(updated, expected_revision=operation.revision)

    def apply_operation(
        self,
        operation_id: str,
        *,
        plan_digest: str,
        idempotency_key: str,
    ) -> ApplicationOperation:
        operation = self.store.get_operation(operation_id)
        if operation.plan_digest != plan_digest or operation.idempotency_key != idempotency_key:
            raise ApplicationServiceError("reviewed plan or idempotency identity does not match")
        if operation.status == "succeeded":
            return operation
        if operation.status != "planned":
            raise ApplicationServiceError(f"operation cannot apply from {operation.status}")
        conflicts = list(operation.plan.get("conflicts") or [])
        if conflicts:
            raise ApplicationPlanConflict(conflicts)
        if operation.kind == "select_track":
            raw_subscription = operation.plan.get("subscription_change")
            if not isinstance(raw_subscription, Mapping):
                raise ApplicationServiceError("select_track plan is missing subscription state")
            subscription = ApplicationSubscription.from_mapping(raw_subscription)
            self.store.save_subscription(
                subscription, expected_revision=operation.expected_revision
            )
            return self._transition_operation(
                operation,
                "succeeded",
                result={"subscription": subscription.to_dict()},
            )
        if self.executor is None:
            raise ApplicationServiceError("Application operation executor is not configured")
        applying = self._transition_operation(operation, "applying")
        try:
            result = dict(self.executor(operation.plan))
        except Exception as exc:
            self._transition_operation(
                applying,
                "unknown",
                recovery_reason=f"executor_outcome_unknown:{type(exc).__name__}:{exc}",
            )
            raise
        snapshot_ref = None
        snapshot_plan = operation.plan.get("snapshot")
        if isinstance(snapshot_plan, Mapping) and bool(snapshot_plan.get("required")):
            receipt = result.get("snapshot_receipt")
            expected_source = str(snapshot_plan.get("source_release_digest") or "")
            if (
                not isinstance(receipt, Mapping)
                or not str(receipt.get("snapshot_ref") or "").strip()
                or str(receipt.get("source_release_digest") or "") != expected_source
                or str(receipt.get("consistency_boundary") or "")
                != str(snapshot_plan.get("consistency_boundary") or "")
            ):
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="required_snapshot_receipt_invalid",
                )
            snapshot_ref = str(receipt["snapshot_ref"])
            self.store.put_snapshot_receipt(snapshot_ref, receipt)
        if not bool(result.get("ok")) or str(result.get("status") or "") not in {"succeeded", "active", "removed"}:
            if snapshot_ref is not None:
                restore = result.get("restore_receipt")
                if (
                    not isinstance(restore, Mapping)
                    or str(restore.get("snapshot_ref") or "") != snapshot_ref
                    or str(restore.get("restored_release_digest") or "")
                    != str((snapshot_plan or {}).get("source_release_digest") or "")
                    or str(restore.get("status") or "") != "restored"
                ):
                    return self._transition_operation(
                        applying,
                        "unknown",
                        result=result,
                        recovery_reason="snapshot_restore_outcome_unknown",
                    )
                self.store.put_snapshot_receipt(
                    f"restore:{snapshot_ref}",
                    restore,
                )
            return self._transition_operation(
                applying,
                "failed",
                result=result,
                recovery_reason=str(result.get("reason") or "executor_rejected"),
            )
        current: ApplicationInstallation | None
        try:
            current = self.store.get_installation(operation.application_id)
        except FileNotFoundError:
            current = None
        observed_revision = current.revision if current is not None else 0
        if observed_revision != operation.expected_revision:
            return self._transition_operation(
                applying,
                "unknown",
                result=result,
                recovery_reason="installation_revision_changed_after_execution",
            )
        if operation.kind in {"install", "update"}:
            installation = ApplicationInstallation(
                installation_id=current.installation_id if current else f"installation:{operation.application_id}",
                application_id=operation.application_id,
                installed_release_digest=str(operation.plan["release_digest"]),
                component_refs=tuple(operation.plan.get("components") or ()),
                data_policy=str(operation.plan.get("data_policy") or "retain"),
                status="active",
                revision=observed_revision + 1,
                legacy_deployment_id=current.legacy_deployment_id if current else None,
                snapshot_ref=snapshot_ref,
                created_at=current.created_at if current else utc_now(),
                updated_at=utc_now(),
            )
        else:
            assert current is not None
            installation = replace(
                current,
                status="removed",
                data_policy=str(operation.plan.get("data_policy") or current.data_policy),
                revision=current.revision + 1,
                updated_at=utc_now(),
            )
        self.store.save_installation(installation, expected_revision=observed_revision)
        return self._transition_operation(applying, "succeeded", result={**result, "installation": installation.to_dict()})

    def reconcile_operation(
        self,
        operation_id: str,
        *,
        observer: Callable[[ApplicationOperation], Mapping[str, Any]],
    ) -> ApplicationOperation:
        operation = self.store.get_operation(operation_id)
        if operation.status not in {"unknown", "applying", "reconciling"}:
            return operation
        reconciling = operation if operation.status == "reconciling" else self._transition_operation(operation, "reconciling")
        observed = dict(observer(reconciling))
        status = str(observed.get("status") or "unknown")
        if status not in {"succeeded", "failed", "unknown"}:
            status = "unknown"
        return self._transition_operation(
            reconciling,
            status,
            result=observed,
            recovery_reason=None if status != "unknown" else str(observed.get("reason") or "authoritative_state_inconclusive"),
        )

    def simulate_removal(self, application_id: str, *, data_policy: str = "retain") -> dict[str, Any]:
        installation = self.store.get_installation(application_id)
        index = self.component_references()["components"]
        components: list[dict[str, Any]] = []
        for component in installation.component_refs:
            others = [
                item
                for item in index.get(component["component_ref"], [])
                if item["application_id"] != application_id
            ]
            holds = [
                hold
                for item in index.get(component["component_ref"], [])
                for field_name in ("active_runtime_leases", "rollback_holds", "uncertain_operation_refs")
                for hold in item.get(field_name, [])
            ]
            components.append(
                {
                    "component_ref": component["component_ref"],
                    "package_digest": component["package_digest"],
                    "remove_package": not others and not holds,
                    "retained_by_applications": sorted({item["application_id"] for item in others}),
                    "retained_by_holds": sorted(set(holds)),
                }
            )
        return {
            "application_id": application_id,
            "installation_revision": installation.revision,
            "components": components,
            "data_outcome": data_policy,
        }

    def list_models(self, *, installed_only: bool = False) -> list[dict[str, Any]]:
        installations = {item.application_id: item for item in self.store.list_installations() if item.status != "removed"}
        subscriptions = {item.application_id: item for item in self.store.list_subscriptions()}
        operations: dict[str, ApplicationOperation] = {}
        for operation in self.store.list_operations():
            operations.setdefault(operation.application_id, operation)
        models: list[dict[str, Any]] = []
        for application in self.store.list_applications():
            installation = installations.get(application.application_id)
            if installed_only and installation is None:
                continue
            channels = dict(self.store.get_channels(application.application_id).get("channels") or {})
            subscription = subscriptions.get(application.application_id)
            effective = self.effective_release(application.application_id)
            update_available = bool(
                installation
                and effective.get("release_digest")
                and effective["release_digest"] != installation.installed_release_digest
            )
            models.append(
                {
                    "application": application.to_dict(),
                    "installed": installation is not None,
                    "installation": installation.to_dict() if installation else None,
                    "available": bool(channels.get("stable")) or application.visibility != "public",
                    "update_available": update_available,
                    "pinned": bool(subscription and subscription.update_policy == "pinned"),
                    "prerelease_following": bool(subscription and subscription.update_track == "prerelease"),
                    "retired": application.lifecycle in {"retired", "archived"},
                    "subscription": subscription.to_dict() if subscription else None,
                    "channels": channels,
                    "effective_release": effective,
                    "operation": operations.get(application.application_id).to_dict() if operations.get(application.application_id) else None,
                }
            )
        return models


__all__ = [
    "ApplicationExecutor",
    "ApplicationPlanConflict",
    "ApplicationService",
    "ApplicationServiceError",
]
