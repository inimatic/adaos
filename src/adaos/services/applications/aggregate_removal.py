"""Fail-closed retirement of a Stable aggregate, independent of consumer UIs."""
from dataclasses import replace
from pathlib import Path

from adaos.domain.application import RuntimeSelection, utc_now
from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root
from .lifecycle_operations import assert_idle_jobs
from .runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from .runtime_transition import ApplicationRuntimeTransition, TransitionStep


def apply_reviewed_removal(service, operation, *, recover=False):
    state = service.store.state_dir
    channel = ApplicationRuntimeChannel(state, operation.application_id)
    runner = ApplicationRuntimeTransition(channel)
    retained = runner.get("application-remove:" + operation.operation_id) or {}
    external_may_have_run = retained.get("running_step") == "remove_packages" or "remove_packages" in retained.get("receipts", {})
    expected = tuple(RuntimeSelection.from_mapping(s) for s in operation.plan.get("expected_runtime_selections", []))
    applying = service._transition_operation(operation, "reconciling" if recover else "applying")
    owner = None
    try:
        from adaos.services.agent_context import get_ctx
        context = get_ctx()
        context = getattr(context, "authority_context", None) or context
        if Path(context.paths.state_dir()).resolve() == Path(state).resolve():
            owner = context
    except RuntimeError:
        pass
    workspace = Path(owner.paths.workspace_dir()) if owner else Path(state).parent / "workspace"

    def idle(_key):
        assert_idle_jobs(state, operation.application_id, workspace)
        for selected in expected:
            if selected.source == "local_trial":
                assert_idle_jobs(state, operation.application_id,
                    trial_workspace_root(workspace, selected.runtime_root_ref.removeprefix("trial:")))
        return {"ok": True}

    def remove(_key):
        current = service.store.get_operation(operation.operation_id)
        receipt = current.result.get("removal_executor_receipt")
        if receipt is None:
            if recover and external_may_have_run:
                # No proof that an external executor didn't already act. A
                # new apply would be a blind replay, even with the same key.
                raise RuntimeChannelConflict("RemovalExecutorOutcomeUnknown:inspect retained deployment journal")
            receipt = dict(service.executor(operation.plan))
            if not receipt.get("ok") or receipt.get("status") not in {"removed", "succeeded"}:
                raise RuntimeChannelConflict("RemovalExecutorNotVerified")
            service._transition_operation(current, current.status,
                result={"removal_executor_receipt": receipt})
        return {"ok": True, "status": "removed", "plan_digest": operation.plan_digest}

    def metadata(_key):
        current = service.store.get_installation(operation.application_id)
        if current.revision == operation.expected_revision:
            service.store.save_installation(replace(current, status="removed",
                data_policy=str(operation.plan.get("data_policy") or "retain"),
                revision=current.revision + 1, updated_at=utc_now()), expected_revision=current.revision)
        elif current.status != "removed" or current.revision != operation.expected_revision + 1:
            raise RuntimeChannelConflict("InstallationChangedDuringRemoval")
        return {"ok": True, "status": "removed"}

    try:
        journal = runner.retire_reviewed("application-remove:" + operation.operation_id,
            contract_digest=operation.plan_digest, expected=expected,
            steps=[TransitionStep("verify_idle_jobs", idle), TransitionStep("remove_packages", remove),
                   TransitionStep("retire_installation", metadata)])
        result = dict(service.store.get_operation(operation.operation_id).result.get("removal_executor_receipt") or {})
        result.update(installation=service.store.get_installation(operation.application_id).to_dict(),
                      runtime_transition_operation_id=journal["operation_id"])
        if owner:
            from .materialization import request_materialization
            result["runtime_refresh"] = request_materialization(owner, operation.application_id,
                webspace_ids=[s.webspace_id for s in expected])
        return service._transition_operation(service.store.get_operation(operation.operation_id), "succeeded", result=result)
    except Exception as exc:
        service._transition_operation(service.store.get_operation(operation.operation_id), "unknown",
            recovery_reason="RemovalRecoveryRequired:" + type(exc).__name__)
        raise
