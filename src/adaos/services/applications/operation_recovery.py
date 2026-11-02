"""Recover only exact Core-owned intents; no generic external-effect replay."""
import hashlib

from adaos.services.artifact_pipeline.storage import mutation_lock
from .lifecycle_operations import ApplicationLifecycleOperations, PLAN_SCHEMA
from .runtime_channel import RuntimeChannelConflict


def recover_core_operation(owner, operation_id: str, *, plan_digest: str,
                           actor_ref: str, subnet_ref: str, capability: str) -> dict:
    operations = ApplicationLifecycleOperations(owner)
    operations._authorize(actor_ref, subnet_ref, capability, "applications.recover")
    if operation_id.startswith("appdevop."):
        from .development import ApplicationDevelopmentCoordinator, RECOVERABLE_PUBLICATION_ACTIONS
        from .candidate_decisions import recover_local_candidate_decision
        op = ApplicationDevelopmentCoordinator(operations.state).get(operation_id)
        if op["intent_digest"] != plan_digest:
            raise PermissionError("Exact retained development intent required")
        if op["action"] in RECOVERABLE_PUBLICATION_ACTIONS:
            from adaos.sdk.builder.applications import reconcile_development_operation
            return reconcile_development_operation(operation_id,
                actor_ref=actor_ref, subnet_ref=subnet_ref, capability=capability)
        return recover_local_candidate_decision(owner, operation_id,
            actor_ref=actor_ref, subnet_ref=subnet_ref, capability=capability)
    op = operations.store.get_operation(operation_id)
    if op.plan_digest != plan_digest or (op.actor_ref, op.subnet_ref) != (actor_ref, subnet_ref):
        raise PermissionError("Exact retained operation authority required")
    if op.plan.get("schema") == PLAN_SCHEMA:
        return operations.apply(operation_id, plan_digest=plan_digest, actor_ref=actor_ref,
            subnet_ref=subnet_ref, capability=capability, recover=True)
    if op.status == "succeeded":
        return op.to_dict()
    if op.status not in {"applying", "unknown", "reconciling"}:
        raise RuntimeChannelConflict("OnlyUnconfirmedOperationCanBeRecovered")
    if op.kind != "remove":
        raise RuntimeChannelConflict("ExternalAdapterOutcomeRequiresObservation: no blind replay permitted")
    token = hashlib.sha256(op.application_id.encode()).hexdigest()
    with mutation_lock(operations.store.root / "operation-leases" / f"{token}.lock", timeout_s=.25):
        from .aggregate_removal import apply_reviewed_removal
        return apply_reviewed_removal(operations.service, operations.store.get_operation(operation_id), recover=True).to_dict()
