"""Local reviewed deployments commit package authority before runtime hooks.

The Artifact Pipeline owns the atomic Workspace/files transaction. Project
Deployment still owns component activation, health and its execution receipts.
Neither runtime guard nor standalone skill APIs gain a bypass permission.
"""
from __future__ import annotations

from dataclasses import replace

from adaos.services.artifact_pipeline.activation import WorkspaceActivationManager
from adaos.services.applications.store import ApplicationStore


def apply_local_workspace_deployment(executor, *, plan, desired, previous,
                                     deployment_plan, principal, snapshot_receipt,
                                     cbs_admission):
    actor = str(plan.get("actor_ref") or "application-core")
    release = executor.runtime.releases.get_release(
        str(plan["legacy_project_id"]), str(plan["release_digest"]))
    for package in release.packages:
        executor.ensure_verified_package(package)
    manager = WorkspaceActivationManager(workspace_root=executor.workspace_root,
        package_store=executor.package_store, state_root=executor.state_dir)
    current = manager.load_lock()
    expected_lock = current.to_dict()["lock_digest"] if current else None
    # A legacy installation need not have ProjectDeployment metadata yet. Its
    # reviewed installation is still the rollback authority, not an abandoned
    # desired topology left by an earlier unknown operation.
    baseline = previous
    if plan["kind"] == "update":
        store = ApplicationStore(executor.state_dir)
        try:
            installed = store.get_installation(str(plan["application_id"]))
        except FileNotFoundError:
            installed = None
        if installed is not None:
            old = store.get_release(installed.application_id, installed.installed_release_digest)
            baseline = replace(desired, release_digest=old.release_digest,
                placements=tuple(replace(desired.placements[0],
                    component_ref=str(component["component_ref"]), mode="singleton",
                    min_instances=1, max_instances=1)
                    for component in installed.component_refs))

    execution = {"operation": None, "attempted": False, "compensated": False,
                 "compensation_attempted": False}

    def reload_runtime(lock):
        selected = any(slot.project_id == release.release.project_id
                       and slot.release_digest == release.release.release_digest
                       for slot in lock.slots)
        if selected:
            execution["attempted"] = True
            operation = executor.runtime.apply(str(deployment_plan.plan_digest),
                principal=principal, idempotency_key=f"application:{plan['idempotency_key']}")
            execution["operation"] = operation
            if operation.state != "succeeded":
                raise RuntimeError("local_application_deployment_not_healthy:" + operation.state)
            return {"status": "completed", "deployment_operation_id": operation.operation_id}
        execution["compensation_attempted"] = True
        adapter = executor.runtime.adapter
        local = adapter if callable(getattr(adapter, "reconcile_locked_runtimes", None)) else getattr(adapter, "local", None)
        reconcile = getattr(local, "reconcile_locked_runtimes", None)
        if callable(reconcile):
            reconcile(lock, component_refs=[package.key for package in release.packages])
        receipt = executor._rollback_applied_deployment(desired=desired, previous=baseline,
            actor_ref=actor, idempotency_key=str(plan["idempotency_key"]))
        execution["compensated"] = True
        return {"status": "completed", "deployment_compensation": receipt}

    def health_check(_lock):
        operation = execution["operation"]
        return bool(operation is not None and operation.state == "succeeded")

    try:
        if cbs_admission is not None:
            from .cbs_activation import NativeApplicationCBSActivationService
            activated = NativeApplicationCBSActivationService(state_dir=executor.state_dir,
                workspace_root=executor.workspace_root, package_store=executor.package_store,
                activation_manager=manager).activate(admission=cbs_admission, release_plan=release,
                    application_id=str(plan["application_id"]), idempotency_key=str(plan["idempotency_key"]),
                    actor_ref=actor, reload_runtime=reload_runtime, health_check=health_check)
            activation = activated.to_dict()
        else:
            preserved_cbs = dict(current.cbs) if current and current.cbs else None
            activated = manager.activate(release,
                idempotency_key=f"application-workspace:{plan['idempotency_key']}",
                slot_id=release.release.project_id,
                fetch_package=lambda package: executor.package_store.read(package.digest),
                expected_lock_digest=expected_lock, reload_runtime=reload_runtime, health_check=health_check,
                permission_decision={"approved": True, "approved_by": actor,
                    "reason": "exact reviewed Application operation"},
                cbs_lock=preserved_cbs,
                application_resolution_digest=(preserved_cbs or {}).get("application_resolution_digest"),
                resolution_plan_digest=(preserved_cbs or {}).get("resolution_plan_digest"))
            activation = {"operation_id": activated.operation_id, "status": activated.status,
                "release_digest": activated.release_digest,
                "workspace_lock": activated.workspace_lock.to_dict(),
                "idempotent_replay": activated.idempotent_replay}
        # Replaying an already completed exact Workspace activation does not
        # replay migrations; observe/apply its idempotent component operation.
        if not execution["attempted"]:
            reload_runtime(manager.load_lock())
        operation = execution["operation"]
        return {"ok": True, "status": "active", "deployment": desired.to_dict(),
            "deployment_plan": deployment_plan.to_dict(), "deployment_operation": operation.to_dict(),
            "snapshot_receipt": snapshot_receipt, "workspace_activation": activation,
            "cbs_admission": dict(cbs_admission) if cbs_admission else None,
            "cbs_workspace_activation": activation if cbs_admission else None}
    except Exception as exc:
        compensation_error = None
        activation_key = ("native-cbs:" if cbs_admission else "application-workspace:") + str(plan["idempotency_key"])
        journal = manager._read_operation(manager.operation_id(activation_key))
        observed = manager.load_lock()
        restored_lock = (observed.to_dict()["lock_digest"] if observed else None) == expected_lock
        workspace_certain = restored_lock and (journal is None or (
            journal.get("status") == "failed" and journal.get("rolled_back") is True
            and not journal.get("rollback_error")))
        if journal and journal.get("rollback_error"):
            compensation_error = str(journal["rollback_error"])
        if (workspace_certain and execution["attempted"] and not execution["compensated"]
                and not execution["compensation_attempted"]):
            try:
                execution["compensation_attempted"] = True
                executor._rollback_applied_deployment(desired=desired, previous=baseline,
                    actor_ref=actor, idempotency_key=str(plan["idempotency_key"]))
                execution["compensated"] = True
            except Exception as rollback_exc:
                compensation_error = type(rollback_exc).__name__ + ": " + str(rollback_exc)
        certain = workspace_certain and (not execution["attempted"] or execution["compensated"])
        if certain and not execution["attempted"] and baseline is not None:
            executor._restore_desired(baseline, actor_ref=actor)
        restore = executor.snapshots.restore(snapshot_receipt["snapshot_ref"]) if certain and snapshot_receipt else None
        return {"ok": False, "status": "failed" if certain else "unknown",
            "reason": "local_workspace_activation_failed" if certain else "local_workspace_compensation_uncertain",
            "error_type": type(exc).__name__, "message": str(exc),
            "compensation_error": compensation_error, "snapshot_receipt": snapshot_receipt,
            "restore_receipt": restore,
            "workspace_activation_operation_id": manager.operation_id(activation_key),
            "deployment_operation": execution["operation"].to_dict() if execution["operation"] else None}
