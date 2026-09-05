"""Public product-level Application lifecycle facade.

The facade exposes typed records and reviewed operations only. It never accepts
filesystem paths, process commands, Git credentials, or raw registry writes.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from adaos.domain.application import RuntimeSelection
from adaos.sdk.core._ctx import require_ctx
from adaos.services.applications import (
    ApplicationRolloutService,
    DevelopmentReportTriageService,
    TrialAccessService,
    get_application_service,
    get_development_report_service,
)
from adaos.services.policy.skill_capabilities import require_skill_capability


def _service():
    ctx = require_ctx("sdk.applications")
    return get_application_service(Path(ctx.paths.state_dir()))


def _local_subnet_ref() -> str:
    config = require_ctx("sdk.applications").config
    subnet_id = str(
        config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id
    ).strip()
    return subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"


def _admit_active_skill_capability(required_capability: str) -> None:
    ctx = require_ctx("sdk.applications")
    current = ctx.skill_ctx.get()
    if str(getattr(current, "name", "") or "").strip():
        require_skill_capability(ctx, required_capability)


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
    if subnet.lower() != _local_subnet_ref().lower():
        raise ValueError("Application mutation subnet does not match local identity")
    _admit_active_skill_capability(required_capability)
    return actor, subnet, granted, key


def _report_identity(
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    *,
    required_capability: str,
) -> tuple[str, str, str, str]:
    identity = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability=required_capability,
    )
    if identity[1] != _local_subnet_ref():
        raise ValueError("Development Report subnet does not match local identity")
    return identity


def _release_read_model(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(value)
    project = raw.get("project_release")
    project = dict(project) if isinstance(project, Mapping) else {}
    components = []
    for item in project.get("components") or ():
        if not isinstance(item, Mapping):
            continue
        components.append({
            key: deepcopy(item[key])
            for key in (
                "kind", "artifact_id", "version", "digest", "manifest_digest",
                "builder_id", "build_policy_digest", "schema_locks",
                "conversational_lock", "workflow_lock", "workflow_validation_lock",
                "workflow_adapter_locks", "workflow_binding_digest",
                "workflow_role_policy_digest",
            )
            if key in item
        })
    dependencies = []
    for item in project.get("resolved_dependencies") or ():
        if not isinstance(item, Mapping):
            continue
        dependencies.append({
            key: deepcopy(item[key])
            for key in (
                "kind", "artifact_id", "version", "package_digest", "version_spec",
                "optional",
            )
            if key in item
        })
    composition = project.get("composition_lock")
    composition = dict(composition) if isinstance(composition, Mapping) else {}
    safe_composition = {
        key: deepcopy(composition[key])
        for key in (
            "schema", "project_definition_digest", "profiles", "members",
            "project_dependencies",
        )
        if key in composition
    }
    safe_composition["entrypoint_ids"] = [
        str(item.get("id") or "")
        for item in composition.get("entrypoints") or ()
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    ]
    safe_project = {
        key: deepcopy(project[key])
        for key in (
            "schema", "project_id", "version", "permissions", "schema_locks",
            "migration_locks", "validation_evidence_refs", "release_digest",
        )
        if key in project
    }
    safe_project.update({
        "components": components,
        "resolved_dependencies": dependencies,
        "composition_lock": safe_composition or None,
        "migration": {
            "required": bool(project.get("migrations")),
            "count": len(project.get("migrations") or ()),
        },
        "validation_evidence_count": len(project.get("validation_evidence") or ()),
        "private_source": "redacted",
    })
    return {
        key: deepcopy(raw[key])
        for key in (
            "schema", "application_id", "publisher_ref", "legacy_project_id", "version",
            "release_digest", "accepted_candidate_id", "provenance_refs",
            "addresses_report_ids", "lifecycle", "published_at", "channels",
        )
        if key in raw
    } | {
        "project_release": safe_project,
        "acceptance_evidence_count": len(raw.get("acceptance_evidence") or ()),
    }


def _application_read_model(value: Mapping[str, Any]) -> dict[str, Any]:
    model = deepcopy(dict(value))
    effective = model.get("effective_release")
    if isinstance(effective, dict) and isinstance(effective.get("release"), Mapping):
        effective["release"] = _release_read_model(effective["release"])
    return model


def list_applications(*, installed_only: bool = False) -> list[dict[str, Any]]:
    return [
        _application_read_model(item)
        for item in _service().list_models(
            installed_only=installed_only,
            subscriber_subnet_ref=_local_subnet_ref(),
        )
    ]


def get_application(application_id: str) -> dict[str, Any]:
    service = _service()
    application = service.store.get_application(application_id)
    return next(
        item
        for item in list_applications()
        if item["application"]["application_id"] == application.application_id
    )


def list_catalog() -> list[dict[str, Any]]:
    return [
        item
        for item in list_applications()
        if item["application"]["visibility"] == "public"
        and item["channels"].get("stable")
    ]


def list_releases(application_id: str) -> list[dict[str, Any]]:
    return [
        _release_read_model(item) for item in _service().list_releases(application_id)
    ]


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


def list_development_report_appeals(
    report_id: str | None = None,
) -> list[dict[str, Any]]:
    return get_development_report_service().list_local_appeals(report_id)


def list_publisher_development_report_appeals(
    report_id: str | None = None,
) -> list[dict[str, Any]]:
    return get_development_report_service().list_publisher_appeals(report_id)


def get_development_report_triage(
    report_id: str,
    *,
    threshold: float = 0.65,
    limit: int = 10,
) -> dict[str, Any]:
    triage = DevelopmentReportTriageService(get_development_report_service())
    return {
        "policy": triage.privacy_policy(),
        "duplicates": triage.duplicate_candidates(
            report_id, threshold=threshold, limit=limit
        ),
        "reporter_history": triage.reporter_history(report_id),
    }


def submit_development_report(
    application_id: str,
    *,
    summary: str,
    details: str,
    evidence: Sequence[Mapping[str, Any]] = (),
    installed_release_digest: str | None = None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.report",
    )
    return get_development_report_service().create_report(
        application_id=application_id,
        summary=summary,
        details=details,
        evidence=evidence,
        installed_release_digest=installed_release_digest,
        idempotency_key=idempotency_key,
    )


def sync_development_reports(
    *,
    limit: int = 20,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.report",
    )
    service = get_development_report_service()
    return {
        "received": service.receive(limit=max(1, min(int(limit), 100))),
        "outbox": service.flush_outbox(limit=max(1, min(int(limit), 100))),
    }


def triage_development_report(
    report_id: str,
    *,
    outcome: str,
    reason_code: str | None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.publisher.triage",
    )
    return get_development_report_service().triage(
        report_id, outcome=outcome, reason_code=reason_code
    )


def accept_development_report(
    report_id: str,
    *,
    policy_ref: str | None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.publisher.triage",
    )
    return get_development_report_service().accept(
        report_id, actor=actor_ref, policy_ref=policy_ref
    )


def set_development_report_status(
    report_id: str,
    *,
    status: str,
    reason_code: str | None,
    release_digest: str | None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.publisher.triage",
    )
    return get_development_report_service().set_public_status(
        report_id,
        status=status,
        reason_code=reason_code,
        release_digest=release_digest,
    )


def submit_development_report_appeal(
    report_id: str,
    *,
    statement: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.report",
    )
    return get_development_report_service().submit_appeal(
        report_id, statement=statement, idempotency_key=idempotency_key
    )


def resolve_development_report_appeal(
    appeal_id: str,
    *,
    resolution: str,
    rationale: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.publisher.triage",
    )
    return get_development_report_service().resolve_appeal(
        appeal_id, resolution=resolution, rationale=rationale
    )


def verify_development_report_release(
    report_id: str,
    *,
    outcome: str,
    release_digest: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.report",
    )
    return get_development_report_service().verify_release(
        report_id, outcome=outcome, release_digest=release_digest
    )


def request_development_report_resync(
    report_id: str,
    *,
    after_revision: int,
    limit: int = 100,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _report_identity(
        actor_ref, subnet_ref, capability, idempotency_key,
        required_capability="applications.report",
    )
    return get_development_report_service().request_resync(
        report_id, after_revision=after_revision, limit=limit
    )


def get_operation(operation_id: str) -> dict[str, Any]:
    return _service().store.get_operation(operation_id).to_dict()


def list_trial_access(application_id: str | None = None) -> list[dict[str, Any]]:
    return [item.to_dict() for item in _service().store.list_grants(application_id)]


def get_prerelease_rollout(application_id: str) -> dict[str, Any] | None:
    service = _service()
    policy = ApplicationRolloutService(service).get_policy(application_id)
    if policy is None:
        return None
    return {
        "policy": policy,
        "health": ApplicationRolloutService(service).health_summary(
            application_id, str(policy["release_digest"])
        ),
    }


def set_prerelease_rollout(
    application_id: str,
    *,
    release_digest: str,
    percentage: int,
    paused: bool,
    minimum_health_subnets: int,
    failure_threshold: float,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    resume_after_halt: bool = False,
) -> dict[str, Any]:
    _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.publish",
    )
    return ApplicationRolloutService(_service()).set_policy(
        application_id,
        release_digest=release_digest,
        publisher_ref=subnet_ref,
        percentage=percentage,
        paused=paused,
        minimum_health_subnets=minimum_health_subnets,
        failure_threshold=failure_threshold,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        resume_after_halt=resume_after_halt,
    )


def record_prerelease_health(
    application_id: str,
    release_digest: str,
    *,
    outcome: str,
    installation_revision: int,
    evidence_digest: str,
    observed_at: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    return ApplicationRolloutService(_service()).record_health(
        application_id,
        release_digest,
        subscriber_subnet_ref=subnet_ref,
        outcome=outcome,
        installation_revision=installation_revision,
        evidence_digest=evidence_digest,
        observed_at=observed_at,
        idempotency_key=idempotency_key,
    )


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
    """Plan stable or prerelease Application install/update track selection."""
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
    "accept_development_report",
    "apply_operation",
    "explain_plan",
    "get_application",
    "get_development_report",
    "get_development_report_status",
    "get_development_report_triage",
    "get_operation",
    "get_prerelease_rollout",
    "get_runtime_selection",
    "get_subscription",
    "issue_trial_access",
    "list_applications",
    "list_catalog",
    "list_development_report_intakes",
    "list_development_report_appeals",
    "list_development_reports",
    "list_operations",
    "list_releases",
    "list_trial_access",
    "list_publisher_development_report_appeals",
    "plan_install",
    "plan_trial_link_install",
    "plan_remove",
    "plan_update",
    "plan_update_track",
    "poll_operation_events",
    "record_prerelease_health",
    "request_development_report_resync",
    "resolve_development_report_appeal",
    "resolve_trial_link",
    "revoke_trial_access",
    "select_runtime",
    "set_prerelease_rollout",
    "set_development_report_status",
    "submit_development_report",
    "submit_development_report_appeal",
    "sync_development_reports",
    "triage_development_report",
    "verify_development_report_release",
    "simulate_removal",
]
