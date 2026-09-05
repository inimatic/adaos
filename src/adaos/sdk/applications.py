"""Public product-level Application lifecycle facade.

The facade exposes typed records and reviewed operations only. It never accepts
filesystem paths, process commands, Git credentials, or raw registry writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.domain.application import RuntimeSelection
from adaos.sdk.core._ctx import require_ctx
from adaos.services.applications import get_application_service


def _service():
    ctx = require_ctx("sdk.applications")
    return get_application_service(Path(ctx.paths.state_dir()))


def _mutation_identity(actor_ref: str, subnet_ref: str, idempotency_key: str) -> tuple[str, str, str]:
    actor = str(actor_ref or "").strip()
    subnet = str(subnet_ref or "").strip()
    key = str(idempotency_key or "").strip()
    if not actor:
        raise ValueError("actor_ref is required")
    if not subnet.startswith("subnet:"):
        raise ValueError("subnet_ref must use subnet:<id>")
    if not key:
        raise ValueError("idempotency_key is required")
    return actor, subnet, key


def list_applications(*, installed_only: bool = False) -> list[dict[str, Any]]:
    return _service().list_models(installed_only=installed_only)


def get_application(application_id: str) -> dict[str, Any]:
    service = _service()
    application = service.store.get_application(application_id)
    return next(
        item
        for item in service.list_models()
        if item["application"]["application_id"] == application.application_id
    )


def list_catalog() -> list[dict[str, Any]]:
    return [
        item
        for item in _service().list_models()
        if item["application"]["visibility"] == "public"
        and item["channels"].get("stable")
    ]


def list_releases(application_id: str) -> list[dict[str, Any]]:
    return _service().list_releases(application_id)


def get_subscription(application_id: str) -> dict[str, Any] | None:
    try:
        return _service().store.get_subscription(application_id).to_dict()
    except FileNotFoundError:
        return None


def get_runtime_selection(webspace_id: str, application_id: str) -> dict[str, Any] | None:
    try:
        return _service().store.get_runtime_selection(webspace_id, application_id).to_dict()
    except FileNotFoundError:
        return None


def list_operations(application_id: str | None = None) -> list[dict[str, Any]]:
    return [item.to_dict() for item in _service().store.list_operations(application_id)]


def get_operation(operation_id: str) -> dict[str, Any]:
    return _service().store.get_operation(operation_id).to_dict()


def plan_install(
    application_id: str,
    *,
    release_digest: str | None,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    idempotency_key: str,
    data_policy: str = "retain",
) -> dict[str, Any]:
    actor, subnet, key = _mutation_identity(actor_ref, subnet_ref, idempotency_key)
    return _service().plan_operation(
        application_id,
        "install",
        release_digest=release_digest,
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        idempotency_key=key,
        data_policy=data_policy,
    ).to_dict()


def plan_update(
    application_id: str,
    *,
    release_digest: str | None,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    idempotency_key: str,
) -> dict[str, Any]:
    actor, subnet, key = _mutation_identity(actor_ref, subnet_ref, idempotency_key)
    return _service().plan_operation(
        application_id,
        "update",
        release_digest=release_digest,
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        idempotency_key=key,
    ).to_dict()


def plan_remove(
    application_id: str,
    *,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    idempotency_key: str,
    data_policy: str = "retain",
) -> dict[str, Any]:
    actor, subnet, key = _mutation_identity(actor_ref, subnet_ref, idempotency_key)
    return _service().plan_operation(
        application_id,
        "remove",
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        idempotency_key=key,
        data_policy=data_policy,
    ).to_dict()


def plan_update_track(
    application_id: str,
    *,
    update_track: str,
    update_policy: str,
    paused: bool,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    idempotency_key: str,
    pinned_release_digest: str | None = None,
) -> dict[str, Any]:
    actor, subnet, key = _mutation_identity(actor_ref, subnet_ref, idempotency_key)
    return _service().plan_operation(
        application_id,
        "select_track",
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        idempotency_key=key,
        update_track=update_track,
        update_policy=update_policy,
        paused=paused,
        pinned_release_digest=pinned_release_digest,
    ).to_dict()


def apply_operation(
    operation_id: str,
    *,
    plan_digest: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if not str(plan_digest or "").startswith("sha256:"):
        raise ValueError("plan_digest is required")
    if not str(idempotency_key or "").strip():
        raise ValueError("idempotency_key is required")
    return _service().apply_operation(
        operation_id,
        plan_digest=plan_digest,
        idempotency_key=idempotency_key,
    ).to_dict()


def select_runtime(
    *,
    webspace_id: str,
    application_id: str,
    source: str,
    release_digest: str,
    runtime_root_ref: str,
    expected_revision: int,
) -> dict[str, Any]:
    selection: RuntimeSelection = _service().select_runtime(
        webspace_id=webspace_id,
        application_id=application_id,
        source=source,
        release_digest=release_digest,
        runtime_root_ref=runtime_root_ref,
        expected_revision=expected_revision,
    )
    return selection.to_dict()


def simulate_removal(application_id: str, *, data_policy: str = "retain") -> dict[str, Any]:
    return _service().simulate_removal(application_id, data_policy=data_policy)


def explain_plan(operation_id: str) -> dict[str, Any]:
    operation = _service().store.get_operation(operation_id)
    return {
        "operation_id": operation.operation_id,
        "plan_digest": operation.plan_digest,
        "plan": dict(operation.plan),
        "conflicts": list(operation.plan.get("conflicts") or []),
        "requires_snapshot": bool((operation.plan.get("snapshot") or {}).get("required")),
    }


__all__ = [
    "apply_operation",
    "explain_plan",
    "get_application",
    "get_operation",
    "get_runtime_selection",
    "get_subscription",
    "list_applications",
    "list_catalog",
    "list_operations",
    "list_releases",
    "plan_install",
    "plan_remove",
    "plan_update",
    "plan_update_track",
    "select_runtime",
    "simulate_removal",
]
