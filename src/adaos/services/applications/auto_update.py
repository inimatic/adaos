"""Fail-closed automatic Application updates.

The public registry may advance independently from a node.  This service uses
the ordinary reviewed Application plan/apply protocol and only applies plans
that do not expand authority or require a data migration.  Every decision is
persisted so an update notification is observable even when nothing was
applied.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adaos.domain.application import utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.applications.service import (
    APPLICATION_OPERATION_PLANNER_REVISION,
    ApplicationService,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


AUTO_UPDATE_RUN_SCHEMA = "adaos.application.auto_update_run.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def automatic_update_blockers(plan: Mapping[str, Any]) -> list[str]:
    """Return reasons that require a human-reviewed update instead."""

    blockers: list[str] = []
    conflicts = plan.get("conflicts")
    if isinstance(conflicts, list) and conflicts:
        blockers.append("component_conflicts")

    permission_review = plan.get("permission_review")
    if not isinstance(permission_review, Mapping):
        blockers.append("permission_review_unavailable")
    elif bool(permission_review.get("approval_required")):
        blockers.append("permission_approval_required")

    compatibility = plan.get("compatibility")
    if not isinstance(compatibility, Mapping):
        blockers.append("compatibility_unavailable")
    else:
        migration = compatibility.get("migration")
        if not isinstance(migration, Mapping):
            blockers.append("migration_assessment_unavailable")
        elif bool(migration.get("required")):
            blockers.append("migration_required")

    return list(dict.fromkeys(blockers))


def _retryable_terminal_operation(operation: Any) -> bool:
    """Return whether a prior exact update can safely converge in a new operation."""

    status = _text(getattr(operation, "status", "planned"))
    if status == "failed":
        return True
    if status != "unknown":
        return False
    result = getattr(operation, "result", None)
    if not isinstance(result, Mapping):
        return False
    deployment = result.get("deployment_operation")
    if not isinstance(deployment, Mapping):
        return False
    error = deployment.get("error")
    return (
        _text(deployment.get("state")) == "partial"
        and not bool(deployment.get("uncertain"))
        and isinstance(error, Mapping)
        and error.get("manual_reconciliation") is False
    )


def _retryable_lock_contention(operation: Any) -> bool:
    """Recognize one known-safe transient failure for same-run convergence.

    A component mutation lock timeout is observed before the failed phase can
    commit authority, and the deployment executor has already completed its
    rollback before returning the Application receipt.  Retry it with a new
    idempotency identity in the same registry run.  Other deterministic
    deployment failures stay terminal and are never blindly replayed.
    """

    if _text(getattr(operation, "status", "")) != "failed":
        return False

    def contains_lock_timeout(value: Any) -> bool:
        if isinstance(value, Mapping):
            if _text(value.get("type")) == "MutationLockTimeout":
                return True
            return any(contains_lock_timeout(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_lock_timeout(item) for item in value)
        return False

    return contains_lock_timeout(getattr(operation, "result", None))


def _model_runtime_selections(model: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = model.get("runtime_selections")
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, Mapping)]


def _runtime_selection_requires_reconciliation(
    model: Mapping[str, Any],
    *,
    installed_release_digest: str,
) -> bool:
    return any(
        _text(item.get("source")) == "stable_installation"
        and _text(item.get("runtime_root_ref")) == "workspace"
        and _text(item.get("release_digest")) != installed_release_digest
        for item in _model_runtime_selections(model)
    )


def _workspace_authority_requires_reconciliation(
    service: ApplicationService,
    *,
    state_dir: Path,
    application_id: str,
    installed_release_digest: str,
) -> bool:
    executor = getattr(service, "executor", None)
    inspect = getattr(executor, "native_cbs_workspace_status", None)
    if callable(inspect):
        status = inspect(application_id, installed_release_digest)
    else:
        from adaos.services.applications.cbs_admission import (
            NativeApplicationCBSAdmissionService,
        )
        from adaos.services.applications.cbs_activation import (
            NativeApplicationCBSActivationService,
        )
        from adaos.services.artifact_pipeline.packages import (
            ContentAddressedPackageStore,
        )

        admission = NativeApplicationCBSAdmissionService(
            state_dir
        ).find_by_project_release(
            installed_release_digest,
            application_ref=f"application:{application_id}",
        )
        if admission is None:
            return False
        status = NativeApplicationCBSActivationService(
            state_dir=state_dir,
            workspace_root=state_dir.parent / "workspace",
            package_store=ContentAddressedPackageStore(
                state_dir / "artifact_pipeline" / "packages"
            ),
        ).current_status(
            application_ref=f"application:{application_id}",
            project_release_digest=installed_release_digest,
            admission_digest=str(admission.get("admission_digest") or ""),
        )
    return isinstance(status, Mapping) and status.get("current") is False


class ApplicationAutoUpdateService:
    """Apply exact safe updates for ``auto_compatible`` subscriptions."""

    def __init__(
        self, state_dir: Path, application_service: ApplicationService
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.application_service = application_service

    @property
    def root(self) -> Path:
        path = self.state_dir / "applications" / "auto_update_runs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def _persist(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(payload)
        run_id = _text(value.get("run_id"))
        with mutation_lock(self.lock_path, timeout_s=30.0):
            atomic_write_json(self.root / run_id / "run.json", value)
            atomic_write_json(self.root / "current.json", value)
        return value

    def _reconcile_stable_runtime(
        self,
        *,
        application_id: str,
        project_id: str,
        release_digest: str,
        webspace_id: str,
        actor_ref: str,
        subnet_ref: str,
    ) -> dict[str, Any]:
        """Advance an existing Stable channel after its installation updates.

        Applications without a RuntimeSelection remain on the compatibility
        lifecycle.  Existing Stable selections, however, must move with the
        installation or ingress observes two different release identities.
        This follow-up is idempotent and a later auto-update run can repair it
        even when package installation already converged.
        """

        workspace_authority: Mapping[str, Any] | None = None
        executor = getattr(self.application_service, "executor", None)
        reconcile_workspace = getattr(
            executor, "reconcile_native_cbs_workspace", None
        )
        if callable(reconcile_workspace):
            workspace_authority = reconcile_workspace(
                application_id=application_id,
                project_id=project_id,
                release_digest=release_digest,
                actor_ref=actor_ref,
                subnet_ref=subnet_ref,
                idempotency_key=(
                    f"application-auto-update:workspace-reconcile:"
                    f"{application_id}:{release_digest}"
                ),
            )
        else:
            from adaos.services.applications.cbs_admission import (
                NativeApplicationCBSAdmissionService,
            )
            from adaos.services.applications.cbs_activation import (
                NativeApplicationCBSActivationService,
            )
            from adaos.services.artifact_pipeline.channels import ReleaseRepository
            from adaos.services.artifact_pipeline.packages import (
                ContentAddressedPackageStore,
            )

            admission_service = NativeApplicationCBSAdmissionService(self.state_dir)
            admission = admission_service.find_by_project_release(
                release_digest,
                application_ref=f"application:{application_id}",
            )
            if admission is not None:
                release_plan = ReleaseRepository(
                    self.state_dir / "artifact_pipeline" / "release-cache"
                ).get_release(project_id, release_digest)
                packages = ContentAddressedPackageStore(
                    self.state_dir / "artifact_pipeline" / "packages"
                )
                if not isinstance(admission.get("binding_instance_records"), list):
                    from adaos.services.applications.cbs import ApplicationCBSService

                    compilation = ApplicationCBSService(
                        self.state_dir
                    ).inspect_requirement_source(
                        f"application:{application_id}",
                        project_release_digest=release_digest,
                    )
                    if compilation is None:
                        raise RuntimeError(
                            "native CBS workspace reconciliation has no semantic source"
                        )
                    admission = admission_service.admit(
                        application_ref=f"application:{application_id}",
                        compilation=compilation,
                        release_plan=release_plan,
                        package_store=packages,
                        workspace_ref=str(
                            admission.get("workspace_ref") or "workspace:local"
                        ),
                        evidence_context={
                            "application_id": application_id,
                            "operation": "workspace_reconcile",
                            "source": "application_auto_update",
                        },
                    )
                activation_service = NativeApplicationCBSActivationService(
                    state_dir=self.state_dir,
                    workspace_root=self.state_dir.parent / "workspace",
                    package_store=packages,
                )
                before = activation_service.current_status(
                    application_ref=f"application:{application_id}",
                    project_release_digest=release_digest,
                    admission_digest=str(admission.get("admission_digest") or ""),
                )
                activation = activation_service.activate(
                    admission=admission,
                    release_plan=release_plan,
                    application_id=application_id,
                    idempotency_key=(
                        f"application-auto-update:workspace-reconcile:"
                        f"{application_id}:{release_digest}"
                    ),
                    actor_ref=actor_ref,
                )
                workspace_authority = {
                    "status": (
                        "current" if before.get("current") is True else "reconciled"
                    ),
                    "changed": before.get("current") is not True,
                    "before": dict(before),
                    "activation": activation.to_dict(),
                }

        store = getattr(self.application_service, "store", None)
        if store is None or not callable(
            getattr(store, "list_runtime_selections", None)
        ):
            return {
                "status": "not_observed",
                "reason": "runtime_store_unavailable",
                "workspace_authority": dict(workspace_authority or {}),
            }
        selections = [
            item
            for item in store.list_runtime_selections()
            if item.application_id == application_id
        ]
        if any(
            item.source != "stable_installation" or item.runtime_root_ref != "workspace"
            for item in selections
        ):
            raise RuntimeError(
                "automatic update cannot replace an active non-Stable runtime selection"
            )
        if not selections:
            # Native Applications may have been imported before the stable
            # RuntimeSelection rail existed.  Adopt only an exact
            # application:<id> admission; legacy Scenario/Skill admissions
            # intentionally remain on the compatibility lifecycle.
            from .cbs_admission import NativeApplicationCBSAdmissionService

            admission = NativeApplicationCBSAdmissionService(
                store.state_dir
            ).find_by_project_release(
                release_digest,
                application_ref=f"application:{application_id}",
            )
            if admission is None:
                return {"status": "compatibility_runtime", "changed": False}
            return self.application_service.reconcile_native_runtime_selection(
                application_id=application_id,
                release_digest=release_digest,
                admission=admission,
                webspace_id=webspace_id,
                actor_ref=actor_ref,
                subnet_ref=subnet_ref,
            )
        if all(item.release_digest == release_digest for item in selections):
            return {
                "status": "current",
                "changed": False,
                "selection_count": len(selections),
                "workspace_authority": dict(workspace_authority or {}),
            }
        selected = next(
            (item for item in selections if item.webspace_id == webspace_id),
            selections[0],
        )
        updated = self.application_service.select_runtime(
            webspace_id=selected.webspace_id,
            application_id=application_id,
            source="stable_installation",
            release_digest=release_digest,
            runtime_root_ref="workspace",
            expected_revision=selected.revision,
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability="applications.apply",
        )
        return {
            "status": "reconciled",
            "changed": True,
            "selection_count": len(selections),
            "selection": updated.to_dict(),
            "workspace_authority": dict(workspace_authority or {}),
        }

    def run(
        self,
        *,
        subnet_ref: str,
        trigger: str,
        registry_index_digest: str | None = None,
        application_ids: list[str] | tuple[str, ...] | None = None,
        webspace_id: str = "desktop",
    ) -> dict[str, Any]:
        subnet = _text(subnet_ref)
        if not subnet.startswith("subnet:"):
            raise ValueError("subnet_ref must be a canonical subnet reference")
        trigger_token = _text(trigger) or "registry_sync"
        requested = {_text(item) for item in (application_ids or ()) if _text(item)}
        models = self.application_service.list_models(
            installed_only=True,
            subscriber_subnet_ref=subnet,
        )
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for model in models:
            application = model.get("application")
            installation = model.get("installation")
            effective = model.get("effective_release")
            app_id = _text(
                application.get("application_id")
                if isinstance(application, Mapping)
                else ""
            )
            project_id = _text(
                application.get("legacy_project_id")
                if isinstance(application, Mapping)
                else ""
            )
            if requested and app_id not in requested:
                continue
            if not bool(model.get("auto_update_enabled")):
                skipped.append(
                    {"application_id": app_id, "reason": "auto_update_disabled"}
                )
                continue
            if not isinstance(installation, Mapping):
                skipped.append(
                    {"application_id": app_id, "reason": "stable_installation_required"}
                )
                continue
            if list(installation.get("uncertain_operation_refs") or ()):
                skipped.append(
                    {"application_id": app_id, "reason": "uncertain_operation_present"}
                )
                continue
            if bool(model.get("local_beta_active")) or any(
                _text(item.get("source")) != "stable_installation"
                or _text(item.get("runtime_root_ref")) != "workspace"
                for item in _model_runtime_selections(model)
            ):
                skipped.append(
                    {"application_id": app_id, "reason": "active_non_stable_runtime"}
                )
                continue
            installed_digest = _text(installation.get("installed_release_digest"))
            if not bool(model.get("update_available")):
                if installed_digest and (
                    _runtime_selection_requires_reconciliation(
                        model,
                        installed_release_digest=installed_digest,
                    )
                    or _workspace_authority_requires_reconciliation(
                        self.application_service,
                        state_dir=self.state_dir,
                        application_id=app_id,
                        installed_release_digest=installed_digest,
                    )
                ):
                    candidates.append(
                        {
                            "application_id": app_id,
                            "project_id": project_id,
                            "target_release_digest": installed_digest,
                            "installation_revision": int(
                                installation.get("revision") or 0
                            ),
                            "reconcile_only": True,
                        }
                    )
                else:
                    skipped.append(
                        {"application_id": app_id, "reason": "already_current"}
                    )
                continue
            target_digest = _text(
                effective.get("release_digest")
                if isinstance(effective, Mapping)
                else ""
            )
            if not target_digest:
                skipped.append(
                    {"application_id": app_id, "reason": "target_release_unavailable"}
                )
                continue
            candidates.append(
                {
                    "application_id": app_id,
                    "project_id": project_id,
                    "target_release_digest": target_digest,
                    "installation_revision": int(installation.get("revision") or 0),
                }
            )

        identity = canonical_payload_digest(
            {
                "subnet_ref": subnet,
                "trigger": trigger_token,
                "registry_index_digest": _text(registry_index_digest) or None,
                "candidates": candidates,
            }
        )
        run_id = f"appautorun.{identity.split(':', 1)[1][:32]}"
        actor_ref = "service:application-auto-update"
        registry_snapshot = _text(registry_index_digest) or "registry-index:unavailable"
        outcomes: list[dict[str, Any]] = []
        for candidate in candidates:
            app_id = candidate["application_id"]
            target_digest = candidate["target_release_digest"]
            child_identity = hashlib.sha256(
                (
                    f"{subnet}\0{app_id}\0{target_digest}\0"
                    f"{candidate['installation_revision']}\0{registry_snapshot}\0"
                    f"{APPLICATION_OPERATION_PLANNER_REVISION}"
                ).encode("utf-8")
            ).hexdigest()
            idempotency_key = f"application-auto-update:{child_identity}"
            item: dict[str, Any] = {
                **candidate,
                "status": "planning",
                "idempotency_key": idempotency_key,
            }
            try:
                if bool(candidate.get("reconcile_only")):
                    item["runtime_selection"] = self._reconcile_stable_runtime(
                        application_id=app_id,
                        project_id=str(candidate.get("project_id") or app_id),
                        release_digest=target_digest,
                        webspace_id=webspace_id,
                        actor_ref=actor_ref,
                        subnet_ref=subnet,
                    )
                    item["status"] = "succeeded"
                    outcomes.append(item)
                    continue
                retry_chain: list[str] = []
                failed_retry_chain: list[str] = []
                for _attempt in range(16):
                    operation = self.application_service.plan_operation(
                        app_id,
                        "update",
                        release_digest=target_digest,
                        expected_revision=candidate["installation_revision"],
                        actor_ref=actor_ref,
                        subnet_ref=subnet,
                        capability="applications.plan",
                        idempotency_key=idempotency_key,
                    )
                    operation_status = _text(getattr(operation, "status", "planned"))
                    if operation_status != "planned":
                        if not _retryable_terminal_operation(operation):
                            raise RuntimeError(
                                "automatic update blocked by unresolved operation "
                                f"status: {operation_status or 'unknown'}"
                            )
                        retry_chain.append(_text(operation.operation_id))
                        if operation_status == "failed":
                            failed_retry_chain.append(_text(operation.operation_id))
                        retry_identity = hashlib.sha256(
                            (
                                f"{idempotency_key}\0{operation.operation_id}\0"
                                f"{getattr(operation, 'revision', 0)}"
                            ).encode("utf-8")
                        ).hexdigest()[:20]
                        idempotency_key = (
                            f"application-auto-update:{child_identity}:retry:"
                            f"{retry_identity}"
                        )
                        continue

                    item["idempotency_key"] = idempotency_key
                    item["operation_id"] = operation.operation_id
                    item["plan_digest"] = operation.plan_digest
                    blockers = automatic_update_blockers(operation.plan)
                    if blockers:
                        item["status"] = "review_required"
                        item["blockers"] = blockers
                        break

                    receipt = self.application_service.apply_operation(
                        operation.operation_id,
                        plan_digest=operation.plan_digest,
                        idempotency_key=idempotency_key,
                        actor_ref=actor_ref,
                        subnet_ref=subnet,
                        capability="applications.apply",
                    )
                    item["status"] = receipt.status
                    item["operation_revision"] = receipt.revision
                    item["result"] = dict(receipt.result)
                    if receipt.status == "succeeded":
                        item["runtime_selection"] = self._reconcile_stable_runtime(
                            application_id=app_id,
                            project_id=str(candidate.get("project_id") or app_id),
                            release_digest=target_digest,
                            webspace_id=webspace_id,
                            actor_ref=actor_ref,
                            subnet_ref=subnet,
                        )
                        try:
                            release = self.application_service.store.get_release(
                                app_id, target_digest
                            )
                            item["addresses_report_ids"] = list(
                                release.addresses_report_ids
                            )
                        except (AttributeError, FileNotFoundError, KeyError):
                            item["addresses_report_ids"] = []
                        break
                    if not _retryable_lock_contention(receipt):
                        break
                    retry_chain.append(_text(operation.operation_id))
                    failed_retry_chain.append(_text(operation.operation_id))
                    retry_identity = hashlib.sha256(
                        (
                            f"{idempotency_key}\0{operation.operation_id}\0"
                            f"{getattr(receipt, 'revision', 0)}"
                        ).encode("utf-8")
                    ).hexdigest()[:20]
                    idempotency_key = (
                        f"application-auto-update:{child_identity}:retry:"
                        f"{retry_identity}"
                    )
                else:
                    raise RuntimeError("automatic update retry chain exhausted")
                item["idempotency_key"] = idempotency_key
                if retry_chain:
                    item["retried_terminal_operation_ids"] = retry_chain
                if failed_retry_chain:
                    item["retried_failed_operation_ids"] = failed_retry_chain
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            outcomes.append(item)

        applied = sum(1 for item in outcomes if item.get("status") == "succeeded")
        review_required = sum(
            1 for item in outcomes if item.get("status") == "review_required"
        )
        failed = sum(1 for item in outcomes if item.get("status") == "failed")
        uncertain = sum(
            1
            for item in outcomes
            if item.get("status") in {"unknown", "applying", "reconciling"}
        )
        payload = {
            "schema": AUTO_UPDATE_RUN_SCHEMA,
            "run_id": run_id,
            "status": "failed" if failed else "uncertain" if uncertain else "completed",
            "trigger": trigger_token,
            "subnet_ref": subnet,
            "webspace_id": _text(webspace_id) or "desktop",
            "registry_index_digest": _text(registry_index_digest) or None,
            "candidate_count": len(candidates),
            "applied_count": applied,
            "review_required_count": review_required,
            "failed_count": failed,
            "uncertain_count": uncertain,
            "skipped_count": len(skipped),
            "outcomes": outcomes,
            "skipped": skipped,
            "updated_at": utc_now(),
        }
        return self._persist(payload)


__all__ = [
    "AUTO_UPDATE_RUN_SCHEMA",
    "ApplicationAutoUpdateService",
    "automatic_update_blockers",
]
