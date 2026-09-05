"""Public product-level Application lifecycle facade.

The facade exposes typed records and reviewed operations only. It never accepts
filesystem paths, process commands, Git credentials, or raw registry writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.domain.application import RuntimeSelection
from adaos.sdk.core._ctx import require_ctx
from adaos.services.applications import (
    TrialAccessService,
    get_application_service,
    get_development_report_service,
)


def _service():
    ctx = require_ctx("sdk.applications")
    return get_application_service(Path(ctx.paths.state_dir()))


def _mutation_identity(
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    *,
    required_capability: str,
) -> tuple[str, str, str, str]:
    actor = str(actor_ref or "").strip()
    subnet = str(subnet_ref or "").strip()
    key = str(idempotency_key or "").strip()
    granted = str(capability or "").strip()
    if not actor:
        raise ValueError("actor_ref is required")
    if not subnet.startswith("subnet:"):
        raise ValueError("subnet_ref must use subnet:<id>")
    if not key:
        raise ValueError("idempotency_key is required")
    if granted != required_capability:
        raise ValueError(f"{required_capability} capability is required")
    return actor, subnet, granted, key


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


def poll_operation_events(
    *,
    application_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    events, checkpoint = _service().store.list_operation_events(
        application_id=application_id,
        cursor=cursor,
        limit=limit,
    )
    return {
        "schema": "adaos.application.operation_event_page.v1",
        "events": [dict(item) for item in events],
        "cursor": checkpoint,
    }


def list_development_reports() -> list[dict[str, Any]]:
    return get_development_report_service().list_reports()


def get_development_report(report_id: str) -> dict[str, Any]:
    report = get_development_report_service().get_report(report_id)
    if report is None:
        raise FileNotFoundError(f"DevelopmentReport not found: {report_id}")
    return dict(report)


def get_development_report_status(report_id: str) -> dict[str, Any] | None:
    status = get_development_report_service().public_status(report_id)
    return dict(status) if status is not None else None


def list_development_report_intakes() -> list[dict[str, Any]]:
    return get_development_report_service().list_publisher_intakes()


def get_operation(operation_id: str) -> dict[str, Any]:
    return _service().store.get_operation(operation_id).to_dict()


def list_trial_access(application_id: str | None = None) -> list[dict[str, Any]]:
    return [item.to_dict() for item in _service().store.list_grants(application_id)]


def issue_trial_access(
    application_id: str,
    *,
    publisher_ref: str,
    recipient_subnet_ref: str,
    recipient_key_ref: str,
    scope: str,
    expires_at: str,
    allowed_zones: tuple[str, ...],
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    release_digest: str | None = None,
    max_uses: int = 1,
) -> dict[str, Any]:
    _, subnet, _, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    if publisher_ref != subnet:
        raise ValueError("publisher_ref must match the authorized subnet")
    return TrialAccessService(_service()).issue(
        application_id,
        publisher_ref=publisher_ref,
        recipient_subnet_ref=recipient_subnet_ref,
        recipient_key_ref=recipient_key_ref,
        scope=scope,
        expires_at=expires_at,
        allowed_zones=allowed_zones,
        idempotency_key=idempotency_key,
        release_digest=release_digest,
        max_uses=max_uses,
    )


def resolve_trial_link(
    link: str,
    *,
    recipient_subnet_ref: str,
    recipient_key_ref: str,
    zone: str,
    actor_ref: str,
    capability: str,
    redemption_id: str,
) -> dict[str, Any]:
    """Resolve a prerelease Trial link before a reviewed Application install."""
    _mutation_identity(
        actor_ref,
        recipient_subnet_ref,
        capability,
        redemption_id,
        required_capability="applications.trial.redeem",
    )
    return TrialAccessService(_service()).resolve(
        link,
        recipient_subnet_ref=recipient_subnet_ref,
        recipient_key_ref=recipient_key_ref,
        zone=zone,
        redemption_id=redemption_id,
    )


def plan_trial_link_install(
    link: str,
    *,
    recipient_key_ref: str,
    zone: str,
    redemption_id: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    data_policy: str = "retain",
) -> dict[str, Any]:
    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.trial.install",
    )
    service = _service()
    redemption = TrialAccessService(service).resolve(
        link,
        recipient_subnet_ref=subnet,
        recipient_key_ref=recipient_key_ref,
        zone=zone,
        redemption_id=redemption_id,
    )
    operation = service.plan_operation(
        str(redemption["application_id"]),
        "install",
        release_digest=str(redemption["release_digest"]),
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        capability=granted,
        idempotency_key=key,
        data_policy=data_policy,
        access_redemption_id=str(redemption["redemption_id"]),
    )
    return {"redemption": dict(redemption), "operation": operation.to_dict()}


def revoke_trial_access(
    grant_id: str,
    *,
    publisher_ref: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
) -> dict[str, Any]:
    _, subnet, _, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        f"revoke:{grant_id}:{expected_revision}",
        required_capability="applications.apply",
    )
    if publisher_ref != subnet:
        raise ValueError("publisher_ref must match the authorized subnet")
    return TrialAccessService(_service()).revoke(
        grant_id,
        publisher_ref=publisher_ref,
        expected_revision=expected_revision,
    ).to_dict()


def plan_install(
    application_id: str,
    *,
    release_digest: str | None,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    data_policy: str = "retain",
    access_redemption_id: str | None = None,
) -> dict[str, Any]:
    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return _service().plan_operation(
        application_id,
        "install",
        release_digest=release_digest,
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        capability=granted,
        idempotency_key=key,
        data_policy=data_policy,
        access_redemption_id=access_redemption_id,
    ).to_dict()


def plan_update(
    application_id: str,
    *,
    release_digest: str | None,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    access_redemption_id: str | None = None,
) -> dict[str, Any]:
    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return _service().plan_operation(
        application_id,
        "update",
        release_digest=release_digest,
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        capability=granted,
        idempotency_key=key,
        access_redemption_id=access_redemption_id,
    ).to_dict()


def plan_remove(
    application_id: str,
    *,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    data_policy: str = "retain",
) -> dict[str, Any]:
    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return _service().plan_operation(
        application_id,
        "remove",
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        capability=granted,
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
    capability: str,
    idempotency_key: str,
    pinned_release_digest: str | None = None,
) -> dict[str, Any]:
    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return _service().plan_operation(
        application_id,
        "select_track",
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        capability=granted,
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
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if not str(plan_digest or "").startswith("sha256:"):
        raise ValueError("plan_digest is required")
    if not str(idempotency_key or "").strip():
        raise ValueError("idempotency_key is required")
    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    return _service().apply_operation(
        operation_id,
        plan_digest=plan_digest,
        idempotency_key=key,
        actor_ref=actor,
        subnet_ref=subnet,
        capability=granted,
    ).to_dict()


def select_runtime(
    *,
    webspace_id: str,
    application_id: str,
    source: str,
    release_digest: str,
    runtime_root_ref: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
) -> dict[str, Any]:
    actor, subnet, granted, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        f"runtime-selection:{webspace_id}:{application_id}:{expected_revision}",
        required_capability="applications.apply",
    )
    selection: RuntimeSelection = _service().select_runtime(
        webspace_id=webspace_id,
        application_id=application_id,
        source=source,
        release_digest=release_digest,
        runtime_root_ref=runtime_root_ref,
        expected_revision=expected_revision,
        actor_ref=actor,
        subnet_ref=subnet,
        capability=granted,
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
    "get_development_report",
    "get_development_report_status",
    "get_operation",
    "get_runtime_selection",
    "get_subscription",
    "issue_trial_access",
    "list_applications",
    "list_catalog",
    "list_development_report_intakes",
    "list_development_reports",
    "list_operations",
    "list_releases",
    "list_trial_access",
    "plan_install",
    "plan_trial_link_install",
    "plan_remove",
    "plan_update",
    "plan_update_track",
    "poll_operation_events",
    "resolve_trial_link",
    "revoke_trial_access",
    "select_runtime",
    "simulate_removal",
]
