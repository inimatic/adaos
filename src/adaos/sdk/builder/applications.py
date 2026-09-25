"""Bounded Builder facade for the Application development lifecycle."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from adaos.domain.application import Application, utc_now
from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.developer import compositions, projects
from adaos.services.applications import (
    ApplicationAccessManagementService,
    ApplicationDevelopmentCoordinator,
    StableSourceProjectionService,
    compile_setup_contract,
    get_application_distribution_service,
    get_application_service,
    get_stable_source_publisher,
)
from adaos.services.artifact_pipeline.runtime_trust import artifact_signing_public_identity
from adaos.services.policy.skill_capabilities import require_skill_capability


_ACTION_CAPABILITIES = {
    "create": "applications.develop",
    "update_metadata": "applications.develop",
    "delete": "applications.develop",
    "materialize": "applications.develop",
    "preview": "applications.develop",
    "create_trial": "applications.develop",
    "decide_trial": "applications.develop",
    "publish_trial": "applications.publish",
    "publish_prerelease": "applications.publish",
    "promote_stable": "applications.publish",
    "publish_to_registry": "applications.publish",
    "publish_stable_source": "applications.publish",
    "recover": "applications.recover",
}


def _ctx():
    return require_ctx("sdk.builder.applications")


def _state_dir() -> Path:
    return Path(_ctx().paths.state_dir()).expanduser().resolve()


def _application_service():
    return get_application_service(_state_dir())


def _distribution_service():
    try:
        return get_application_distribution_service()
    except RuntimeError as exc:
        if "not configured" not in str(exc).lower():
            raise
        from adaos.services.project_deployment.default_runtime import (
            configure_default_distributed_runtimes,
        )

        configure_default_distributed_runtimes(_ctx(), authoritative=False)
        return get_application_distribution_service()


def _application_for_project(project_id: str) -> Application | None:
    matches = [
        item
        for item in _application_service().store.list_applications()
        if item.legacy_project_id == str(project_id or "").strip()
    ]
    if len(matches) > 1:
        raise ValueError("Project is bound to multiple Application aggregates")
    return matches[0] if matches else None


def _ensure_application_for_project(project_id: str, *, actor_ref: str) -> Application:
    """Adopt an existing DEV Project before release-bound verification."""

    application = _application_for_project(project_id)
    if application is not None:
        return application

    project = compositions.get(project_id)
    catalog = project.get("catalog") or {}
    publisher = publisher_context()
    create_application(
        project_id,
        title=str(catalog.get("title") or project_id),
        summary=str(catalog.get("description") or ""),
        visibility="private",
        actor_ref=actor_ref,
        subnet_ref=publisher["publisher_ref"],
        capability="applications.develop",
        expected_revision=0,
        idempotency_key=f"trial-adopt:{project_id}",
    )
    application = _application_for_project(project_id)
    if application is None:
        raise ValueError("Created Project has no Application aggregate")
    return application


def _publisher_owner_role_ids(release) -> tuple[tuple[str, ...], str]:
    """Resolve the local publisher's Application role without guessing broadly."""

    owner_roles = [
        role for role in release.application_roles if "owner" in role.assignable_to
    ]
    explicit = [
        role
        for role in owner_roles
        if str(role.default_for.get("owner") or "") == role.role_id
    ]
    if len(explicit) == 1:
        return (explicit[0].role_id,), "declared_default"
    if len(explicit) > 1:
        raise ValueError(
            "Application declares multiple default owner roles; keep one "
            "application_roles[].default_for.owner"
        )

    # Compatibility for releases authored before owner defaults were required:
    # accept only a unique role whose grants are a strict superset of every
    # other owner-compatible role. Incomparable roles need an explicit choice.
    maximal = [
        role
        for role in owner_roles
        if not any(
            set(role.grants) < set(other.grants)
            for other in owner_roles
            if other.role_id != role.role_id
        )
    ]
    if len(maximal) == 1:
        return (maximal[0].role_id,), "unique_maximal_compatibility"
    raise ValueError(
        "Application owner role is ambiguous; declare exactly one "
        "application_roles[].default_for.owner"
    )


def _ensure_publisher_owner_access(
    application_id: str,
    *,
    release_digest: str,
) -> dict[str, Any]:
    """Materialize the publisher owner's reviewed access for an admitted release."""

    from adaos.services.personalization_runtime import current_user_id

    service = _application_service()
    release = service.store.get_release(application_id, release_digest)
    if release.application_roles:
        role_ids, resolution = _publisher_owner_role_ids(release)
    else:
        # A roleless Application still needs a reviewed permission grant.  Its
        # authority is the intersection of the release permission profile,
        # component capabilities and this ceiling; it deliberately has no
        # Application-local RBAC layer.
        role_ids, resolution = (), "permission_profile_only"
    owner_ref = f"user:{current_user_id(_ctx())}"
    constraints = {
        "platform_role": "owner",
        "subject_kind": "user",
        "managed_by": "builder.publisher_owner",
    }
    management = ApplicationAccessManagementService(service)
    managed = [
        grant
        for grant in service.store.list_application_access_grants(
            application_id,
            subject_ref=owner_ref,
        )
        if grant.constraints.get("managed_by") == "builder.publisher_owner"
    ]
    if len(managed) > 1:
        raise ValueError("Builder publisher owner access is duplicated; reconcile grants")
    ceiling = tuple(release.permission_profile.flat_permissions)
    if managed:
        grant = managed[0]
        exact = (
            grant.status == "active"
            and grant.application_roles == role_ids
            and grant.permission_ceiling == ceiling
            and not grant.explicit_denies
            and dict(grant.constraints) == constraints
            and grant.reviewed_permission_profile_digest
            == release.permission_profile.digest
            and grant.expires_at is None
        )
        if not exact:
            grant = management.access.change_access(
                grant.grant_id,
                release_digest=release_digest,
                application_roles=role_ids,
                issuer_ref=owner_ref,
                expected_revision=grant.revision,
                permission_ceiling=ceiling,
                explicit_denies=(),
                constraints=constraints,
                expires_at=None,
            )
    else:
        grant = management.access.grant_access(
            application_id,
            release_digest=release_digest,
            subject_ref=owner_ref,
            application_roles=role_ids,
            issuer_ref=owner_ref,
            idempotency_key="builder-publisher-owner-v1",
            permission_ceiling=ceiling,
            explicit_denies=(),
            constraints=constraints,
        )
    return {
        "required": True,
        "grant_id": grant.grant_id,
        "subject_ref": owner_ref,
        "application_roles": list(grant.application_roles),
        "permission_profile_digest": grant.reviewed_permission_profile_digest,
        "role_resolution": resolution,
        "revision": grant.revision,
    }


def project_access_contract(
    component_ref: str,
    *,
    project_ref: str | None = None,
) -> dict[str, Any]:
    """Project the trusted DEV permission declaration for Builder review."""

    from adaos.services.builder.application_permissions import (
        application_permissions_context,
    )

    ctx = _ctx()
    paths = ctx.paths
    projects_method = getattr(paths, "dev_projects_dir", None)
    skills_method = getattr(paths, "dev_skills_dir", None)
    dev_root = Path(paths.dev_dir()).resolve()
    projects_root = Path(
        projects_method() if callable(projects_method) else dev_root / "projects"
    ).resolve()
    skills_root = Path(
        skills_method() if callable(skills_method) else dev_root / "skills"
    ).resolve()
    return application_permissions_context(
        component_ref=str(component_ref or "").strip(),
        requested_project_ref=(str(project_ref or "").strip() or None),
        dev_projects_root=projects_root,
        dev_skills_root=skills_root,
    )


def verify_candidate_access(
    project_id: str,
    candidate_id: str,
    *,
    evidence: Mapping[str, Any] | None,
    actor_ref: str,
) -> dict[str, Any]:
    """Run access-aware Builder verification before a Candidate becomes Trial."""

    application = _ensure_application_for_project(project_id, actor_ref=actor_ref)
    distribution = _distribution_service()
    release = distribution.candidate_release_projection(
        application.application_id,
        candidate_id,
        publisher_ref=application.publisher_ref,
    )
    composition = release.project_release.composition_lock
    if composition is None or composition.permission_profile is None:
        return {
            "required": False,
            "status": "not_applicable",
            "reason": "legacy_application_contract",
            "application_id": application.application_id,
            "release_digest": release.release_digest,
        }
    payload = dict(evidence) if isinstance(evidence, Mapping) else {}
    if not payload:
        raise ValueError(
            "Access-aware Trial requires Builder Final Verification evidence"
        )
    management = ApplicationAccessManagementService(distribution.applications)
    observed = tuple(payload.get("observed_capabilities") or ())
    inferred = tuple(payload.get("inferred_capabilities") or ())
    profiler = management.permission_profiler(
        application.application_id,
        release_digest=release.release_digest,
        observed_capabilities=observed,
        inferred_capabilities=inferred,
        candidate_release=release,
    )
    verification = management.final_verification(
        application.application_id,
        release_digest=release.release_digest,
        source_commit=str(payload.get("source_commit") or "").strip(),
        observed_capabilities=observed,
        inferred_capabilities=inferred,
        regression_evidence=tuple(payload.get("regression_evidence") or ()),
        access_matrix_evidence=tuple(payload.get("access_matrix_evidence") or ()),
        pending_action_evidence=tuple(payload.get("pending_action_evidence") or ()),
        audit_evidence=tuple(payload.get("audit_evidence") or ()),
        disclosure_evidence=tuple(payload.get("disclosure_evidence") or ()),
        redaction_evidence=tuple(payload.get("redaction_evidence") or ()),
        release_scope=str(payload.get("release_scope") or "trial"),
        actor_ref=actor_ref,
        candidate_release=release,
    )
    if not verification.get("publication_allowed"):
        failed = [
            item["id"]
            for item in verification.get("checklist") or ()
            if item.get("result") in {"failed", "inconclusive"}
        ]
        raise ValueError(
            "Application Final Verification did not pass: "
            + ", ".join(failed)
        )
    return {
        "required": True,
        "status": "passed",
        "application_id": application.application_id,
        "release_digest": release.release_digest,
        "permission_profile": profiler,
        "verification": verification,
    }


def _coordinator() -> ApplicationDevelopmentCoordinator:
    return ApplicationDevelopmentCoordinator(_state_dir())


def _local_subnet_ref() -> str:
    config = _ctx().config
    subnet_id = str(
        config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id
    ).strip()
    return subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"


def _admit_builder_mutation(
    action: str,
    application_id: str,
    *,
    subnet_ref: str,
    capability: str,
) -> None:
    required = _ACTION_CAPABILITIES[action]
    if capability != required:
        raise ValueError(f"{required} capability is required")
    if str(subnet_ref).strip().lower() != _local_subnet_ref().lower():
        raise ValueError("Builder subnet does not match the local publisher identity")
    ctx = _ctx()
    current = ctx.skill_ctx.get()
    if str(getattr(current, "name", "") or "").strip():
        require_skill_capability(ctx, required)
    if action not in {"create", "recover"}:
        application = _application_service().store.get_application(application_id)
        if application.publisher_ref.lower() != str(subnet_ref).strip().lower():
            raise ValueError("only the local Application publisher may develop this Application")


def _execute_development(
    action: str,
    application_id: str,
    **arguments: Any,
) -> dict[str, Any]:
    _admit_builder_mutation(
        action,
        application_id,
        subnet_ref=str(arguments.get("subnet_ref") or ""),
        capability=str(arguments.get("capability") or ""),
    )
    return _coordinator().execute(action, application_id, **arguments)


def _development_project_snapshot(application: Application) -> dict[str, Any]:
    from adaos.sdk.developer import compositions

    project = compositions.get(application.legacy_project_id)
    primary = next(
        (
            item
            for item in project.get("components", {}).get("owned", ())
            if item.get("role") == "primary"
        ),
        None,
    )
    primary_ref = str((primary or {}).get("ref") or "").strip()
    if not primary_ref:
        raise ValueError("Application DEV Project has no primary component")
    owned_refs = tuple(
        str(item.get("ref") or "").strip()
        for item in project.get("components", {}).get("owned", ())
        if str(item.get("ref") or "").strip()
    )
    dependents = []
    owned_set = set(owned_refs)
    for candidate in compositions.list_projects(limit=500):
        candidate_id = str(candidate.get("id") or "").strip()
        if not candidate_id or candidate_id == application.legacy_project_id:
            continue
        try:
            candidate_project = compositions.get(candidate_id)
        except (FileNotFoundError, compositions.ProjectCompositionNotFound):
            continue
        dependencies = {
            str(item.get("ref") or "").strip()
            for item in candidate_project.get("components", {}).get("dependencies", ())
        }
        if owned_set.intersection(dependencies):
            dependents.append(candidate_id)
    if dependents:
        raise ValueError(
            "Application DEV components are dependencies of: " + ", ".join(sorted(dependents))
        )
    return {
        "project_id": application.legacy_project_id,
        "manifest_digest": str(project["manifest_digest"]),
        "primary_ref": primary_ref,
        "owned_refs": list(owned_refs),
    }


def _application(application_id: str, expected_revision: int) -> Application:
    application = _application_service().store.get_application(application_id)
    if application.revision != expected_revision:
        raise ValueError(
            f"Application revision conflict: expected {expected_revision}, observed {application.revision}"
        )
    return application


def _primary_scenario(application: Application) -> str:
    refs = [
        str(item.get("presentation_ref") or "")
        for item in application.entrypoints
        if str(item.get("presentation_ref") or "").startswith("scenario:")
    ]
    if not refs:
        raise ValueError("Application has no scenario entrypoint for Builder Preview")
    return refs[0].split(":", 1)[1]


def publisher_context() -> dict[str, Any]:
    ctx = _ctx()
    config = ctx.config
    subnet_id = str(config.subnet_id_value if hasattr(config, "subnet_id_value") else config.subnet_id)
    subnet_ref = subnet_id if subnet_id.startswith("subnet:") else f"subnet:{subnet_id}"
    zone_id = str(getattr(config, "zone_id", None) or "local").strip().lower()
    try:
        from adaos.services.subnet_alias import load_subnet_alias

        display_name = str(load_subnet_alias(subnet_id=subnet_id) or "").strip()
    except Exception:
        display_name = ""
    short_ref = subnet_id[-12:] if len(subnet_id) > 12 else subnet_id
    signing = artifact_signing_public_identity()
    return {
        "schema": "adaos.application.publisher_context.v1",
        "publisher_ref": subnet_ref,
        "display_name": display_name or f"Publisher {short_ref}",
        "subnet_short_ref": short_ref,
        "home_zone": zone_id,
        "release_key_ref": signing["release_key_ref"],
        "release_key_fingerprint": signing["key_id"],
        "release_key_algorithm": signing["algorithm"],
        "release_key_issuer": signing["issuer"],
        "trust_relation": "local",
    }


def production_webspace_id(webspace_id: str) -> str:
    from adaos.services.workspaces.relations import WebspaceRelationshipRegistry

    return WebspaceRelationshipRegistry.from_context().resolve_production_host(webspace_id)


def _sync_local_trial_home(application_id: str, *, webspace_id: str) -> dict[str, Any]:
    """Project an admitted local Beta as an installed, pinned Home application."""

    from adaos.sdk.applications import _sync_home_installation

    return _sync_home_installation(
        application_id,
        installed=True,
        webspace_id=webspace_id,
    )


def _with_release_setup_contract(envelope, runtime):
    """Compile setup from immutable release manifests and access declarations."""

    component_manifests: dict[str, Mapping[str, Any]] = {}
    for package in runtime.packages:
        source = runtime.verified_source(package)
        value: Any = None
        if package.kind == "skill":
            path = source / "skill.yaml"
            if path.is_file():
                value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        elif package.kind == "scenario":
            path = source / "scenario.json"
            if path.is_file():
                value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, Mapping):
            component_manifests[f"{package.kind}:{package.artifact_id}"] = dict(value)

    connected_accounts = []
    for provider in envelope.permission_profile.external_providers:
        provider_id = str(provider.get("id") or "").strip().lower()
        if not provider_id:
            continue
        account_id = str(provider.get("account_id") or provider_id).strip().lower()
        connected_accounts.append(
            {
                "id": account_id,
                "title": str(provider.get("title") or provider_id),
                "purpose": str(
                    provider.get("purpose")
                    or f"Connect the declared {provider_id} account"
                ),
                "required": bool(provider.get("required", True)),
                "scopes": list(provider.get("scopes") or ()),
            }
        )
    contract = compile_setup_contract(
        application_id=envelope.application_id,
        release_digest=envelope.release_digest,
        component_manifests=component_manifests,
        permission_profile=envelope.permission_profile.to_dict(),
        connected_accounts=connected_accounts,
        placement_required=True,
    )
    return replace(envelope, setup_contract=contract)


def place_local_trial(candidate_id: str, *, webspace_id: str, actor_ref: str) -> dict[str, Any]:
    """Admit local Builder Beta, migrating Stable data without a second UI approval."""

    from adaos.domain.application import ApplicationRelease
    from adaos.domain.artifact_release import ProjectRelease
    from adaos.sdk.developer import projects
    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.applications.local_release_transition import bind_local_data_lifecycle
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
    from adaos.services.workspaces.relations import WebspaceRelationshipRegistry

    if not actor_ref.strip():
        raise ValueError("Trial placement requires an actor")
    host = WebspaceRelationshipRegistry.from_context().resolve_production_host(webspace_id)
    if host != webspace_id:
        raise ValueError("Trial placement must target the production Webspace, not Preview")
    publisher = publisher_context()
    candidate = projects.get_candidate(candidate_id)["candidate"]
    runtime = NativeTrialRuntime._resolve_immutable(_ctx(), candidate_id, candidate["release_digest"])
    release_path = runtime.root / ".adaos/releases" / f"{runtime.release_digest.split(':')[1]}.json"
    release = ProjectRelease.from_mapping(json.loads(release_path.read_text(encoding="utf-8"))).seal()
    if release.release_digest != runtime.release_digest:
        raise ValueError("Candidate release digest mismatch")
    service = _application_service()
    project_id = release.project_id
    application = _ensure_application_for_project(project_id, actor_ref=actor_ref)
    _admit_builder_mutation("create_trial", application.application_id, subnet_ref=publisher["publisher_ref"],
                            capability="applications.develop")
    envelope = ApplicationRelease(application_id=application.application_id,
                                  publisher_ref=application.publisher_ref, project_release=release,
                                  accepted_candidate_id=candidate_id,
                                  acceptance_evidence=tuple(candidate["validation_evidence"]),
                                  provenance_refs=(release.release_digest,), lifecycle="trial")
    envelope = _with_release_setup_contract(envelope, runtime)
    from adaos.services.applications.access_management import (
        ApplicationAccessManagementService,
    )

    verification = ApplicationAccessManagementService(service).admit_release_stage(
        application.application_id,
        release_digest=envelope.release_digest,
        stage="trial",
        candidate_release=envelope,
    )
    service.register_release(envelope)
    owner_access = _ensure_publisher_owner_access(
        application.application_id,
        release_digest=envelope.release_digest,
    )
    data = bind_local_data_lifecycle(_ctx(), runtime, release)
    activations = TrialActivationStore(_state_dir() / "artifact_pipeline/trial-activations")

    def activate(_key):
        try:
            for package in runtime.packages:
                if package.kind == "skill":
                    runtime.ready_manager(package.artifact_id)
        except (ValueError, FileNotFoundError, RuntimeError, KeyError):
            runtime.manager(prepare=True)
        for package in runtime.packages:
            if package.kind == "skill":
                runtime.ready_manager(package.artifact_id)
        activation = activations.load(candidate_id)
        proof = {"operation_id": f"application-beta:{project_id}:{candidate_id}",
                 "contract_digest": data._contract()}
        activations.update(candidate_id, data_mode="snapshot",
            target=dict(activation["target"]) | {"webspace_id": webspace_id, "space_kind": "workspace"},
            safety_evidence=dict(activation.get("safety_evidence") or {}) | {"data_transition": proof})
        return {"ok": True, **proof}

    transition = data.prepare_beta(
        webspace_id=webspace_id,
        activate=activate,
        allow_beta_data_reset=True,
    )
    selection = service.store.get_runtime_selection(webspace_id, application.application_id)
    activation = activations.load(candidate_id)
    transition_proof = {
        "operation_id": transition.get("operation_id"),
        "contract_digest": data._contract(),
        "completed": bool(transition.get("completed")),
    }
    migrated_from_stable = bool(data.stable_digest)
    activation = activations.update(
        candidate_id,
        data_mode="snapshot",
        safety_evidence={
            **dict(activation.get("safety_evidence") or {}),
            "status": "verified",
            "mode": (
                "stable_snapshot_forward"
                if migrated_from_stable
                else "empty_initialization"
            ),
            "reason": (
                "Stable data was snapshotted and migrated into the isolated Trial."
                if migrated_from_stable
                else "The first release initialized an isolated Trial data store."
            ),
            "data_transition": transition_proof,
        },
    )
    home = _sync_local_trial_home(application.application_id, webspace_id=webspace_id)
    refresh = _refresh_application_placements(application.application_id)
    return {"ok": True, "runtime_selection": selection.to_dict(), "trial_activation": activation,
            "runtime_refresh": refresh, "data_transition": transition,
            "verification": verification, "publisher_owner_access": owner_access,
            "home": home}


def _refresh_application_placements(application_id: str) -> dict[str, Any]:
    from adaos.services.component_updates import ComponentUpdateService

    rooms = sorted({item.webspace_id for item in _application_service().store.list_runtime_selections()
                    if item.application_id == application_id})
    updates = ComponentUpdateService(state_dir=_state_dir())
    # Page metadata must observe the selected release before materialization.
    # Opening the notifications panel is not a prerequisite for Beta acceptance.
    for room in rooms:
        updates.reconcile_local_trials(room, application_id=application_id)
    return {"ok": True, "webspaces": {room: refresh_placement(room) for room in rooms}}


def abort_local_trial_preparation(candidate_id: str, *, release_digest: str, actor_ref: str) -> dict[str, Any]:
    """Recover a failed local prepare to its unchanged Stable, retaining evidence.

    Requires applications.recover and the exact Candidate/release. This cannot
    undo accepted data or published code; those require publication recovery.
    """
    import json

    from adaos.domain.artifact_release import ProjectRelease, WorkspaceLock
    from adaos.services.applications.local_release_transition import bind_local_data_lifecycle
    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.artifact_pipeline.storage import mutation_lock

    if not actor_ref.strip():
        raise ValueError("Recovery requires an actor")
    runtime = NativeTrialRuntime._resolve_immutable(_ctx(), candidate_id, release_digest)
    path = runtime.root / ".adaos/releases" / f"{runtime.release_digest.split(':')[1]}.json"
    release = ProjectRelease.from_mapping(json.loads(path.read_text(encoding="utf-8"))).seal()
    application = _application_service().store.get_application(release.project_id)
    subnet = _local_subnet_ref()
    _admit_builder_mutation("recover", application.application_id, subnet_ref=subnet, capability="applications.recover")
    if application.publisher_ref.lower() != subnet.lower():
        raise ValueError("Only the local publisher may recover its Beta preparation")
    lifecycle = bind_local_data_lifecycle(_ctx(), runtime, release)
    metadata = Path(_ctx().paths.workspace_dir()) / ".adaos"

    def verify(_key):
        lock = WorkspaceLock.from_mapping(json.loads((metadata / "workspace.lock.json").read_text(encoding="utf-8")))
        slots = [slot for slot in lock.slots if slot.project_id == release.project_id]
        if lifecycle.stable_digest:
            installation = _application_service().store.get_installation(application.application_id)
            if (installation.status != "active" or installation.installed_release_digest != lifecycle.stable_digest
                    or len(slots) != 1 or slots[0].release_digest != lifecycle.stable_digest):
                raise ValueError("Stable code/installation changed; explicit publication recovery required")
        elif slots:
            raise ValueError("Workspace installation appeared after Beta preparation")
        return {"ok": True, "release_digest": lifecycle.stable_digest}

    result = lifecycle.abort_beta_preparation(verify_source=verify,
        source_guard=lambda: mutation_lock(metadata / ".workspace-writer.lock", timeout_s=30))
    return {"ok": True, "status": "aborted", "operation_id": result["operation_id"],
            "runtime_refresh": _refresh_application_placements(application.application_id)}


def refresh_placement(webspace_id: str) -> dict[str, Any]:
    """Refresh derived room state through its owner, without changing navigation."""
    import requests

    from adaos.apps.cli.active_control import resolve_control_base_url, resolve_control_token

    base = resolve_control_base_url(prefer_local=True)
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(f"{base.rstrip('/')}/api/node/yjs/webspaces/{webspace_id}/refresh",
            headers={"X-AdaOS-Token": resolve_control_token(base_url=base)}, timeout=30)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise ValueError("Trial selection retained, but owner runtime refresh failed")
        return result


def accept_local_trial(application_id: str, *, webspace_id: str, candidate_id: str,
                       candidate_digest: str, actor_ref: str) -> dict[str, Any]:
    """Accept an exact local Candidate into Workspace, without public distribution."""
    from . import lifecycle, workflow
    from adaos.sdk.developer import projects

    service = _application_service()
    publisher = _local_subnet_ref()
    _admit_builder_mutation("promote_stable", application_id, subnet_ref=publisher, capability="applications.publish")
    application = service.store.get_application(application_id)
    selection = service.store.get_runtime_selection(webspace_id, application_id)
    release = service.store.get_release(application_id, selection.release_digest)
    if release.accepted_candidate_id != candidate_id:
        raise ValueError("RuntimeSelection changed; reopen the Candidate changelog")
    if selection.source not in {"local_trial", "stable_installation"}:
        raise ValueError("Only the publisher's local Trial can be accepted into Workspace")
    scenario_id = _primary_scenario(application)
    try:
        state = workflow.get_state("scenario", scenario_id)
    except FileNotFoundError:
        state = {}
    workflow_matches = lifecycle._candidate_identity(state) == (
        candidate_id,
        candidate_digest,
    )
    candidate: Mapping[str, Any] = {}
    if not workflow_matches:
        # A selected immutable Trial can outlive or advance independently from
        # the mutable Builder workflow projection. Resume only when the
        # Candidate record proves the exact reviewed package and release.
        try:
            candidate_result = projects.get_candidate(candidate_id)
        except (FileNotFoundError, KeyError, RuntimeError, ValueError):
            raise ValueError("Builder Candidate changed; reopen its changelog") from None
        candidate = (
            candidate_result.get("candidate")
            if isinstance(candidate_result.get("candidate"), Mapping)
            else {}
        )
        if (
            str(candidate.get("candidate_id") or "").strip() != candidate_id
            or str(candidate.get("package_digest") or "").strip() != candidate_digest
            or str(candidate.get("release_digest") or "").strip()
            != selection.release_digest
            or str(candidate.get("status") or "").strip().lower()
            not in {"trial", "accepted"}
        ):
            raise ValueError("Builder Candidate changed; reopen its changelog")
    access_management = ApplicationAccessManagementService(service)
    delivery_status = str((state.get("delivery") or {}).get("status") or "").strip().lower()
    publication_verification = _promote_local_trial_final_verification(
        access_management,
        application_id=application_id,
        webspace_id=webspace_id,
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
        release_digest=selection.release_digest,
        actor_ref=actor_ref,
        allow_completed=(
            not workflow_matches or delivery_status in {"accepted", "published"}
        ),
    )
    access_verification = access_management.admit_release_stage(
        application_id,
        release_digest=selection.release_digest,
        stage="publication",
    )
    identity = {"expected_candidate_id": candidate_id, "expected_candidate_digest": candidate_digest}
    key = f"accept-local-trial:{application_id}:{candidate_id}"
    if not workflow_matches:
        if str(candidate.get("status") or "").strip().lower() != "accepted":
            decided = projects.decide_candidate(
                candidate_id,
                accepted=True,
                observations=[{
                    "actor": actor_ref,
                    "decision": "accepted_for_publication",
                    "idempotency_key": key + ":decision",
                }],
            )
            decided_candidate = (
                decided.get("candidate")
                if isinstance(decided.get("candidate"), Mapping)
                else {}
            )
            if (
                str(decided_candidate.get("candidate_id") or "").strip()
                != candidate_id
                or str(decided_candidate.get("package_digest") or "").strip()
                != candidate_digest
                or str(decided_candidate.get("release_digest") or "").strip()
                != selection.release_digest
                or str(decided_candidate.get("status") or "").strip().lower()
                != "accepted"
            ):
                raise ValueError("Candidate acceptance is not confirmed")
        result = projects.promote_candidate(
            candidate_id,
            permission_decision={
                "approved": True,
                "actor": actor_ref,
                "actor_type": "user",
                "approval_id": f"candidate:{candidate_id}:publication",
            },
        )
        if (
            not bool(result.get("ok", True))
            or result.get("error")
            or str(result.get("status") or "").strip().lower() == "stale"
            or str(result.get("candidate_id") or "").strip() != candidate_id
            or str(result.get("release_digest") or "").strip()
            != selection.release_digest
            or str(result.get("package_digest") or "").strip() != candidate_digest
        ):
            raise ValueError("Workspace publication is not confirmed; Trial selection is retained")
        placement = place_local_stable(
            application_id,
            webspace_id=webspace_id,
            candidate_id=candidate_id,
            candidate_digest=candidate_digest,
            actor_ref=actor_ref,
            publication_result=result,
        )
        return {
            **placement,
            "publication": result,
            "application_verification": access_verification,
            "publication_verification": publication_verification,
        }
    if delivery_status not in {"accepted", "published"}:
        lifecycle.decide_trial("scenario", scenario_id, accepted=True, actor=actor_ref,
                              idempotency_key=key + ":decision", **identity)
    result = lifecycle.publish_candidate("scenario", scenario_id, actor=actor_ref,
                                         idempotency_key=key + ":workspace", **identity)
    committed = workflow.get_state("scenario", scenario_id)
    if not lifecycle._published_candidate_matches(committed.get("publication") or {},
                                                  candidate_id=candidate_id, candidate_digest=candidate_digest):
        raise ValueError("Workspace publication is not confirmed; Trial selection is retained")
    placement = place_local_stable(application_id, webspace_id=webspace_id, candidate_id=candidate_id,
                                   candidate_digest=candidate_digest, actor_ref=actor_ref)
    return {
        **placement,
        "publication": result,
        "application_verification": access_verification,
        "publication_verification": publication_verification,
    }


def _promote_local_trial_final_verification(
    management: ApplicationAccessManagementService,
    *,
    application_id: str,
    webspace_id: str,
    candidate_id: str,
    candidate_digest: str,
    release_digest: str,
    actor_ref: str,
    allow_completed: bool = False,
) -> dict[str, Any]:
    """Qualify the exact healthy Trial review for publication admission."""

    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

    runtime = NativeTrialRuntime.resolve(_ctx(), candidate_id, release_digest)
    activation = TrialActivationStore(
        _state_dir() / "artifact_pipeline/trial-activations"
    ).load(candidate_id)
    identity = dict(activation.get("candidate_ref") or {})
    target = dict(activation.get("target") or {})
    health = dict(activation.get("health_evidence") or {})
    binding = dict(activation.get("runtime_binding") or {})
    activation_statuses = {"active", "completed"} if allow_completed else {"active"}
    if (
        runtime.project_id != application_id
        or activation.get("status") not in activation_statuses
        or identity.get("candidate_id") != candidate_id
        or identity.get("release_digest") != release_digest
        or identity.get("package_digest") != candidate_digest
        or target.get("webspace_id") != webspace_id
        or health.get("status") != "passed"
        or binding.get("authority") != "immutable_candidate"
    ):
        raise ValueError(
            "The exact healthy Trial is required before publication verification"
        )
    lock_digest = str(binding.get("workspace_lock_digest") or "").strip()
    if not lock_digest.startswith("sha256:"):
        raise ValueError("Trial WorkspaceLock evidence is unavailable")
    evidence_ref = (
        f"release:trial-runtime:{candidate_id}#"
        f"{lock_digest.removeprefix('sha256:')}"
    )
    return management.promote_trial_verification_for_publication(
        application_id,
        release_digest=release_digest,
        publication_evidence=evidence_ref,
        actor_ref=actor_ref,
    )


def place_local_stable(application_id: str, *, webspace_id: str, candidate_id: str,
                       candidate_digest: str, actor_ref: str,
                       publication_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Project verified Workspace publication; never use a DEV Preview surface."""
    import json
    from adaos.domain.artifact_release import WorkspaceLock
    from adaos.services.artifact_pipeline.storage import mutation_lock
    from . import lifecycle, workflow

    if production_webspace_id(webspace_id) != webspace_id:
        raise ValueError("Stable placement requires a production Webspace")
    publisher = _local_subnet_ref()
    _admit_builder_mutation("promote_stable", application_id, subnet_ref=publisher, capability="applications.publish")
    service = _application_service()
    application = service.store.get_application(application_id)
    scenario_id = _primary_scenario(application)
    try:
        state = workflow.get_state("scenario", scenario_id)
    except FileNotFoundError:
        state = {}
    try:
        selected_release_digest = service.store.get_runtime_selection(
            webspace_id, application_id
        ).release_digest
    except FileNotFoundError:
        selected_release_digest = ""
    publication = state.get("publication") or {}
    workflow_published = lifecycle._published_candidate_matches(
        publication,
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
    )
    receipt = dict(publication_result or {})
    receipt_published = (
        bool(receipt.get("ok", True))
        and not receipt.get("error")
        and str(receipt.get("candidate_id") or "").strip() == candidate_id
        and str(receipt.get("release_digest") or "").strip()
        == selected_release_digest
        and str(receipt.get("package_digest") or "").strip() == candidate_digest
    )
    if not workflow_published and not receipt_published:
        raise ValueError("The exact Candidate has not been accepted into Workspace")
    record = publication.get("release_record") or {}
    digest = (
        str(record.get("release_digest") or "").strip()
        if workflow_published
        else str(receipt.get("release_digest") or "").strip()
    )
    release = service.store.get_release(application_id, digest)
    if release.accepted_candidate_id != candidate_id:
        raise ValueError("Published Application release belongs to another Candidate")
    ApplicationAccessManagementService(service).admit_release_stage(
        application_id,
        release_digest=release.release_digest,
        stage="publication",
    )
    owner_access = _ensure_publisher_owner_access(
        application_id,
        release_digest=release.release_digest,
    )
    metadata_root = Path(_ctx().paths.workspace_dir()) / ".adaos"
    with mutation_lock(metadata_root / ".workspace-writer.lock", timeout_s=30):
        lock = WorkspaceLock.from_mapping(json.loads((metadata_root / "workspace.lock.json").read_text(encoding="utf-8")))
        installation = service.reconcile_workspace_installation(application_id, digest, lock)
        try:
            selection = service.store.get_runtime_selection(webspace_id, application_id)
        except FileNotFoundError:
            selection = None
        if selection and selection.release_digest != digest:
            raise ValueError("RuntimeSelection changed; do not overwrite another selected release")
        if selection is None or selection.source != "stable_installation":
            selection = service.select_runtime(webspace_id=webspace_id, application_id=application_id,
                source="stable_installation", release_digest=digest, runtime_root_ref="workspace",
                expected_revision=selection.revision if selection else 0, actor_ref=actor_ref,
                subnet_ref=publisher, capability="applications.apply")
    existing = (state.get("project") or {}).get("placements") or []
    if workflow_published and not any(item.get("kind") == "stable" and item.get("status") == "active"
                                      and (item.get("target") or {}).get("webspace_id") == webspace_id
                                      and (item.get("result_ref") or {}).get("digest") == digest for item in existing):
        state = workflow.record_project_placement("scenario", scenario_id, {
            "kind": "stable", "result_ref": {"kind": "release", "id": publication["release"],
                "version": release.project_release.version, "digest": digest},
            "target": {"webspace_id": webspace_id, "space_kind": "workspace"},
            "scenario_id": scenario_id, "data_mode": "real", "runtime_binding": dict(record["activation"]),
            "safety": {"status": "verified", "source": "publication_activation"}},
            expected_generation=int(state["generation"]))["workflow"]
    if workflow_published:
        for item in (state.get("project") or {}).get("placements") or []:
            if (item.get("kind") == "trial" and item.get("status") == "active"
                and (item.get("target") or {}).get("webspace_id") == webspace_id
                and (item.get("result_ref") or {}).get("id") == candidate_id):
                state = workflow.record_project_placement("scenario", scenario_id, {**item, "status": "detached"},
                    expected_generation=int(state["generation"]))["workflow"]
    refresh = _refresh_application_placements(application_id)
    return {"ok": True, "workflow": state, "installation": installation.to_dict(),
            "runtime_selection": selection.to_dict(), "runtime_refresh": refresh,
            "publisher_owner_access": owner_access}


def open_trial_placement(candidate_id: str, *, webspace_id: str, scenario_id: str) -> dict[str, Any]:
    """Ask the running room owner to open an already admitted production Trial."""
    import requests

    from adaos.apps.cli.active_control import resolve_control_base_url, resolve_control_token
    from adaos.services.applications.runtime_selection import selected_trial

    runtime = selected_trial(_ctx(), webspace_id, "scenario", scenario_id)
    if runtime is None or runtime.candidate_id != candidate_id:
        raise ValueError("Trial placement is not the selected Application release")
    base = resolve_control_base_url(prefer_local=True)
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(f"{base.rstrip('/')}/api/node/yjs/webspaces/{webspace_id}/scenario",
            headers={"X-AdaOS-Token": resolve_control_token(base_url=base)},
            json={"scenario_id": scenario_id, "set_home": False,
                  "request_source": "builder.trial.placement", "wait_for_rebuild": False}, timeout=30)
        response.raise_for_status()
        return response.json()


def _create_application_effect(
    application_id: str,
    *,
    title: str,
    summary: str,
    template: str,
    visibility: str,
    actor_ref: str,
    subnet_ref: str,
    expected_revision: int,
    publisher: Mapping[str, Any],
    protection: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    from adaos.sdk.developer import compositions

    service = _application_service()
    try:
        existing = service.store.get_application(application_id)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        expected_identity = (
            existing.revision == 1
            and expected_revision == 0
            and existing.legacy_project_id == application_id
            and existing.publisher_ref == subnet_ref
            and existing.slug == application_id
            and existing.visibility == visibility
            and existing.display.get("title") == title
            and existing.display.get("summary") == summary
            and _primary_scenario(existing) == application_id
            and dict(existing.protection) == dict(Application(
                application_id=application_id,
                legacy_project_id=application_id,
                publisher_ref=subnet_ref,
                slug=application_id,
                display={"title": title, "summary": summary},
                visibility=visibility,  # type: ignore[arg-type]
                entrypoints=({"entrypoint_id": "main", "presentation_ref": f"scenario:{application_id}"},),
                publisher={key: publisher[key] for key in (
                    "publisher_ref", "display_name", "subnet_short_ref", "release_key_ref",
                    "release_key_fingerprint", "home_zone", "trust_relation",
                )},
                protection=dict(protection or {}),
            ).protection)
        )
        if not expected_identity:
            raise ValueError("Application already exists with another identity or revision")
        return {"ok": True, "duplicate": True, "application": existing.to_dict()}
    if expected_revision != 0:
        raise ValueError("new Application expected_revision must be zero")
    try:
        project = compositions.get(application_id)
        component_ref = f"scenario:{application_id}"
        if component_ref not in {
            str(item.get("ref") or "") for item in project["components"]["owned"]
        }:
            raise ValueError("existing Project does not own the Application scenario")
        composition = {"ok": True, "project": project, "created_component": False}
    except compositions.ProjectCompositionNotFound:
        composition = compositions.create_with_primary_component(
            application_id,
            kind="scenario",
            component_id=application_id,
            template=template,
            title=title,
            description=summary,
            entrypoints=(
                {
                    "id": "main",
                    "presentation": f"scenario:{application_id}",
                    "default": True,
                    "bindings": {},
                },
            ),
            compatibility={"required_entrypoints": ["main"]},
            actor=actor_ref,
        )
    application = Application(
        application_id=application_id,
        legacy_project_id=application_id,
        publisher_ref=subnet_ref,
        slug=application_id,
        display={"title": title, "summary": summary},
        visibility=visibility,  # type: ignore[arg-type]
        entrypoints=(
            {"entrypoint_id": "main", "presentation_ref": f"scenario:{application_id}"},
        ),
        publisher={
            key: publisher[key]
            for key in (
                "publisher_ref",
                "display_name",
                "subnet_short_ref",
                "release_key_ref",
                "release_key_fingerprint",
                "home_zone",
                "trust_relation",
            )
        },
        protection=dict(protection or {}),
    )
    saved = service.register(application, expected_revision=0)
    return {"ok": True, "application": saved.to_dict(), "composition": composition}


def create_application(
    application_id: str,
    *,
    title: str,
    summary: str,
    template: str = "empty",
    visibility: str = "private",
    protection: Mapping[str, Any] | None = None,
    source_webspace_id: str = "desktop",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    publisher = publisher_context()
    if subnet_ref != publisher["publisher_ref"]:
        raise ValueError("Builder subnet does not match the local publisher identity")
    intent = {
        "title": str(title),
        "summary": str(summary),
        "template": str(template),
        "visibility": str(visibility),
        "publisher_key_fingerprint": publisher["release_key_fingerprint"],
        "publisher": dict(publisher),
        "protection": dict(protection or {}),
        "source_webspace_id": str(source_webspace_id or "").strip() or "desktop",
    }

    def execute() -> Mapping[str, Any]:
        return _create_application_effect(
            application_id,
            title=title,
            summary=summary,
            template=template,
            visibility=visibility,
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            expected_revision=expected_revision,
            publisher=publisher,
            protection=protection,
        )

    return _execute_development(
        "create",
        application_id,
        actor_ref=actor_ref,
        subnet_ref=subnet_ref,
        capability=capability,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        intent=intent,
        callback=execute,
    )


def _delete_application_development_effect(
    application_id: str,
    *,
    expected_revision: int,
    project_id: str,
    manifest_digest: str,
    primary_ref: str,
    owned_refs: Sequence[str],
) -> Mapping[str, Any]:
    from adaos.sdk.developer import compositions, projects

    service = _application_service()
    try:
        application = service.store.get_application(application_id)
    except FileNotFoundError:
        application = None
    if application is not None:
        if application.protection.get("system_application"):
            raise ValueError("system Application development cannot be deleted")
        if application.legacy_project_id != project_id:
            raise ValueError("Application DEV Project identity changed")
        definition = service.store.delete_unpublished_application(
            application_id,
            expected_revision=expected_revision,
        )
    else:
        definition = {
            "ok": True,
            "application_id": application_id,
            "definition_removed": True,
            "duplicate": True,
        }

    try:
        project = compositions.get(project_id)
    except (FileNotFoundError, compositions.ProjectCompositionNotFound):
        project = None
    if project is not None:
        observed_refs = [
            str(item.get("ref") or "").strip()
            for item in project.get("components", {}).get("owned", ())
        ]
        if observed_refs != list(owned_refs):
            raise ValueError("Application DEV Project ownership changed")
        composition = compositions.delete(
            project_id,
            expected_manifest_digest=manifest_digest,
            expected_primary_ref=primary_ref,
        )
    else:
        composition = {
            "ok": True,
            "project_id": project_id,
            "local_removed": True,
            "duplicate": True,
        }

    components = []
    for ref in owned_refs:
        kind, separator, component_id = str(ref).partition(":")
        if separator != ":" or kind not in {"skill", "scenario"} or not component_id:
            raise ValueError("Application DEV Project contains an unsupported owned component")
        try:
            result = projects.delete(kind, component_id, remove_local=True)
        except (FileNotFoundError, projects.ProjectNotFoundError):
            result = {
                "ok": True,
                "kind": kind,
                "project_id": component_id,
                "local_removed": True,
                "duplicate": True,
            }
        components.append(result)
    return {
        "ok": True,
        "application_id": application_id,
        "definition": definition,
        "project": composition,
        "components": components,
    }


def delete_application_development(
    application_id: str,
    *,
    expected_manifest_digest: str,
    expected_primary_ref: str,
    confirmed: bool,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    if confirmed is not True:
        raise ValueError("explicit deletion confirmation is required")
    application = _application(application_id, expected_revision)
    if application.protection.get("system_application"):
        raise ValueError("system Application development cannot be deleted")
    snapshot = _development_project_snapshot(application)
    if snapshot["manifest_digest"] != str(expected_manifest_digest or "").strip():
        raise ValueError("Application DEV Project changed since confirmation")
    if snapshot["primary_ref"] != str(expected_primary_ref or "").strip():
        raise ValueError("Application DEV primary component changed since confirmation")
    intent = {
        **snapshot,
        "confirmed": True,
    }

    def execute() -> Mapping[str, Any]:
        return _delete_application_development_effect(
            application_id,
            expected_revision=expected_revision,
            project_id=str(snapshot["project_id"]),
            manifest_digest=str(snapshot["manifest_digest"]),
            primary_ref=str(snapshot["primary_ref"]),
            owned_refs=tuple(snapshot["owned_refs"]),
        )

    return _execute_development(
        "delete",
        application_id,
        actor_ref=actor_ref,
        subnet_ref=subnet_ref,
        capability=capability,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        intent=intent,
        callback=execute,
    )


def _update_application_metadata_effect(
    application_id: str,
    *,
    title: str,
    summary: str,
    categories: Sequence[str],
    expected_revision: int,
) -> Mapping[str, Any]:
    service = _application_service()
    application = service.store.get_application(application_id)
    updated = replace(
        application,
        display={
            "title": str(title),
            "summary": str(summary),
            "categories": [str(item) for item in categories],
        },
        revision=application.revision + 1,
        updated_at=utc_now(),
    )
    if application.revision == expected_revision + 1:
        if dict(application.display) != dict(updated.display):
            raise ValueError(
                "Application revision advanced with different catalog metadata"
            )
        return {"ok": True, "duplicate": True, "application": application.to_dict()}
    if application.revision != expected_revision:
        raise ValueError(
            f"Application revision conflict: expected {expected_revision}, "
            f"observed {application.revision}"
        )
    saved = service.register(updated, expected_revision=expected_revision)
    return {"ok": True, "application": saved.to_dict()}


def update_application_metadata(
    application_id: str,
    *,
    title: str,
    summary: str,
    categories: Sequence[str] = (),
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {
        "title": str(title),
        "summary": str(summary),
        "categories": [str(item) for item in categories],
    }

    def execute() -> Mapping[str, Any]:
        return _update_application_metadata_effect(
            application_id,
            title=str(intent["title"]),
            summary=str(intent["summary"]),
            categories=tuple(intent["categories"]),
            expected_revision=expected_revision,
        )

    return _execute_development(
        "update_metadata",
        application_id,
        actor_ref=actor_ref,
        subnet_ref=subnet_ref,
        capability=capability,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        intent=intent,
        callback=execute,
    )


def materialize_application(
    application_id: str,
    *,
    revision: str,
    source_webspace_id: str = "desktop",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"revision": revision, "source_webspace_id": source_webspace_id}

    def execute() -> Mapping[str, Any]:
        from . import preview

        application = _application(application_id, expected_revision)
        scenario_id = _primary_scenario(application)
        ready = preview.ensure(
            source_webspace_id,
            active_draft_id=scenario_id,
            runtime_scenario_id=scenario_id,
            wait_for_rebuild=True,
        )
        result = preview.materialize_revision(
            webspace_id=preview.dev_webspace_id(source_webspace_id),
            scenario_id=scenario_id,
            revision=str(revision or "").strip() or None,
            preview_stage="prototype",
            preview_label=f"proto: {scenario_id}",
        )
        return {"ok": bool(result.get("ok", True)), "preview": ready, "materialization": result}

    return _execute_development(
        "materialize", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def preview_development(
    application_id: str,
    *,
    source_webspace_id: str = "desktop",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"source_webspace_id": source_webspace_id, "stage": "development"}

    def execute() -> Mapping[str, Any]:
        from . import preview

        application = _application(application_id, expected_revision)
        return preview.select_project(
            "scenario",
            _primary_scenario(application),
            source_webspace_id=source_webspace_id,
            ensure_ready=True,
            wait_for_rebuild=True,
        )

    return _execute_development(
        "preview", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def create_trial(
    application_id: str,
    *,
    source_webspace_id: str = "desktop",
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
    permission_decision: bool | Mapping[str, Any] | None = None,
    verification_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    application = _application(application_id, expected_revision)
    scenario_id = _primary_scenario(application)
    resolved_verification_evidence = (
        dict(verification_evidence)
        if isinstance(verification_evidence, Mapping) and verification_evidence
        else None
    )
    if resolved_verification_evidence is None:
        from . import automation

        resolved_verification_evidence = automation.trial_verification_evidence(
            object_type="scenario",
            object_id=scenario_id,
            webspace_id=source_webspace_id,
        )
        if resolved_verification_evidence.get("ok") is not True:
            raise ValueError(
                "Builder Trial verification evidence is unavailable: "
                + str(
                    resolved_verification_evidence.get("reason")
                    or resolved_verification_evidence.get("status")
                    or "unknown"
                )
            )
    intent = {
        "source_webspace_id": source_webspace_id,
        "permission_decision": (
            dict(permission_decision)
            if isinstance(permission_decision, Mapping)
            else permission_decision
        ),
        "verification_evidence": resolved_verification_evidence,
    }

    def execute() -> Mapping[str, Any]:
        from . import lifecycle

        application = _application(application_id, expected_revision)
        return lifecycle.prepare_trial(
            "scenario",
            _primary_scenario(application),
            actor=actor_ref,
            idempotency_key=idempotency_key,
            source_webspace_id=source_webspace_id,
            publication_project_ref=f"project:{application.legacy_project_id}",
            permission_decision=permission_decision,
            verification_evidence=resolved_verification_evidence,
        )

    return _execute_development(
        "create_trial", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def decide_trial(
    application_id: str,
    *,
    accepted: bool,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"accepted": bool(accepted)}

    def execute() -> Mapping[str, Any]:
        from . import lifecycle

        application = _application(application_id, expected_revision)
        return lifecycle.decide_trial(
            "scenario", _primary_scenario(application), accepted=accepted,
            actor=actor_ref, idempotency_key=idempotency_key,
        )

    return _execute_development(
        "decide_trial", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def _publish_trial(
    application_id: str,
    candidate_id: str,
    *,
    mode: str,
    expected_prerelease_digest: str | None,
    addresses_report_ids: Sequence[str],
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    action = "publish_prerelease" if mode == "prerelease" else "publish_trial"
    bounded_reports = tuple(sorted({str(item).strip() for item in addresses_report_ids if str(item).strip()}))
    if len(bounded_reports) > 200:
        raise ValueError("addresses_report_ids exceeds 200 items")
    intent = {
        "candidate_id": candidate_id,
        "mode": mode,
        "expected_prerelease_digest": expected_prerelease_digest,
        "addresses_report_ids": list(bounded_reports),
    }

    def execute() -> Mapping[str, Any]:
        application = _application(application_id, expected_revision)
        if application.publisher_ref != subnet_ref:
            raise ValueError("only the local Application publisher may publish a Trial")
        return _distribution_service().publish_trial(
            application_id,
            candidate_id,
            publisher_ref=subnet_ref,
            mode=mode,
            expected_prerelease_digest=expected_prerelease_digest,
            addresses_report_ids=bounded_reports,
        )

    return _execute_development(
        action, application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def publish_link_trial(
    application_id: str,
    candidate_id: str,
    *,
    addresses_report_ids: Sequence[str] = (),
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    return _publish_trial(
        application_id, candidate_id, mode="link_only", expected_prerelease_digest=None,
        addresses_report_ids=addresses_report_ids, actor_ref=actor_ref,
        subnet_ref=subnet_ref, capability=capability,
        expected_revision=expected_revision, idempotency_key=idempotency_key,
    )


def publish_prerelease(
    application_id: str,
    candidate_id: str,
    *,
    expected_prerelease_digest: str | None,
    addresses_report_ids: Sequence[str] = (),
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    return _publish_trial(
        application_id, candidate_id, mode="prerelease",
        expected_prerelease_digest=expected_prerelease_digest,
        addresses_report_ids=addresses_report_ids, actor_ref=actor_ref,
        subnet_ref=subnet_ref, capability=capability,
        expected_revision=expected_revision, idempotency_key=idempotency_key,
    )


def promote_stable(
    application_id: str,
    candidate_id: str,
    *,
    expected_stable_digest: str | None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    intent = {"candidate_id": candidate_id, "expected_stable_digest": expected_stable_digest}

    def execute() -> Mapping[str, Any]:
        application = _application(application_id, expected_revision)
        if application.publisher_ref != subnet_ref:
            raise ValueError("only the local Application publisher may promote stable")
        distribution = _distribution_service()
        candidate = distribution.candidates.load(candidate_id)
        selections = [
            item
            for item in distribution.applications.store.list_runtime_selections()
            if item.application_id == application_id
            and item.release_digest == candidate.release_digest
            and item.source in {"local_trial", "stable_installation"}
        ]
        if len(selections) != 1:
            raise ValueError(
                "Application stable promotion requires one unambiguous exact Trial "
                "RuntimeSelection"
            )
        publication_verification = _promote_local_trial_final_verification(
            ApplicationAccessManagementService(distribution.applications),
            application_id=application_id,
            webspace_id=selections[0].webspace_id,
            candidate_id=candidate_id,
            candidate_digest=candidate.package_digest,
            release_digest=candidate.release_digest,
            actor_ref=actor_ref,
            allow_completed=True,
        )
        channels = (
            distribution.applications.store.get_channels(application_id).get(
                "channels"
            )
            or {}
        )
        # A Builder Candidate initially exists only in the publisher's local
        # release cache.  Project publication is what uploads its immutable
        # closure, attestations and exact attestation-set binding.  Application
        # distribution intentionally verifies those remote facts, so Finalize
        # must establish them before it creates the link/prerelease projection.
        project_publication = projects.promote_candidate(
            candidate_id,
            permission_decision={
                "approved": True,
                "actor": actor_ref,
                "actor_type": "user",
                "approval_id": f"application:{application_id}:{candidate_id}:stable",
            },
        )
        project_status = str(project_publication.get("status") or "").strip().lower()
        if project_status == "stale" or project_publication.get("error"):
            raise ValueError(
                "Application stable promotion could not publish the exact Project "
                f"Candidate: {project_status or project_publication.get('error')}"
            )

        mode = "prerelease" if channels.get("stable") else "link_only"
        trial_publication = distribution.publish_trial(
            application_id,
            candidate_id,
            publisher_ref=subnet_ref,
            mode=mode,
            expected_prerelease_digest=(
                str(channels.get("prerelease") or "").strip() or None
            ),
        )
        promoted = distribution.promote_stable(
            application_id,
            candidate_id,
            publisher_ref=subnet_ref,
            expected_stable_digest=expected_stable_digest,
        )
        return {
            **dict(promoted),
            "publication_verification": dict(publication_verification),
            "project_publication": dict(project_publication),
            "trial_publication": dict(trial_publication),
        }

    return _execute_development(
        "promote_stable", application_id, actor_ref=actor_ref, subnet_ref=subnet_ref,
        capability=capability, expected_revision=expected_revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute,
    )


def publish_stable_source(
    application_id: str,
    release_digest: str,
    *,
    release_notes: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    if len(str(release_notes)) > 20_000:
        raise ValueError("release_notes exceeds 20000 characters")
    intent = {"release_digest": release_digest, "release_notes": str(release_notes)}

    def execute() -> Mapping[str, Any]:
        _application(application_id, expected_revision)
        return StableSourceProjectionService(
            _application_service(), publisher=get_stable_source_publisher()
        ).publish(
            application_id,
            release_digest,
            publisher_ref=subnet_ref,
            release_notes=release_notes,
        )

    return _execute_development(
        "publish_stable_source", application_id, actor_ref=actor_ref,
        subnet_ref=subnet_ref, capability=capability,
        expected_revision=expected_revision, idempotency_key=idempotency_key,
        intent=intent, callback=execute,
    )


def _publish_to_registry_effect(
    application_id: str,
    release_digest: str,
    *,
    release_notes: str,
    subnet_ref: str,
    expected_revision: int,
) -> Mapping[str, Any]:
    """Make one publisher-owned stable Application public and project it.

    The visibility transition is deliberately resumable.  If publication loses
    its response after the Application became public, recovery observes the
    exact next revision and retries only the idempotent registry projection.
    """

    service = _application_service()
    application = service.store.get_application(application_id)
    if application.publisher_ref != subnet_ref:
        raise ValueError("only the local Application publisher may publish to registry")
    if application.visibility == "public":
        if application.revision not in {expected_revision, expected_revision + 1}:
            raise ValueError(
                f"Application revision conflict: expected {expected_revision} or "
                f"{expected_revision + 1}, observed {application.revision}"
            )
    else:
        if application.revision != expected_revision:
            raise ValueError(
                f"Application revision conflict: expected {expected_revision}, "
                f"observed {application.revision}"
            )
        application = service.register(
            replace(
                application,
                visibility="public",
                revision=application.revision + 1,
                updated_at=utc_now(),
            ),
            expected_revision=expected_revision,
        )
    receipt = StableSourceProjectionService(
        service, publisher=get_stable_source_publisher()
    ).publish(
        application_id,
        release_digest,
        publisher_ref=subnet_ref,
        release_notes=release_notes,
        require_application_catalog=True,
    )
    return {
        "ok": True,
        "application": application.to_dict(),
        "registry_publication": receipt,
    }


def publish_to_registry(
    application_id: str,
    release_digest: str,
    *,
    release_notes: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """Publish one exact stable Application and its portable CBS projection."""

    if len(str(release_notes)) > 20_000:
        raise ValueError("release_notes exceeds 20000 characters")
    intent = {"release_digest": release_digest, "release_notes": str(release_notes)}

    def execute() -> Mapping[str, Any]:
        return _publish_to_registry_effect(
            application_id,
            release_digest,
            release_notes=release_notes,
            subnet_ref=subnet_ref,
            expected_revision=expected_revision,
        )

    return _execute_development(
        "publish_to_registry", application_id, actor_ref=actor_ref,
        subnet_ref=subnet_ref, capability=capability,
        expected_revision=expected_revision, idempotency_key=idempotency_key,
        intent=intent, callback=execute,
    )


def _replay_development_operation(operation: Mapping[str, Any]) -> Mapping[str, Any]:
    action = str(operation.get("action") or "")
    application_id = str(operation.get("application_id") or "")
    actor_ref = str(operation.get("actor_ref") or "")
    subnet_ref = str(operation.get("subnet_ref") or "")
    expected_revision = int(operation.get("expected_revision") or 0)
    idempotency_key = str(operation.get("idempotency_key") or "")
    raw_intent = operation.get("intent")
    if not application_id or not isinstance(raw_intent, Mapping):
        raise ValueError("stored Application development intent is invalid")
    intent = dict(raw_intent)

    if action == "create":
        current_publisher = publisher_context()
        stored_publisher = intent.get("publisher")
        publisher = (
            dict(stored_publisher)
            if isinstance(stored_publisher, Mapping)
            else current_publisher
        )
        if (
            publisher.get("publisher_ref") != subnet_ref
            or publisher.get("release_key_fingerprint")
            != intent.get("publisher_key_fingerprint")
        ):
            raise ValueError("stored publisher identity no longer matches the create intent")
        return _create_application_effect(
            application_id,
            title=str(intent.get("title") or ""),
            summary=str(intent.get("summary") or ""),
            template=str(intent.get("template") or "empty"),
            visibility=str(intent.get("visibility") or "private"),
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            expected_revision=expected_revision,
            publisher=publisher,
            protection=(
                dict(intent["protection"])
                if isinstance(intent.get("protection"), Mapping)
                else None
            ),
        )
    if action == "update_metadata":
        return _update_application_metadata_effect(
            application_id,
            title=str(intent.get("title") or ""),
            summary=str(intent.get("summary") or ""),
            categories=tuple(intent.get("categories") or ()),
            expected_revision=expected_revision,
        )
    if action == "delete":
        return _delete_application_development_effect(
            application_id,
            expected_revision=expected_revision,
            project_id=str(intent.get("project_id") or ""),
            manifest_digest=str(intent.get("manifest_digest") or ""),
            primary_ref=str(intent.get("primary_ref") or ""),
            owned_refs=tuple(intent.get("owned_refs") or ()),
        )
    if action in {"materialize", "preview"}:
        from . import preview

        application = _application(application_id, expected_revision)
        scenario_id = _primary_scenario(application)
        source_webspace_id = str(intent.get("source_webspace_id") or "desktop")
        if action == "preview":
            return preview.select_project(
                "scenario",
                scenario_id,
                source_webspace_id=source_webspace_id,
                ensure_ready=True,
                wait_for_rebuild=True,
            )
        ready = preview.ensure(
            source_webspace_id,
            active_draft_id=scenario_id,
            runtime_scenario_id=scenario_id,
            wait_for_rebuild=True,
        )
        result = preview.materialize_revision(
            webspace_id=preview.dev_webspace_id(source_webspace_id),
            scenario_id=scenario_id,
            revision=str(intent.get("revision") or "").strip() or None,
            preview_stage="prototype",
            preview_label=f"proto: {scenario_id}",
        )
        return {
            "ok": bool(result.get("ok", True)),
            "preview": ready,
            "materialization": result,
        }
    if action in {"create_trial", "decide_trial"}:
        from . import lifecycle

        application = _application(application_id, expected_revision)
        scenario_id = _primary_scenario(application)
        if action == "create_trial":
            verification_evidence = (
                dict(intent["verification_evidence"])
                if isinstance(intent.get("verification_evidence"), Mapping)
                and intent.get("verification_evidence")
                else None
            )
            if verification_evidence is None:
                from . import automation

                verification_evidence = automation.trial_verification_evidence(
                    object_type="scenario",
                    object_id=scenario_id,
                    webspace_id=str(
                        intent.get("source_webspace_id") or "desktop"
                    ),
                )
                if verification_evidence.get("ok") is not True:
                    raise ValueError(
                        "Builder Trial verification evidence is unavailable: "
                        + str(
                            verification_evidence.get("reason")
                            or verification_evidence.get("status")
                            or "unknown"
                        )
                    )
            return lifecycle.prepare_trial(
                "scenario",
                scenario_id,
                actor=actor_ref,
                idempotency_key=idempotency_key,
                source_webspace_id=str(intent.get("source_webspace_id") or "desktop"),
                publication_project_ref=f"project:{application.legacy_project_id}",
                permission_decision=(
                    intent.get("permission_decision")
                    if isinstance(intent.get("permission_decision"), (bool, Mapping))
                    else None
                ),
                verification_evidence=verification_evidence,
            )
        return lifecycle.decide_trial(
            "scenario",
            scenario_id,
            accepted=bool(intent.get("accepted")),
            actor=actor_ref,
            idempotency_key=idempotency_key,
        )
    if action in {"publish_trial", "publish_prerelease", "promote_stable"}:
        application = _application(application_id, expected_revision)
        if application.publisher_ref != subnet_ref:
            raise ValueError("only the local Application publisher may recover publication")
        distribution = _distribution_service()
        candidate_id = str(intent.get("candidate_id") or "")
        try:
            distribution.reconcile(candidate_id)
        except FileNotFoundError:
            pass
        if action == "promote_stable":
            return distribution.promote_stable(
                application_id,
                candidate_id,
                publisher_ref=subnet_ref,
                expected_stable_digest=(
                    str(intent.get("expected_stable_digest") or "").strip() or None
                ),
            )
        return distribution.publish_trial(
            application_id,
            candidate_id,
            publisher_ref=subnet_ref,
            mode=str(intent.get("mode") or "link_only"),
            expected_prerelease_digest=(
                str(intent.get("expected_prerelease_digest") or "").strip() or None
            ),
            addresses_report_ids=tuple(intent.get("addresses_report_ids") or ()),
        )
    if action == "publish_stable_source":
        _application(application_id, expected_revision)
        return StableSourceProjectionService(
            _application_service(), publisher=get_stable_source_publisher()
        ).publish(
            application_id,
            str(intent.get("release_digest") or ""),
            publisher_ref=subnet_ref,
            release_notes=str(intent.get("release_notes") or ""),
        )
    if action == "publish_to_registry":
        return _publish_to_registry_effect(
            application_id,
            str(intent.get("release_digest") or ""),
            release_notes=str(intent.get("release_notes") or ""),
            subnet_ref=subnet_ref,
            expected_revision=expected_revision,
        )
    raise ValueError(f"unsupported stored Application development action: {action}")


def reconcile_development_operation(
    operation_id: str,
    *,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
) -> dict[str, Any]:
    coordinator = _coordinator()
    operation = coordinator.get(operation_id)
    _admit_builder_mutation(
        "recover",
        str(operation.get("application_id") or ""),
        subnet_ref=subnet_ref,
        capability=capability,
    )
    return coordinator.recover(
        operation_id,
        actor_ref=actor_ref,
        subnet_ref=subnet_ref,
        capability=capability,
        callback=_replay_development_operation,
    )


def get_development_operation(operation_id: str) -> dict[str, Any]:
    return _coordinator().get(operation_id)


def list_development_operations(application_id: str | None = None) -> list[dict[str, Any]]:
    return _coordinator().list(application_id)


__all__ = [
    "abort_local_trial_preparation",
    "accept_local_trial",
    "production_webspace_id",
    "project_access_contract",
    "place_local_trial",
    "place_local_stable",
    "refresh_placement",
    "open_trial_placement",
    "create_application",
    "delete_application_development",
    "create_trial",
    "decide_trial",
    "get_development_operation",
    "list_development_operations",
    "materialize_application",
    "preview_development",
    "promote_stable",
    "publish_link_trial",
    "publish_prerelease",
    "publish_to_registry",
    "publish_stable_source",
    "publisher_context",
    "reconcile_development_operation",
    "update_application_metadata",
    "verify_candidate_access",
]
