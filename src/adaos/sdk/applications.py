"""Public product-level Application lifecycle facade.

The facade exposes typed records and reviewed operations only. It never accepts
filesystem paths, process commands, Git credentials, or raw registry writes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from pathlib import Path
from typing import Any
import uuid

from adaos.domain.application import RuntimeSelection, utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.sdk.core._ctx import require_ctx
from adaos.services.application_registry_projection import ApplicationRegistryProjection
from adaos.services.applications import (
    ApplicationAccessManagementService,
    ApplicationAccessService,
    ApplicationDevelopmentCoordinator,
    ApplicationRolloutService,
    ApplicationSetupStateStore,
    DevelopmentReportTriageService,
    TrialAccessService,
    get_application_service,
    get_development_report_service,
    project_setup_state,
)
from adaos.services.applications.configuration import ApplicationConfigurationStore
from adaos.services.applications.conditions import enrich_application_conditions
from adaos.services.applications.runtime_credentials import (
    ApplicationRuntimeCredentials,
)
from adaos.services.applications.update_batches import (
    ApplicationUpdateBatchStore,
    update_batch_id,
)
from adaos.services.builder.workbench import BuilderWorkbenchService
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService
from adaos.services.io_web.desktop import WebDesktopInstalled, WebDesktopService
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.project_deployment import (
    ProjectDeploymentStore,
    ProjectDeploymentStoreError,
)


def _state_dir() -> Path:
    """Return the platform authority state, not an isolated Trial data store."""

    ctx = require_ctx("sdk.applications")
    raw = getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir()
    return Path(raw).expanduser().resolve()


def _service():
    return get_application_service(_state_dir())


def _access_management() -> ApplicationAccessManagementService:
    return ApplicationAccessManagementService(_service())


def _local_subnet_ref() -> str:
    config = require_ctx("sdk.applications").config
    subnet_id = str(
        config.subnet_id_value
        if hasattr(config, "subnet_id_value")
        else config.subnet_id
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
        components.append(
            {
                key: deepcopy(item[key])
                for key in (
                    "kind",
                    "artifact_id",
                    "version",
                    "digest",
                    "manifest_digest",
                    "builder_id",
                    "build_policy_digest",
                    "schema_locks",
                    "conversational_lock",
                    "workflow_lock",
                    "workflow_validation_lock",
                    "workflow_adapter_locks",
                    "workflow_binding_digest",
                    "workflow_role_policy_digest",
                )
                if key in item
            }
        )
    dependencies = []
    for item in project.get("resolved_dependencies") or ():
        if not isinstance(item, Mapping):
            continue
        dependencies.append(
            {
                key: deepcopy(item[key])
                for key in (
                    "kind",
                    "artifact_id",
                    "version",
                    "package_digest",
                    "version_spec",
                    "optional",
                )
                if key in item
            }
        )
    composition = project.get("composition_lock")
    composition = dict(composition) if isinstance(composition, Mapping) else {}
    safe_composition = {
        key: deepcopy(composition[key])
        for key in (
            "schema",
            "project_definition_digest",
            "profiles",
            "members",
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
            "schema",
            "project_id",
            "version",
            "permissions",
            "schema_locks",
            "migration_locks",
            "validation_evidence_refs",
            "release_digest",
            "catalog",
        )
        if key in project
    }
    safe_project.update(
        {
            "components": components,
            "resolved_dependencies": dependencies,
            "composition_lock": safe_composition or None,
            "migration": {
                "required": bool(project.get("migrations")),
                "count": len(project.get("migrations") or ()),
            },
            "validation_evidence_count": len(project.get("validation_evidence") or ()),
            "private_source": "redacted",
        }
    )
    return (
        {
            key: deepcopy(raw[key])
            for key in (
                "schema",
                "application_id",
                "publisher_ref",
                "legacy_project_id",
                "version",
                "release_digest",
                "accepted_candidate_id",
                "provenance_refs",
                "addresses_report_ids",
                "lifecycle",
                "published_at",
                "channels",
            )
            if key in raw
        }
        | {
            "project_release": safe_project,
            "acceptance_evidence_count": len(raw.get("acceptance_evidence") or ()),
        }
        | (
            {
                "setup_contract": deepcopy(raw.get("setup_contract")),
                "setup_contract_digest": raw.get("setup_contract_digest"),
            }
            if raw.get("setup_contract") is not None
            else {}
        )
    )


def _workspace_project_components(project: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project legacy ownership into the release component read model."""

    raw = project.get("components")
    groups = raw if isinstance(raw, Mapping) else {}
    components: list[dict[str, Any]] = []
    for ownership, group in (
        ("owned", groups.get("owned") or ()),
        ("dependency", groups.get("dependencies") or groups.get("required") or ()),
    ):
        for item in group:
            if not isinstance(item, Mapping):
                continue
            component_ref = str(item.get("ref") or "").strip()
            kind, separator, artifact_id = component_ref.partition(":")
            if not separator:
                kind = str(item.get("kind") or "component").strip()
                artifact_id = str(
                    item.get("artifact_id") or item.get("id") or component_ref
                ).strip()
                component_ref = f"{kind}:{artifact_id}" if artifact_id else kind
            components.append(
                {
                    "kind": kind,
                    "artifact_id": artifact_id,
                    "component_ref": component_ref,
                    "ownership": ownership,
                    **{
                        key: deepcopy(item[key])
                        for key in ("version", "digest", "optional", "role")
                        if key in item
                    },
                }
            )
    return components


def _application_read_model(value: Mapping[str, Any]) -> dict[str, Any]:
    model = deepcopy(dict(value))
    application = model.get("application")
    if isinstance(application, dict):
        application.setdefault("aggregate_backed", True)
    for field in (
        "installed_release",
        "local_beta_release",
        "active_release",
        "marketplace_release",
        "prerelease_release",
    ):
        if isinstance(model.get(field), Mapping):
            model[field] = _release_read_model(model[field])
    local_beta_releases = model.get("local_beta_releases")
    if isinstance(local_beta_releases, list):
        model["local_beta_releases"] = [
            _release_read_model(item)
            for item in local_beta_releases
            if isinstance(item, Mapping)
        ]
    effective = model.get("effective_release")
    if isinstance(effective, dict) and isinstance(effective.get("release"), Mapping):
        effective["release"] = _release_read_model(effective["release"])
    return model


def _installation_summary(
    model: Mapping[str, Any], *, webspace_id: str | None
) -> dict[str, Any]:
    """Project persisted installations and active Trial runtimes uniformly."""

    installation = (
        dict(model.get("installation") or {})
        if isinstance(model.get("installation"), Mapping)
        else {}
    )
    subscription = (
        dict(model.get("subscription") or {})
        if isinstance(model.get("subscription"), Mapping)
        else {}
    )
    installed_release = (
        dict(model.get("installed_release") or {})
        if isinstance(model.get("installed_release"), Mapping)
        else {}
    )
    selections = [
        dict(item)
        for item in model.get("runtime_selections") or ()
        if isinstance(item, Mapping)
    ]
    webspace = str(webspace_id or "").strip()
    active_runtime = next(
        (
            item
            for item in selections
            if webspace and str(item.get("webspace_id") or "") == webspace
        ),
        selections[0] if selections else {},
    )
    local_trial = str(active_runtime.get("source") or "") == "local_trial"
    active_release = _active_release_for_webspace(
        model, webspace_id=webspace_id, active_runtime=active_runtime
    )
    installed = bool(model.get("installed"))
    if local_trial:
        status = "beta_active"
        source = "local_trial"
    elif installation:
        status = str(installation.get("status") or "active")
        source = "installation"
    else:
        status = "not_installed"
        source = None
    return {
        "schema": "adaos.application.installation_summary.v1",
        "installed": installed,
        "status": status,
        "source": source,
        "version": active_release.get("version"),
        "release_digest": (
            active_runtime.get("release_digest")
            if local_trial
            else installation.get("installed_release_digest")
            or installed_release.get("release_digest")
        ),
        "updated_at": (
            active_runtime.get("updated_at")
            if local_trial
            else installation.get("updated_at")
        ),
        "webspace_id": active_runtime.get("webspace_id") if active_runtime else None,
        "runtime_root_ref": (
            active_runtime.get("runtime_root_ref") if active_runtime else None
        ),
        "update_track": subscription.get("update_track"),
        "update_policy": subscription.get("update_policy"),
        "auto_update_enabled": bool(model.get("auto_update_enabled")),
        "local_beta_active": bool(model.get("local_beta_active")),
    }


def _active_release_for_webspace(
    model: Mapping[str, Any],
    *,
    webspace_id: str | None,
    active_runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve release metadata for the runtime selected in one Webspace."""

    selections = [
        dict(item)
        for item in model.get("runtime_selections") or ()
        if isinstance(item, Mapping)
    ]
    webspace = str(webspace_id or "").strip()
    selected = dict(active_runtime or {}) or next(
        (
            item
            for item in selections
            if webspace and str(item.get("webspace_id") or "") == webspace
        ),
        selections[0] if selections else {},
    )
    selected_digest = str(selected.get("release_digest") or "").strip()
    if str(selected.get("source") or "") == "local_trial" and selected_digest:
        for release in model.get("local_beta_releases") or ():
            if (
                isinstance(release, Mapping)
                and str(release.get("release_digest") or "").strip() == selected_digest
            ):
                return dict(release)
    for field in ("active_release", "installed_release"):
        release = model.get(field)
        if isinstance(release, Mapping):
            return dict(release)
    return {}


def _application_home_aliases(model: Mapping[str, Any]) -> tuple[str, ...]:
    application = (
        dict(model.get("application") or {})
        if isinstance(model.get("application"), Mapping)
        else {}
    )
    aliases: list[str] = []
    for entrypoint in application.get("entrypoints") or ():
        if not isinstance(entrypoint, Mapping):
            continue
        presentation_ref = str(entrypoint.get("presentation_ref") or "").strip()
        if presentation_ref:
            aliases.append(presentation_ref)
    application_id = str(application.get("application_id") or "").strip()
    legacy_project_id = str(application.get("legacy_project_id") or "").strip()
    if application_id:
        aliases.append(application_id)
        aliases.append(f"scenario:{application_id}")
    if legacy_project_id:
        aliases.append(legacy_project_id)
        aliases.append(f"scenario:{legacy_project_id}")
    return tuple(dict.fromkeys(item for item in aliases if item))


_HOME_SNAPSHOT_UNSET = object()


def _home_projection(
    model: Mapping[str, Any],
    webspace_id: str | None,
    *,
    snapshot: Any = _HOME_SNAPSHOT_UNSET,
) -> dict[str, Any]:
    webspace = str(webspace_id or "").strip()
    aliases = _application_home_aliases(model)
    if not webspace:
        return {
            "schema": "adaos.application.home_projection.v1",
            "webspace_id": None,
            "application_ref": aliases[0] if aliases else None,
            "installed": bool(model.get("installed")),
            "pinnable": bool(model.get("installed")),
            "pinned": False,
            "status": "webspace_not_selected",
        }
    if snapshot is _HOME_SNAPSHOT_UNSET:
        try:
            snapshot = WebDesktopService().get_snapshot(webspace)
        except (OSError, RuntimeError, ValueError):
            snapshot = None
    if snapshot is None:
        return {
            "schema": "adaos.application.home_projection.v1",
            "webspace_id": webspace,
            "application_ref": aliases[0] if aliases else None,
            "installed": bool(model.get("installed")),
            "pinnable": bool(model.get("installed")),
            "pinned": False,
            "status": "unavailable",
        }
    installed = set(snapshot.installed.apps)
    pinned = set(snapshot.pinned_applications)
    application_ref = next((item for item in aliases if item in installed), None)
    if application_ref is None:
        application_ref = next((item for item in aliases if item in pinned), None)
    if application_ref is None and aliases:
        application_ref = aliases[0]
    is_installed = bool(installed.intersection(aliases))
    return {
        "schema": "adaos.application.home_projection.v1",
        "webspace_id": webspace,
        "application_ref": application_ref,
        "installed": is_installed,
        "pinnable": bool(model.get("installed")) and is_installed,
        "pinned": bool(pinned.intersection(aliases)),
        "status": "ready",
    }


def _empty_execution_placement(
    application_id: str, *, status: str, managed: bool, partial: bool
) -> dict[str, Any]:
    deployment_id = f"application-deployment:{application_id}"
    return {
        "schema": "adaos.application.execution_placement.v1",
        "installation_scope": "subnet",
        "deployment_id": deployment_id,
        "managed": managed,
        "status": status,
        "desired": [],
        "observed": [],
        "partial": partial,
    }


def _execution_placement_payload(
    desired: Any,
    activations: Sequence[Any],
    *,
    partial: bool,
) -> dict[str, Any]:
    return {
        "schema": "adaos.application.execution_placement.v1",
        "installation_scope": "subnet",
        "deployment_id": desired.deployment_id,
        "project_ref": desired.project_ref,
        "managed": True,
        "status": desired.status,
        "revision": desired.revision,
        "desired": [
            {
                "component_ref": item.component_ref,
                "mode": item.mode,
                "selected_node_ids": list(item.selected_node_ids),
                "min_instances": item.min_instances,
                "max_instances": item.max_instances,
            }
            for item in desired.placements
        ],
        "observed": [
            {
                "component_ref": item.component_ref,
                "node_id": item.node_id,
                "status": item.status,
                "generation": item.generation,
                "updated_at": item.updated_at,
            }
            for item in activations
        ],
        "desired_component_count": len(desired.placements),
        "observed_instance_count": len(activations),
        "observed_active_count": sum(
            1 for item in activations if item.status == "active"
        ),
        "observed_node_ids": sorted({item.node_id for item in activations}),
        "partial": partial,
        "updated_at": desired.updated_at,
    }


def _execution_placement_read_model(
    application_id: str,
    *,
    store: ProjectDeploymentStore | None = None,
) -> dict[str, Any]:
    deployment_id = f"application-deployment:{application_id}"
    try:
        store = store or ProjectDeploymentStore(state_dir=_state_dir())
        desired = store.get_deployment(deployment_id)
        activations, next_cursor = store.list_activations(
            deployment_id=deployment_id,
            limit=200,
        )
    except FileNotFoundError:
        return _empty_execution_placement(
            application_id,
            status="not_materialized",
            managed=False,
            partial=False,
        )
    except (OSError, RuntimeError, ValueError, ProjectDeploymentStoreError):
        return _empty_execution_placement(
            application_id,
            status="unavailable",
            managed=True,
            partial=True,
        )
    return _execution_placement_payload(
        desired,
        activations,
        partial=next_cursor is not None,
    )


def _execution_placement_index(
    application_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    requested = {
        f"application-deployment:{application_id}": application_id
        for application_id in application_ids
        if application_id
    }
    if not requested:
        return {}
    try:
        store = ProjectDeploymentStore(state_dir=_state_dir())
        deployments: dict[str, Any] = {}
        cursor: str | None = None
        while True:
            page, cursor = store.list_deployments(cursor=cursor, limit=1000)
            deployments.update(
                (item.deployment_id, item)
                for item in page
                if item.deployment_id in requested
            )
            if cursor is None:
                break
        activations_by_deployment: dict[str, list[Any]] = {}
        cursor = None
        while True:
            page, cursor = store.list_activations(cursor=cursor, limit=1000)
            for item in page:
                if item.deployment_id in requested:
                    activations_by_deployment.setdefault(item.deployment_id, []).append(
                        item
                    )
            if cursor is None:
                break
    except (OSError, RuntimeError, ValueError, ProjectDeploymentStoreError):
        return {
            application_id: _empty_execution_placement(
                application_id,
                status="unavailable",
                managed=True,
                partial=True,
            )
            for application_id in requested.values()
        }
    return {
        application_id: (
            _execution_placement_payload(
                deployments[deployment_id],
                activations_by_deployment.get(deployment_id, ()),
                partial=False,
            )
            if deployment_id in deployments
            else _empty_execution_placement(
                application_id,
                status="not_materialized",
                managed=False,
                partial=False,
            )
        )
        for deployment_id, application_id in requested.items()
    }


def _workspace_project_read_models(
    existing: Sequence[Mapping[str, Any]],
    *,
    application_id: str | None = None,
) -> list[dict[str, Any]]:
    """Project manifests remain visible while their Application aggregate is migrated."""
    try:
        projects = ApplicationRegistryProjection(_state_dir()).list_workspace_projects(
            include_hidden=False,
            query=str(application_id or "").strip() or None,
        )
    except (OSError, RuntimeError, ValueError):
        return []
    claimed = {
        token
        for item in existing
        for token in (
            str((item.get("application") or {}).get("application_id") or "").strip(),
            str((item.get("application") or {}).get("legacy_project_id") or "").strip(),
        )
        if token
    }
    local_ref = _local_subnet_ref()
    local_publisher = next(
        (
            dict(application.get("publisher") or {})
            for item in existing
            if isinstance(item, Mapping)
            and isinstance((application := item.get("application")), Mapping)
            and str(application.get("publisher_ref") or "").lower() == local_ref.lower()
            and isinstance(application.get("publisher"), Mapping)
        ),
        {
            "publisher_ref": local_ref,
            "display_name": local_ref.removeprefix("subnet:"),
            "trust_relation": "local",
        },
    )
    models: list[dict[str, Any]] = []
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        if application_id is not None and project_id != application_id:
            continue
        if not project_id or project_id in claimed:
            continue
        entrypoints = [
            {
                "entrypoint_id": str(item.get("id") or "main"),
                "presentation_ref": str(item.get("presentation") or ""),
            }
            for item in project.get("entrypoints") or ()
            if isinstance(item, Mapping) and str(item.get("presentation") or "").strip()
        ]
        version = str(project.get("version") or "").strip()
        project_components = _workspace_project_components(project)
        models.append(
            {
                "application": {
                    "schema": "adaos.application.workspace_project_projection.v1",
                    "application_id": project_id,
                    "legacy_project_id": project_id,
                    "publisher_ref": local_ref,
                    "slug": project_id,
                    "display": {
                        "title": str(project.get("title") or project_id),
                        "summary": str(project.get("description") or ""),
                        "categories": list(project.get("categories") or ()),
                    },
                    "visibility": "private",
                    "catalog_visibility": str(project.get("visibility") or "unlisted"),
                    "entrypoints": entrypoints,
                    "publisher": deepcopy(local_publisher),
                    "protection": {
                        "system_application": False,
                        "bootstrap_capable": False,
                        "active_installation_removable": False,
                        "recovery_surfaces": ["cli"],
                    },
                    "lifecycle": "active",
                    "aggregate_backed": False,
                    "revision": 0,
                },
                "installed": True,
                "available": True,
                "update_available": False,
                "pinned": False,
                "prerelease_following": False,
                "use_prerelease": False,
                "local_beta_active": False,
                "runtime_selections": [],
                "auto_update_enabled": False,
                "retired": False,
                "channels": {},
                "installation": {
                    "schema": "adaos.application.workspace_project_installation.v1",
                    "application_id": project_id,
                    "status": "active",
                    "revision": 0,
                },
                "installed_release": {
                    "schema": "adaos.application.workspace_project_release.v1",
                    "application_id": project_id,
                    "version": version,
                    "project_release": {
                        "project_id": project_id,
                        "version": version,
                        "components": project_components,
                        "resolved_dependencies": [
                            item
                            for item in project_components
                            if item.get("ownership") == "dependency"
                        ],
                    },
                },
                "effective_release": {
                    "application_id": project_id,
                    "version": version,
                    "update_track": "stable",
                    "reason": "workspace_project_projection",
                },
                "icon": str(project.get("icon") or "apps-outline"),
                "workspace_project": {
                    key: deepcopy(project[key])
                    for key in (
                        "id",
                        "version",
                        "profiles",
                        "stage",
                        "visibility",
                        "primary_ref",
                        "manifest_digest",
                        "permission_profile",
                        "application_roles",
                        "components",
                    )
                    if key in project
                },
                "local_development": None,
            }
        )
    return models


def list_development_projects(
    *,
    profile: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read sanitized local development metadata from the authority projection.

    Immutable Trial runtimes may inspect this projection, but never receive DEV
    filesystem paths or direct access to mutable source trees.
    """

    _admit_active_skill_capability("workspace.read")
    rows = ApplicationRegistryProjection(_state_dir()).list_development_projects(
        profile=str(profile or "").strip() or None,
        query=str(query or "").strip() or None,
        limit=max(1, min(int(limit), 5000)),
    )
    public_fields = (
        "id",
        "title",
        "title_i18n",
        "description",
        "description_i18n",
        "version",
        "updated_at",
        "stage",
        "visibility",
        "primary_ref",
        "profiles",
        "categories",
        "tags",
        "source_kind",
        "validation_status",
    )
    return [
        {
            **{key: deepcopy(row[key]) for key in public_fields if key in row},
            "status": "development",
        }
        for row in rows
        if isinstance(row, Mapping) and str(row.get("id") or "").strip()
    ]


def _local_development_index() -> dict[str, dict[str, Any]]:
    operations = ApplicationDevelopmentCoordinator(_state_dir()).list()
    local_subnet = _local_subnet_ref().lower()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        if str(operation.get("subnet_ref") or "").lower() != local_subnet:
            continue
        application_id = str(operation.get("application_id") or "").strip()
        if application_id:
            grouped.setdefault(application_id, []).append(operation)
    projections: dict[str, dict[str, Any]] = {}
    for application_id, values in grouped.items():
        latest = max(
            values,
            key=lambda item: (
                str(item.get("updated_at") or ""),
                str(item.get("operation_id") or ""),
            ),
        )
        if latest.get("action") == "delete" and latest.get("status") == "succeeded":
            continue
        source_webspace_id = next(
            (
                str((item.get("intent") or {}).get("source_webspace_id") or "").strip()
                for item in sorted(
                    values,
                    key=lambda item: (
                        str(item.get("updated_at") or ""),
                        str(item.get("operation_id") or ""),
                    ),
                    reverse=True,
                )
                if isinstance(item.get("intent"), Mapping)
                and str(
                    (item.get("intent") or {}).get("source_webspace_id") or ""
                ).strip()
            ),
            "",
        )
        projections[application_id] = {
            "exists": True,
            "status": str(latest.get("status") or "unknown"),
            "latest_action": str(latest.get("action") or "unknown"),
            "updated_at": str(latest.get("updated_at") or ""),
            "operation_count": len(values),
            "source_webspace_id": source_webspace_id or None,
        }
    return projections


def _development_workflow_summary(
    object_type: str,
    object_id: str,
) -> dict[str, Any] | None:
    try:
        return BuilderWorkflowService.from_context().development_summary(
            object_type,
            object_id,
        )
    except (
        AttributeError,
        FileNotFoundError,
        BuilderWorkflowError,
        OSError,
        ValueError,
    ):
        return None


def _application_models(
    *,
    installed_only: bool,
    application_id: str | None = None,
) -> list[dict[str, Any]]:
    service = _service()
    if application_id is None:
        values = service.list_models(
            installed_only=installed_only,
            subscriber_subnet_ref=_local_subnet_ref(),
        )
    else:
        try:
            value = service.get_model(
                application_id,
                subscriber_subnet_ref=_local_subnet_ref(),
            )
        except FileNotFoundError:
            values = []
        else:
            values = [value] if not installed_only or value.get("installed") else []
    models = [_application_read_model(item) for item in values]
    if application_id is None:
        models.extend(_workspace_project_read_models(models))
    else:
        models.extend(
            _workspace_project_read_models(models, application_id=application_id)
        )
    return models


def _marketplace_listing(application: Mapping[str, Any]) -> dict[str, Any]:
    raw = str(application.get("catalog_visibility") or "").strip().lower()
    if not raw:
        raw = (
            "listed"
            if str(application.get("visibility") or "") == "public"
            else "unlisted"
        )
    status = "listed" if raw in {"listed", "public"} else "unlisted"
    return {
        "schema": "adaos.application.marketplace_listing.v1",
        "status": status,
        "listed": status == "listed",
    }


def _application_component_inventory(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    release: Mapping[str, Any] = {}
    release_source = "unavailable"
    for field in ("active_release", "installed_release", "marketplace_release"):
        candidate = model.get(field)
        if isinstance(candidate, Mapping):
            project_release = candidate.get("project_release")
            if isinstance(project_release, Mapping) and project_release.get(
                "components"
            ):
                release = project_release
                release_source = field
                break

    desired = {
        str(item.get("component_ref") or ""): dict(item)
        for item in (model.get("execution_placement") or {}).get("desired", ())
        if isinstance(item, Mapping) and str(item.get("component_ref") or "")
    }
    observed: dict[str, list[dict[str, Any]]] = {}
    for item in (model.get("execution_placement") or {}).get("observed", ()):
        if not isinstance(item, Mapping):
            continue
        component_ref = str(item.get("component_ref") or "")
        if component_ref:
            observed.setdefault(component_ref, []).append(dict(item))

    rows: list[dict[str, Any]] = []
    for raw in release.get("components") or ():
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("kind") or "component").strip()
        component_id = str(raw.get("artifact_id") or raw.get("id") or "").strip()
        component_ref = str(raw.get("component_ref") or "").strip()
        if not component_ref:
            component_ref = f"{kind}:{component_id}" if component_id else kind
        actual = observed.get(component_ref, [])
        plan = desired.get(component_ref, {})
        placement_mode = str(plan.get("mode") or "").strip() or None
        rows.append(
            {
                "component_ref": component_ref,
                "kind": kind,
                "component_id": component_id,
                "ownership": str(raw.get("ownership") or "owned"),
                "version": raw.get("version"),
                "digest": raw.get("digest") or raw.get("package_digest"),
                "source": release_source,
                "placement_mode": placement_mode,
                "placement_status": (
                    "disabled"
                    if placement_mode == "disabled"
                    else "active"
                    if any(str(item.get("status") or "") == "active" for item in actual)
                    else "desired"
                    if plan
                    else "not_placed"
                ),
                "installed": placement_mode != "disabled",
                "installable": placement_mode == "disabled",
                "relocatable": bool(plan) and placement_mode != "disabled",
                "desired_node_ids": list(plan.get("selected_node_ids") or ()),
                "observed_node_ids": sorted(
                    {
                        str(item.get("node_id") or "")
                        for item in actual
                        if str(item.get("node_id") or "")
                    }
                ),
                "runtime_status": (
                    "active"
                    if any(str(item.get("status") or "") == "active" for item in actual)
                    else str(
                        (actual[0] if actual else {}).get("status") or "not_observed"
                    )
                ),
                "deployment_revision": (model.get("execution_placement") or {}).get(
                    "revision"
                ),
            }
        )
    active_count = sum(1 for item in rows if item["installed"])
    for item in rows:
        item["removable"] = bool(
            item["installed"] and item["relocatable"] and active_count > 1
        )
    return rows


def _enrich_application_models(
    models: Sequence[dict[str, Any]],
    *,
    development: Mapping[str, Mapping[str, Any]],
    webspace_id: str | None,
) -> list[dict[str, Any]]:
    webspace = str(webspace_id or "").strip()
    home_snapshot: Any = None
    if webspace:
        try:
            home_snapshot = WebDesktopService().get_snapshot(webspace)
        except (OSError, RuntimeError, ValueError):
            home_snapshot = None
    application_ids = [
        str((model.get("application") or {}).get("application_id") or "").strip()
        for model in models
    ]
    placements = _execution_placement_index(application_ids)
    try:
        project_icons = {
            str(item.get("id") or "").strip(): str(item.get("icon") or "").strip()
            for item in ApplicationRegistryProjection(
                _state_dir()
            ).list_workspace_projects(include_hidden=True)
            if str(item.get("id") or "").strip()
        }
    except (OSError, RuntimeError, ValueError):
        project_icons = {}
    enriched: list[dict[str, Any]] = []
    for model in models:
        application = model.get("application") or {}
        application_id = str(application.get("application_id") or "")
        release_catalogs = [
            (
                ((model.get(field) or {}).get("project_release") or {}).get("catalog")
                or {}
            )
            for field in ("active_release", "marketplace_release", "installed_release")
        ]
        release_icon = next(
            (
                str(catalog.get("icon") or "").strip()
                for catalog in release_catalogs
                if isinstance(catalog, Mapping)
                and str(catalog.get("icon") or "").strip()
            ),
            "",
        )
        model["icon"] = (
            release_icon
            or str((application.get("display") or {}).get("icon") or "").strip()
            or project_icons.get(
                str(application.get("legacy_project_id") or "").strip(), ""
            )
            or "apps-outline"
        )
        local_source = development.get(application_id)
        local = deepcopy(dict(local_source)) if local_source is not None else None
        if local is not None:
            entrypoints = application.get("entrypoints") or []
            presentation_ref = str(
                (entrypoints[0] if entrypoints else {}).get("presentation_ref") or ""
            )
            object_type, _, object_id = presentation_ref.partition(":")
            source_webspace_id = str(
                local.pop("source_webspace_id", None) or ""
            ).strip()
            if not source_webspace_id and object_type and object_id:
                source_webspace_id = str(
                    BuilderWorkbenchService(
                        state_dir=_state_dir()
                    ).find_existing_source_for_selection(
                        object_type=object_type,
                        object_id=object_id,
                    )
                    or ""
                ).strip()
            local["builder"] = {
                "selected_object_type": object_type,
                "selected_object_id": object_id,
                "source_webspace_id": source_webspace_id or None,
                "preview_webspace_id": (
                    source_webspace_id
                    if source_webspace_id.endswith("-dev")
                    else f"{source_webspace_id}-dev"
                )
                if source_webspace_id
                else None,
            }
            if object_type and object_id:
                workflow = _development_workflow_summary(object_type, object_id)
                if workflow is not None:
                    local["transport_status"] = local["status"]
                    local["status"] = workflow["status"]
                    local["phase"] = workflow["phase"]
                    local["revision"] = workflow["revision"]
                    local["stable"] = workflow["stable"]
                    local["accepted"] = workflow["accepted"]
                    local["publication_status"] = workflow["publication_status"]
                    local["updated_at"] = workflow["updated_at"] or local["updated_at"]
            project_id = str(application.get("legacy_project_id") or "").strip()
            if project_id:
                try:
                    from adaos.sdk.developer import compositions

                    project = compositions.get(project_id)
                    primary = next(
                        (
                            item
                            for item in project.get("components", {}).get("owned", ())
                            if item.get("role") == "primary"
                        ),
                        None,
                    )
                    local["deletion"] = {
                        "project_id": project_id,
                        "manifest_digest": str(project.get("manifest_digest") or ""),
                        "primary_ref": str((primary or {}).get("ref") or ""),
                        "allowed": not bool(
                            (application.get("protection") or {}).get(
                                "system_application"
                            )
                        )
                        and not bool(model.get("installed"))
                        and not bool(model.get("channels")),
                    }
                    if not local["deletion"]["allowed"]:
                        local["deletion"]["reason"] = "published_installed_or_protected"
                except (
                    FileNotFoundError,
                    ValueError,
                    OSError,
                    compositions.ProjectCompositionError,
                ):
                    local["exists"] = False
                    local["status"] = "source_unavailable"
                    local["deletion"] = {
                        "project_id": project_id,
                        "allowed": False,
                        "reason": "development_project_unavailable",
                    }
        model["local_development"] = local
        model["execution_placement"] = placements.get(
            application_id,
            _empty_execution_placement(
                application_id,
                status="not_materialized",
                managed=False,
                partial=False,
            ),
        )
        application["distribution"] = {
            "visibility": str(application.get("visibility") or "private")
        }
        application["marketplace_listing"] = _marketplace_listing(application)
        model["active_release"] = (
            _active_release_for_webspace(model, webspace_id=webspace_id) or None
        )
        model["component_inventory"] = _application_component_inventory(model)
        home = _home_projection(model, webspace_id, snapshot=home_snapshot)
        model["home"] = home
        model["pinned"] = bool(home.get("pinned"))
        model["installation_summary"] = _installation_summary(
            model, webspace_id=webspace_id
        )
        enriched.append(enrich_application_conditions(model))
    return enriched


def list_applications(
    *,
    installed_only: bool = False,
    catalog_only: bool = False,
    available_only: bool = False,
    developed_only: bool = False,
    include_development: bool = True,
    webspace_id: str | None = None,
) -> list[dict[str, Any]]:
    development = _local_development_index() if include_development else {}
    models = _application_models(installed_only=installed_only)
    if available_only:
        models = [
            item
            for item in models
            if bool(item.get("installed"))
            or bool(item.get("local_beta_active"))
            or (
                item["application"]["visibility"] == "public"
                and bool(item.get("channels", {}).get("stable"))
            )
        ]
    if catalog_only:
        models = [
            item
            for item in models
            if item["application"]["visibility"] == "public"
            and bool(item.get("channels", {}).get("stable"))
        ]
    models = _enrich_application_models(
        models,
        development=development,
        webspace_id=webspace_id,
    )
    if developed_only:
        models = [
            item
            for item in models
            if bool((item.get("local_development") or {}).get("exists"))
        ]
    return models


def get_application(
    application_id: str, *, webspace_id: str | None = None
) -> dict[str, Any]:
    token = str(application_id or "").strip()
    for item in _application_models(
        installed_only=False,
        application_id=token,
    ):
        if item["application"]["application_id"] == token:
            return _enrich_application_models(
                [item],
                development=_local_development_index(),
                webspace_id=webspace_id,
            )[0]
    raise FileNotFoundError(f"Application not found: {token}")


def list_application_components(
    application_id: str, *, webspace_id: str | None = None
) -> list[dict[str, Any]]:
    """Return the bounded component and placement projection for one Application."""

    return list(
        get_application(application_id, webspace_id=webspace_id).get(
            "component_inventory", ()
        )
    )


def list_application_placements(
    application_id: str, *, webspace_id: str | None = None
) -> list[dict[str, Any]]:
    """Return desired and observed execution as bounded node-level rows."""

    model = get_application(application_id, webspace_id=webspace_id)
    placement = model.get("execution_placement") or {}
    desired = {
        str(item.get("component_ref") or ""): dict(item)
        for item in placement.get("desired") or ()
        if isinstance(item, Mapping) and str(item.get("component_ref") or "")
    }
    observed_by_component: dict[str, list[dict[str, Any]]] = {}
    for item in placement.get("observed") or ():
        if not isinstance(item, Mapping):
            continue
        component_ref = str(item.get("component_ref") or "")
        if component_ref:
            observed_by_component.setdefault(component_ref, []).append(dict(item))

    rows: list[dict[str, Any]] = []
    component_refs = sorted({*desired, *observed_by_component})
    for component_ref in component_refs:
        plan = desired.get(component_ref, {})
        mode = str(plan.get("mode") or "unmanaged")
        if mode == "disabled":
            rows.append(
                {
                    "placement_id": f"{component_ref}@disabled",
                    "component_ref": component_ref,
                    "node_id": None,
                    "desired": False,
                    "desired_mode": mode,
                    "runtime_status": "disabled",
                    "sync_status": "disabled",
                    "status_icon": "remove-circle-outline",
                    "status_color": "medium",
                    "status_tooltip": "Component is explicitly uninstalled.",
                    "generation": None,
                    "updated_at": placement.get("updated_at"),
                    "deployment_id": placement.get("deployment_id"),
                    "deployment_revision": placement.get("revision"),
                }
            )
            continue
        selected_nodes = {
            str(value or "").strip()
            for value in plan.get("selected_node_ids") or ()
            if str(value or "").strip()
        }
        actual = observed_by_component.get(component_ref, [])
        observed_nodes: set[str] = set()
        for item in actual:
            node_id = str(item.get("node_id") or "").strip()
            if node_id:
                observed_nodes.add(node_id)
            desired_here = bool(plan) and (
                not selected_nodes or node_id in selected_nodes
            )
            runtime_status = str(item.get("status") or "unknown").strip().lower()
            if desired_here and runtime_status == "active":
                sync_status, icon, color = (
                    "synced",
                    "checkmark-circle-outline",
                    "success",
                )
            elif desired_here:
                sync_status, icon, color = (
                    "degraded",
                    "alert-circle-outline",
                    "danger",
                )
            else:
                sync_status, icon, color = (
                    "unmanaged",
                    "help-circle-outline",
                    "medium",
                )
            rows.append(
                {
                    "placement_id": f"{component_ref}@{node_id or 'unknown'}",
                    "component_ref": component_ref,
                    "node_id": node_id or None,
                    "desired": desired_here,
                    "desired_mode": mode,
                    "runtime_status": runtime_status,
                    "sync_status": sync_status,
                    "status_icon": icon,
                    "status_color": color,
                    "status_tooltip": (
                        f"Desired and observed state are synchronized on {node_id}."
                        if sync_status == "synced"
                        else (
                            f"Desired state is not ready on {node_id}."
                            if desired_here
                            else f"Observed placement on {node_id} is not in desired state."
                        )
                    ),
                    "generation": item.get("generation"),
                    "updated_at": item.get("updated_at"),
                    "deployment_id": placement.get("deployment_id"),
                    "deployment_revision": placement.get("revision"),
                }
            )

        for node_id in sorted(selected_nodes - observed_nodes):
            rows.append(
                {
                    "placement_id": f"{component_ref}@{node_id}",
                    "component_ref": component_ref,
                    "node_id": node_id,
                    "desired": True,
                    "desired_mode": mode,
                    "runtime_status": "not_observed",
                    "sync_status": "missing",
                    "status_icon": "warning-outline",
                    "status_color": "warning",
                    "status_tooltip": f"Desired placement on {node_id} is not observed.",
                    "generation": None,
                    "updated_at": None,
                    "deployment_id": placement.get("deployment_id"),
                    "deployment_revision": placement.get("revision"),
                }
            )

        if plan and not actual and not selected_nodes:
            rows.append(
                {
                    "placement_id": f"{component_ref}@unassigned",
                    "component_ref": component_ref,
                    "node_id": None,
                    "desired": True,
                    "desired_mode": mode,
                    "runtime_status": "not_observed",
                    "sync_status": "pending",
                    "status_icon": "time-outline",
                    "status_color": "warning",
                    "status_tooltip": "Desired placement has no observed runtime instance.",
                    "generation": None,
                    "updated_at": None,
                    "deployment_id": placement.get("deployment_id"),
                    "deployment_revision": placement.get("revision"),
                }
            )

    rows.sort(
        key=lambda item: (
            str(item.get("component_ref") or "").casefold(),
            str(item.get("node_id") or ""),
        )
    )
    return rows[:500]


def get_application_placement_options(
    application_id: str,
    *,
    component_ref: str | None = None,
    webspace_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return exact placement revision plus bounded eligible node choices."""

    model = get_application(application_id, webspace_id=webspace_id)
    placement = model.get("execution_placement") or {}
    selected_component = str(component_ref or "").strip() or None
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    truncated = False
    if selected_component:
        components = {
            str(item.get("component_ref") or ""): dict(item)
            for item in model.get("component_inventory") or ()
            if isinstance(item, Mapping)
        }
        if selected_component not in components:
            raise ValueError("component_ref is absent from the installed release")
        provider = getattr(_service().executor, "placement_options", None)
        if not callable(provider):
            raise RuntimeError("Application placement option provider is unavailable")
        recommendation = provider(
            application_id,
            selected_component,
            limit=max(1, min(int(limit), 100)),
        )
        candidates = [
            {
                "node_id": str(item.get("node_id") or ""),
                "score": int(item.get("score") or 0),
                "already_active": bool(item.get("already_active")),
                "architecture": str(item.get("architecture") or ""),
                "runtime_version": str(item.get("runtime_version") or ""),
                "labels": dict(item.get("labels") or {}),
                "headroom": dict(item.get("headroom") or {}),
                "reasons": [str(value) for value in item.get("reasons") or ()],
            }
            for item in recommendation.get("candidates") or ()
            if isinstance(item, Mapping) and str(item.get("node_id") or "").strip()
        ]
        rejected = [
            {
                "node_id": str(item.get("node_id") or ""),
                "reason": str(item.get("reason") or "ineligible"),
            }
            for item in recommendation.get("rejected") or ()
            if isinstance(item, Mapping) and str(item.get("node_id") or "").strip()
        ]
        truncated = bool(recommendation.get("truncated"))
    return {
        "schema": "adaos.application.placement_options.v1",
        "application_id": str(application_id),
        "deployment_id": placement.get("deployment_id"),
        "expected_revision": int(placement.get("revision") or 0),
        "component_ref": selected_component,
        "placements": list_application_placements(
            application_id,
            webspace_id=webspace_id,
        ),
        "eligible_nodes": candidates,
        "rejected_nodes": rejected,
        "truncated": truncated,
    }


def _application_setup_target(
    application_id: str,
    *,
    release_digest: str | None = None,
    webspace_id: str | None = None,
) -> tuple[dict[str, Any], Any, str]:
    model = get_application(application_id, webspace_id=webspace_id)
    selected_digest = str(release_digest or "").strip()
    if not selected_digest:
        for field in (
            "active_release",
            "installed_release",
            "marketplace_release",
        ):
            candidate = model.get(field)
            if (
                isinstance(candidate, Mapping)
                and str(candidate.get("release_digest") or "").strip()
            ):
                selected_digest = str(candidate["release_digest"]).strip()
                break
    if not selected_digest:
        raise FileNotFoundError(
            f"Application has no setup-capable release: {application_id}"
        )
    release = _service().store.get_release(application_id, selected_digest)
    selection = get_runtime_selection(str(webspace_id or "desktop"), application_id)
    channel = (
        "beta"
        if str((selection or {}).get("runtime_root_ref") or "").startswith("trial:")
        or str(release.lifecycle or "").lower() in {"candidate", "trial"}
        else "stable"
    )
    return model, release, channel


def _setup_configuration_rows(
    application_id: str,
    release: Any,
    *,
    channel: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[str]]]:
    contract = release.setup_contract
    if contract is None:
        return [], {}, {}
    rows: list[dict[str, Any]] = []
    values_by_component: dict[str, dict[str, Any]] = {}
    credential_presence: dict[str, list[str]] = {}
    for component in contract.payload.get("components") or ():
        component_ref = str(component.get("component_ref") or "")
        store = ApplicationConfigurationStore(
            _state_dir(), application_id, component_ref
        )
        record = store.read()
        selected = None
        if channel == "beta" and (record.get("beta") or {}).get("active"):
            selected = record.get("beta")
        elif channel == "stable":
            selected = record.get("stable")
        settings = component.get("settings") or {}
        defaults = deepcopy(dict(settings.get("defaults") or {}))
        values = deepcopy(dict((selected or {}).get("values") or defaults))
        credentials = dict((selected or {}).get("credentials") or {})
        values_by_component[component_ref] = values
        credential_presence[component_ref] = sorted(credentials)
        rows.append(
            {
                "component_ref": component_ref,
                "revision": int(record.get("revision") or 0),
                "channel": channel,
                "values": values,
                "credential_presence": [
                    {
                        "slot": str(item.get("slot") or ""),
                        "present": str(item.get("slot") or "") in credentials,
                    }
                    for item in component.get("credentials") or ()
                ],
            }
        )
    return rows, values_by_component, credential_presence


def _setup_form_field(
    field_id: str,
    declaration: Mapping[str, Any],
    *,
    required: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    """Project the supported JSON Schema subset into the public WebUI form ABI."""

    raw_types = declaration.get("type")
    if isinstance(raw_types, str):
        types = [raw_types]
    elif isinstance(raw_types, Sequence) and not isinstance(
        raw_types, (str, bytes, bytearray)
    ):
        types = [str(item) for item in raw_types if str(item) != "null"]
    else:
        types = []
    enum = list(declaration.get("enum") or ())
    field_type: str
    if enum:
        field_type = "dropdown"
    elif types == ["boolean"]:
        field_type = "toggle"
    elif types == ["integer"]:
        field_type = "integer"
    elif types == ["number"]:
        field_type = "number"
    elif (
        types == ["array"] and (declaration.get("items") or {}).get("type") == "string"
    ):
        field_type = "tagInput"
    elif not types or types == ["string"]:
        field_type = {
            "email": "email",
            "uri": "url",
            "url": "url",
            "date": "date",
            "time": "time",
            "date-time": "dateTime",
        }.get(str(declaration.get("format") or ""), "shortText")
    else:
        return None, f"unsupported_type:{','.join(types) or 'unspecified'}"
    field: dict[str, Any] = {
        "id": str(field_id),
        "type": field_type,
        "label": str(declaration.get("title") or field_id.replace("_", " ")).strip(),
        "required": bool(required),
    }
    description = str(declaration.get("description") or "").strip()
    if description:
        field["helpText"] = description
    if enum:
        enum_labels = declaration.get("x-enum-labels")
        enum_labels = (
            list(enum_labels)
            if isinstance(enum_labels, Sequence)
            and not isinstance(enum_labels, (str, bytes, bytearray))
            else []
        )
        field["options"] = [
            {
                "value": value,
                "label": str(enum_labels[index] if index < len(enum_labels) else value),
            }
            for index, value in enumerate(enum)
        ]
    for source, target in (
        ("minimum", "min"),
        ("maximum", "max"),
        ("minLength", "minLength"),
        ("maxLength", "maxLength"),
        ("pattern", "pattern"),
    ):
        if declaration.get(source) is not None:
            field[target] = declaration[source]
    if declaration.get("default") is not None:
        field["defaultValue"] = deepcopy(declaration["default"])
    return field, None


def _setup_form_editors(
    application_id: str,
    release_digest: str,
    contract: Any,
    configuration: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build secret-free dynamic editors while keeping mutation actions UI-owned."""

    state_by_component = {
        str(item.get("component_ref") or ""): item for item in configuration
    }
    settings_editors: list[dict[str, Any]] = []
    credential_editors: list[dict[str, Any]] = []
    for component in contract.payload.get("components") or ():
        component_ref = str(component.get("component_ref") or "")
        current = state_by_component.get(component_ref, {})
        settings = dict(component.get("settings") or {})
        schema = dict(settings.get("schema") or {})
        required = {str(item) for item in schema.get("required") or ()}
        fields: list[dict[str, Any]] = []
        unsupported: list[dict[str, str]] = []
        for field_id, declaration in (schema.get("properties") or {}).items():
            if not isinstance(declaration, Mapping):
                unsupported.append(
                    {"field_id": str(field_id), "reason": "invalid_schema_property"}
                )
                continue
            field, reason = _setup_form_field(
                str(field_id),
                declaration,
                required=str(field_id) in required,
            )
            if field is None:
                unsupported.append(
                    {"field_id": str(field_id), "reason": str(reason or "unsupported")}
                )
            else:
                fields.append(field)
        revision = int(current.get("revision") or 0)
        settings_editors.append(
            {
                "id": component_ref,
                "application_id": application_id,
                "release_digest": release_digest,
                "component_ref": component_ref,
                "expected_revision": revision,
                "fields": fields,
                "values": deepcopy(dict(current.get("values") or {})),
                "supported": not unsupported,
                "unsupported_fields": unsupported,
            }
        )
        presence = {
            str(item.get("slot") or ""): bool(item.get("present"))
            for item in current.get("credential_presence") or ()
        }
        for credential in component.get("credentials") or ():
            slot = str(credential.get("slot") or "")
            credential_editors.append(
                {
                    "id": f"{component_ref}#{slot}",
                    "application_id": application_id,
                    "release_digest": release_digest,
                    "component_ref": component_ref,
                    "slot": slot,
                    "expected_revision": revision,
                    "present": bool(presence.get(slot)),
                    "required": bool(credential.get("required")),
                    "fields": [
                        {
                            "id": "value",
                            "type": "password",
                            "label": str(credential.get("title") or slot),
                            "helpText": str(credential.get("purpose") or ""),
                            "required": bool(credential.get("required")),
                        }
                    ],
                    "values": {},
                }
            )
    return {"settings": settings_editors, "credentials": credential_editors}


def get_application_setup(
    application_id: str,
    *,
    release_digest: str | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    """Return release-owned setup plus a secret-free, revisioned readiness view."""

    model, release, channel = _application_setup_target(
        application_id,
        release_digest=release_digest,
        webspace_id=webspace_id,
    )
    contract = release.setup_contract
    if contract is None:
        return {
            "schema": "adaos.application.setup_surface.v1",
            "application_id": application_id,
            "release_digest": release.release_digest,
            "available": False,
            "reason": "release_setup_contract_not_declared",
            "contract": None,
            "state": None,
            "configuration": [],
            "editors": {"settings": [], "credentials": []},
        }
    configuration, values, credential_presence = _setup_configuration_rows(
        application_id,
        release,
        channel=channel,
    )
    try:
        access = get_application_access_surface(
            application_id,
            release_digest=release.release_digest,
        )
    except (FileNotFoundError, RuntimeError, ValueError):
        access = {}
    sections = access.get("sections") if isinstance(access, Mapping) else {}
    sections = sections if isinstance(sections, Mapping) else {}
    account_status = {
        str(item.get("account_id") or item.get("id") or item.get("provider") or ""): (
            "ready"
            if str(item.get("status") or "").lower() in {"active", "connected", "ready"}
            else str(item.get("status") or "missing").lower()
        )
        for item in sections.get("connected_accounts") or ()
        if isinstance(item, Mapping)
    }
    permission_status = {
        str(item.get("id") or ""): (
            "ready" if bool(model.get("installed")) else "pending"
        )
        for item in contract.payload.get("permissions") or ()
        if str(item.get("id") or "")
    }
    placement = model.get("execution_placement") or {}
    placement_state = str(placement.get("status") or "unknown").lower()
    if not bool(contract.payload.get("placement", {}).get("required")):
        placement_status = "not_applicable"
    elif placement_state in {"active", "ready", "synced"} and not bool(
        placement.get("partial")
    ):
        placement_status = "ready"
    elif placement_state in {"failed", "unavailable", "degraded"}:
        placement_status = "failed"
    elif placement_state in {"not_materialized", "not_reported"}:
        placement_status = "missing"
    else:
        placement_status = "unknown"
    evidence_ready = bool(
        next(
            (
                item.get("acceptance_evidence_count")
                for item in list_releases(application_id)
                if item.get("release_digest") == release.release_digest
            ),
            0,
        )
    )
    verification_status = {
        str(item.get("id") or ""): ("ready" if evidence_ready else "pending")
        for item in contract.payload.get("verification") or ()
        if str(item.get("id") or "")
    }
    projection = project_setup_state(
        contract,
        channel=channel,
        configuration=values,
        credential_presence=credential_presence,
        connected_account_status=account_status,
        permission_status=permission_status,
        placement_status=placement_status,
        verification_status=verification_status,
    )
    store = ApplicationSetupStateStore(_state_dir())
    current = store.read(application_id, channel)
    state = store.reconcile(
        projection,
        expected_revision=int((current or {}).get("revision") or 0),
    )
    return {
        "schema": "adaos.application.setup_surface.v1",
        "application_id": application_id,
        "release_digest": release.release_digest,
        "available": True,
        "reason": None,
        "contract": contract.to_dict(),
        "state": state,
        "configuration": configuration,
        "editors": _setup_form_editors(
            application_id,
            str(release.release_digest or ""),
            contract,
            configuration,
        ),
    }


def update_application_configuration(
    application_id: str,
    component_ref: str,
    values: Mapping[str, Any],
    *,
    release_digest: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    """CAS-update non-secret setup values for one owned component."""

    _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        f"application-setup:{application_id}:{component_ref}:{expected_revision}",
        required_capability="applications.apply",
    )
    _, release, channel = _application_setup_target(
        application_id,
        release_digest=release_digest,
        webspace_id=webspace_id,
    )
    contract = release.setup_contract
    if contract is None:
        raise ValueError("Application release does not declare a setup contract")
    component = next(
        (
            dict(item)
            for item in contract.payload.get("components") or ()
            if str(item.get("component_ref") or "") == component_ref
        ),
        None,
    )
    settings = (component or {}).get("settings")
    if not isinstance(settings, Mapping):
        raise ValueError("Application component does not declare typed settings")
    store = ApplicationConfigurationStore(_state_dir(), application_id, component_ref)
    record = store.read()
    if channel == "beta":
        beta = record.get("beta") or {}
        if not beta.get("active"):
            raise ValueError("Beta configuration has not been prepared")
        saved = store.update_beta(
            candidate_id=str(beta.get("candidate_id") or ""),
            schema=dict(settings["schema"]),
            values=dict(values),
            credentials=dict(beta.get("credentials") or {}),
            expected_revision=expected_revision,
        )
    else:
        stable = record.get("stable") or {}
        saved = store.set_stable(
            release_digest=release.release_digest,
            schema=dict(settings["schema"]),
            values=dict(values),
            credentials=dict(stable.get("credentials") or {}),
            expected_revision=expected_revision,
        )
    return {
        "configuration": {
            "component_ref": component_ref,
            "revision": int(saved.get("revision") or 0),
            "values": deepcopy(dict(values)),
        },
        "setup": get_application_setup(
            application_id,
            release_digest=release.release_digest,
            webspace_id=webspace_id,
        ),
    }


def update_application_credential(
    application_id: str,
    component_ref: str,
    slot: str,
    value: str | None,
    *,
    release_digest: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    """Bind or revoke one declared credential without returning its value/ref."""

    _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        f"application-credential:{application_id}:{component_ref}:{slot}:{expected_revision}",
        required_capability="applications.apply",
    )
    if value is not None and (
        not isinstance(value, str) or len(value.encode("utf-8")) > 65_536
    ):
        raise ValueError("Credential value must be a string of at most 64 KiB")
    _, release, channel = _application_setup_target(
        application_id,
        release_digest=release_digest,
        webspace_id=webspace_id,
    )
    contract = release.setup_contract
    if contract is None:
        raise ValueError("Application release does not declare a setup contract")
    component = next(
        (
            dict(item)
            for item in contract.payload.get("components") or ()
            if str(item.get("component_ref") or "") == component_ref
        ),
        None,
    )
    credential = next(
        (
            dict(item)
            for item in (component or {}).get("credentials") or ()
            if str(item.get("slot") or "") == slot
        ),
        None,
    )
    if credential is None:
        raise ValueError("Credential slot is not declared by the release")
    ctx = require_ctx("sdk.applications")
    vault = getattr(ctx, "credential_vault", None)
    if vault is None:
        raise PermissionError("Node credential vault is unavailable")
    from adaos.services.personalization_runtime import personalization_access_service

    owner_ref = personalization_access_service(ctx).owner.ref()
    identity = {
        "application_id": application_id,
        "component_ref": component_ref,
        "owner_ref": owner_ref,
        "slot": slot,
        "purpose": str(credential.get("purpose") or ""),
    }
    store = ApplicationConfigurationStore(_state_dir(), application_id, component_ref)
    record = store.read()
    if int(record.get("revision") or 0) != int(expected_revision):
        raise ValueError("Configuration changed; reopen setup before applying")
    if channel == "beta":
        selected = record.get("beta") or {}
        if not selected.get("active"):
            raise ValueError("Beta configuration has not been prepared")
    else:
        selected = record.get("stable") or {}
    settings = (component or {}).get("settings") or {}
    schema = dict(settings.get("schema") or {"type": "object"})
    values = deepcopy(dict(selected.get("values") or settings.get("defaults") or {}))
    credentials = dict(selected.get("credentials") or {})
    previous_ref = credentials.get(slot)
    new_ref = None
    if value is None:
        credentials.pop(slot, None)
    else:
        new_ref = "credential:" + uuid.uuid4().hex
        payload = json.dumps({"identity": identity, "value": value}, ensure_ascii=False)
        ApplicationRuntimeCredentials._vault_call(
            vault,
            "put",
            ApplicationRuntimeCredentials._key(identity, new_ref),
            payload,
        )
        credentials[slot] = new_ref
    try:
        if channel == "beta":
            saved = store.update_beta(
                candidate_id=str(selected.get("candidate_id") or ""),
                schema=schema,
                values=values,
                credentials=credentials,
                expected_revision=expected_revision,
            )
        else:
            saved = store.set_stable(
                release_digest=release.release_digest,
                schema=schema,
                values=values,
                credentials=credentials,
                expected_revision=expected_revision,
            )
    except Exception:
        if new_ref is not None:
            ApplicationRuntimeCredentials._vault_call(
                vault,
                "delete",
                ApplicationRuntimeCredentials._key(identity, new_ref),
            )
        raise
    if previous_ref and previous_ref != new_ref:
        ApplicationRuntimeCredentials._vault_call(
            vault,
            "delete",
            ApplicationRuntimeCredentials._key(identity, previous_ref),
        )
    return {
        "credential": {
            "component_ref": component_ref,
            "slot": slot,
            "present": value is not None,
            "revision": int(saved.get("revision") or 0),
        },
        "setup": get_application_setup(
            application_id,
            release_digest=release.release_digest,
            webspace_id=webspace_id,
        ),
    }


def set_home_pinned(
    application_id: str,
    *,
    pinned: bool,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Set the presentation overlay without changing subnet installation state."""

    webspace = str(webspace_id or "").strip()
    if not webspace:
        raise ValueError("webspace_id is required")
    service = WebDesktopService()
    snapshot = service.get_snapshot(webspace)
    token = str(application_id or "").strip()
    try:
        model = get_application(token, webspace_id=webspace)
    except FileNotFoundError:
        known_refs = {
            *snapshot.installed.apps,
            *snapshot.pinned_applications,
        }
        if token not in known_refs:
            raise
        model = None
        aliases = (token,)
        installed_model = token in set(snapshot.installed.apps)
    else:
        aliases = _application_home_aliases(model)
        installed_model = bool(model.get("installed"))
    if not installed_model:
        raise ValueError("Only installed Applications can be pinned to Home")
    if not aliases:
        raise ValueError("Application has no Home presentation entrypoint")
    installed = set(snapshot.installed.apps)
    pinned_refs = set(snapshot.pinned_applications)
    application_ref = next((item for item in aliases if item in installed), None)
    if application_ref is None:
        application_ref = next((item for item in aliases if item in pinned_refs), None)
    application_ref = application_ref or aliases[0]
    alias_set = set(aliases)
    projection_reconciled = False
    if pinned and not installed.intersection(alias_set):
        # Installation is subnet-scoped.  The legacy desktop-installed list is
        # only a presentation projection, so materialize it on first pin
        # instead of requiring a second, contradictory installation record.
        service.toggle_install_with_live_room("app", application_ref, webspace)
        projection_reconciled = True
    else:
        next_pinned = [
            item for item in snapshot.pinned_applications if item not in alias_set
        ]
        if pinned:
            next_pinned.append(application_ref)
        service.set_pinned_applications_with_live_room(next_pinned, webspace)
    return {
        "schema": "adaos.application.home_projection.v1",
        "application_id": application_id,
        "application_ref": application_ref,
        "webspace_id": webspace,
        "installed": True,
        "pinnable": True,
        "pinned": bool(pinned),
        "projection_reconciled": projection_reconciled,
        "status": "ready",
    }


def reorder_home_application(
    application_id: str,
    *,
    to_index: int,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Move one pinned Application without changing subnet installation state."""

    webspace = str(webspace_id or "").strip()
    if not webspace:
        raise ValueError("webspace_id is required")
    service = WebDesktopService()
    snapshot = service.get_snapshot(webspace)
    pinned_applications = list(snapshot.pinned_applications)
    current = list(snapshot.icon_order)
    token = str(application_id or "").strip()
    try:
        model = get_application(token, webspace_id=webspace)
    except FileNotFoundError:
        if token not in pinned_applications:
            raise
        aliases = (token,)
    else:
        if not bool(model.get("installed")):
            raise ValueError("Only installed Applications can be reordered on Home")
        aliases = _application_home_aliases(model)
        if not aliases:
            raise ValueError("Application has no Home presentation entrypoint")
    alias_set = set(aliases)
    application_ref = next(
        (item for item in pinned_applications if item in alias_set), None
    )
    if application_ref is None:
        raise ValueError("Application is not pinned to Home")

    remaining = [item for item in current if item not in alias_set]
    bounded_index = max(0, min(int(to_index), len(remaining)))
    reordered = [
        *remaining[:bounded_index],
        application_ref,
        *remaining[bounded_index:],
    ]
    service.set_icon_order_with_live_room(reordered, webspace)
    return {
        "schema": "adaos.application.home_projection.v1",
        "application_id": application_id,
        "application_ref": application_ref,
        "webspace_id": webspace,
        "pinned": True,
        "home_order": bounded_index,
        "pinned_applications": pinned_applications,
        "icon_order": reordered,
        "status": "ready",
    }


def _sync_home_installation(
    application_id: str,
    *,
    installed: bool,
    webspace_id: str,
) -> dict[str, Any]:
    model = get_application(application_id, webspace_id=webspace_id)
    aliases = _application_home_aliases(model)
    if not aliases:
        raise ValueError("Application has no Home presentation entrypoint")
    service = WebDesktopService()
    snapshot = service.get_snapshot(webspace_id)
    current_apps = list(snapshot.installed.apps)
    removed_apps = list(snapshot.installed.removed_apps)
    alias_set = set(aliases)
    existing_ref = next((item for item in current_apps if item in alias_set), None)
    application_ref = existing_ref or aliases[0]
    if installed:
        next_apps = [item for item in current_apps if item not in alias_set]
        next_apps.append(application_ref)
        next_removed = [item for item in removed_apps if item not in alias_set]
    else:
        next_apps = [item for item in current_apps if item not in alias_set]
        next_removed = [item for item in removed_apps if item not in alias_set]
        next_removed.append(application_ref)
    service.set_installed_with_live_room(
        WebDesktopInstalled(
            apps=next_apps,
            widgets=list(snapshot.installed.widgets),
            removed_apps=next_removed,
            removed_widgets=list(snapshot.installed.removed_widgets),
        ),
        webspace_id,
    )
    next_pinned = [
        item for item in snapshot.pinned_applications if item not in alias_set
    ]
    if installed:
        next_pinned.append(application_ref)
    service.set_pinned_applications_with_live_room(next_pinned, webspace_id)
    return {
        "schema": "adaos.application.home_projection.v1",
        "application_id": application_id,
        "application_ref": application_ref,
        "webspace_id": webspace_id,
        "installed": installed,
        "pinnable": installed,
        "pinned": installed,
        "status": "ready",
    }


def get_identity(application_id: str) -> dict[str, Any]:
    """Read registered identity without catalog/runtime scans or creating a record."""
    application = _service().store.get_application(application_id)
    return {
        "application_id": application.application_id,
        "publisher_ref": application.publisher_ref,
        "display_name": str(
            application.publisher.get("display_name") or application.publisher_ref
        ),
        "source": "application_registry",
    }


def list_catalog() -> list[dict[str, Any]]:
    return list_applications(catalog_only=True)


def list_releases(application_id: str) -> list[dict[str, Any]]:
    token = str(application_id or "").strip()
    try:
        releases = _service().list_releases(token)
    except FileNotFoundError:
        projections = _workspace_project_read_models((), application_id=token)
        if not projections:
            raise
        projection = projections[0]
        release = deepcopy(dict(projection.get("installed_release") or {}))
        workspace_project = dict(projection.get("workspace_project") or {})
        manifest_digest = str(workspace_project.get("manifest_digest") or "").strip()
        if not release or not manifest_digest:
            raise FileNotFoundError(f"Application release not found: {token}")
        release.update(
            {
                "release_digest": manifest_digest,
                "lifecycle": "stable",
                "channels": ["stable"],
            }
        )
        project_release = dict(release.get("project_release") or {})
        project_release.setdefault(
            "schema", "adaos.artifact.workspace_project_release.v1"
        )
        project_release["release_digest"] = manifest_digest
        release["project_release"] = project_release
        return [_release_read_model(release)]
    return [_release_read_model(item) for item in releases]


def get_subscription(application_id: str) -> dict[str, Any] | None:
    try:
        return _service().store.get_subscription(application_id).to_dict()
    except FileNotFoundError:
        return None


def get_runtime_selection(
    webspace_id: str, application_id: str
) -> dict[str, Any] | None:
    try:
        return (
            _service()
            .store.get_runtime_selection(webspace_id, application_id)
            .to_dict()
        )
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
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
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.report",
    )
    return get_development_report_service().request_resync(
        report_id, after_revision=after_revision, limit=limit
    )


def get_operation(operation_id: str) -> dict[str, Any]:
    return _service().store.get_operation(operation_id).to_dict()


def list_trial_access(application_id: str | None = None) -> list[dict[str, Any]]:
    return [item.to_dict() for item in _service().store.list_grants(application_id)]


def list_application_access(
    application_id: str | None = None,
    *,
    subject_ref: str | None = None,
) -> list[dict[str, Any]]:
    return [
        item.to_dict()
        for item in _service().store.list_application_access_grants(
            application_id,
            subject_ref=subject_ref,
        )
    ]


def grant_application_access(
    application_id: str,
    *,
    release_digest: str,
    subject_ref: str,
    application_roles: tuple[str, ...],
    issuer_ref: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    permission_ceiling: tuple[str, ...] | None = None,
    explicit_denies: tuple[str, ...] = (),
    constraints: Mapping[str, Any] | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    actor, _, _, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    return (
        ApplicationAccessService(_service())
        .grant_access(
            application_id,
            release_digest=release_digest,
            subject_ref=subject_ref,
            application_roles=application_roles,
            permission_ceiling=permission_ceiling,
            explicit_denies=explicit_denies,
            constraints=constraints,
            expires_at=expires_at,
            issuer_ref=issuer_ref or actor,
            idempotency_key=key,
        )
        .to_dict()
    )


def revoke_application_access(
    grant_id: str,
    *,
    issuer_ref: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
) -> dict[str, Any]:
    actor, _, _, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        f"revoke-application-access:{grant_id}:{expected_revision}",
        required_capability="applications.apply",
    )
    return (
        ApplicationAccessService(_service())
        .revoke_access(
            grant_id,
            issuer_ref=issuer_ref or actor,
            expected_revision=expected_revision,
        )
        .to_dict()
    )


def change_application_access(
    grant_id: str,
    *,
    release_digest: str,
    application_roles: tuple[str, ...],
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    issuer_ref: str = "",
    permission_ceiling: tuple[str, ...] | None = None,
    explicit_denies: tuple[str, ...] | None = None,
    constraints: Mapping[str, Any] | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    actor, _, _, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    return (
        ApplicationAccessService(_service())
        .change_access(
            grant_id,
            release_digest=release_digest,
            application_roles=application_roles,
            expected_revision=expected_revision,
            issuer_ref=issuer_ref or actor,
            permission_ceiling=permission_ceiling,
            explicit_denies=explicit_denies,
            constraints=constraints,
            expires_at=expires_at,
        )
        .to_dict()
    )


def get_application_access_surface(
    application_id: str,
    *,
    release_digest: str | None = None,
    activity_limit: int = 50,
) -> dict[str, Any]:
    try:
        return _access_management().application_detail(
            application_id,
            release_digest=release_digest,
            activity_limit=activity_limit,
        )
    except FileNotFoundError:
        projected = get_application(application_id)
        project = dict(projected.get("workspace_project") or {})
        if not project:
            raise
        profile = dict(project.get("permission_profile") or {})
        roles = [
            dict(item)
            for item in project.get("application_roles") or ()
            if isinstance(item, Mapping)
        ]
        return {
            "schema": "adaos.application.access_surface.v1",
            "application": deepcopy(projected["application"]),
            "release": deepcopy(projected.get("installed_release")),
            "installation": deepcopy(projected.get("installation")),
            "managed": False,
            "reason": "workspace_project_not_migrated_to_application_aggregate",
            "sections": {
                "permissions": {
                    "profile": profile,
                    "digest": project.get("manifest_digest"),
                    "privacy_report": None,
                    "badges": [],
                },
                "access": [],
                "roles": roles,
                "connected_accounts": [],
                "release_readiness": None,
                "activity": [],
                "activity_page": {
                    "limit": max(1, min(int(activity_limit), 200)),
                    "has_more": False,
                },
            },
        }


def get_users_access_surface(
    personalization: Mapping[str, Any] | None = None,
    *,
    activity_limit: int = 50,
) -> dict[str, Any]:
    return _access_management().users_access(
        personalization,
        activity_limit=activity_limit,
    )


def simulate_application_access(application_id: str, **request: Any) -> dict[str, Any]:
    return _access_management().simulate(application_id, **request)


def list_application_access_reviews(
    application_id: str | None = None,
    *,
    stale_days: int = 90,
) -> list[dict[str, Any]]:
    return _access_management().access_reviews(
        application_id=application_id,
        stale_days=stale_days,
    )


def get_application_privacy_report(
    application_id: str,
    *,
    release_digest: str,
) -> dict[str, Any]:
    service = _access_management()
    return {
        "privacy_report": service.privacy_report(
            application_id, release_digest=release_digest
        ),
        "anomalies": service.anomalies(application_id, release_digest=release_digest),
        "badges": service.privacy_badges(application_id, release_digest=release_digest),
    }


def get_application_update_review(
    application_id: str,
    *,
    old_release_digest: str,
    new_release_digest: str,
) -> dict[str, Any]:
    return _access_management().update_review(
        application_id,
        old_release_digest=old_release_digest,
        new_release_digest=new_release_digest,
    )


def profile_application_permissions(
    application_id: str,
    *,
    release_digest: str,
    observed_capabilities: tuple[str, ...] = (),
    inferred_capabilities: tuple[str, ...] = (),
    previous_release_digest: str | None = None,
) -> dict[str, Any]:
    return _access_management().permission_profiler(
        application_id,
        release_digest=release_digest,
        observed_capabilities=observed_capabilities,
        inferred_capabilities=inferred_capabilities,
        previous_release_digest=previous_release_digest,
    )


def put_application_connected_account(
    application_id: str,
    account: Mapping[str, Any],
    *,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    expected_revision: int,
) -> dict[str, Any]:
    _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    return _access_management().put_connected_account(
        application_id,
        account,
        expected_revision=expected_revision,
    )


def export_application_access_snapshot(application_id: str) -> dict[str, Any]:
    return _access_management().export_snapshot(application_id)


def import_application_access_snapshot(
    snapshot: Mapping[str, Any],
    *,
    apply: bool,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    actor, _, _, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    return _access_management().import_snapshot(snapshot, issuer_ref=actor, apply=apply)


def verify_application_release(
    application_id: str,
    *,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    **request: Any,
) -> dict[str, Any]:
    actor, _, _, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return _access_management().final_verification(
        application_id,
        actor_ref=actor,
        **request,
    )


def decide_application_access(
    application_id: str,
    *,
    release_digest: str,
    subject_ref: str,
    permission_id: str,
    app_capability: str,
    actor_chain: Mapping[str, Any],
    component_capabilities: tuple[str, ...] = (),
    approval_id: str | None = None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    actor, subnet, granted, _ = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return (
        ApplicationAccessService(_service())
        .decide(
            application_id,
            release_digest=release_digest,
            subject_ref=subject_ref,
            permission_id=permission_id,
            app_capability=app_capability,
            component_capabilities=component_capabilities,
            approval_id=approval_id,
            actor_chain={
                "actor_ref": actor,
                "subnet_ref": subnet,
                "capability": granted,
                **dict(actor_chain or {}),
            },
        )
        .to_dict()
    )


def list_application_access_audit(
    application_id: str | None = None,
    *,
    subject_ref: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in _service().store.list_application_access_audit(
            application_id,
            subject_ref=subject_ref,
            limit=limit,
        )
    ]


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
    return (
        TrialAccessService(_service())
        .revoke(
            grant_id,
            publisher_ref=publisher_ref,
            expected_revision=expected_revision,
        )
        .to_dict()
    )


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
    return (
        _service()
        .plan_operation(
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
        )
        .to_dict()
    )


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
    return (
        _service()
        .plan_operation(
            application_id,
            "update",
            release_digest=release_digest,
            expected_revision=expected_revision,
            actor_ref=actor,
            subnet_ref=subnet,
            capability=granted,
            idempotency_key=key,
            access_redemption_id=access_redemption_id,
        )
        .to_dict()
    )


def assess_updates(
    *,
    application_ids: Sequence[str] | None = None,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Return a bounded, side-effect-free Application update assessment."""

    requested = {
        str(value or "").strip()
        for value in (application_ids or ())
        if str(value or "").strip()
    }
    if len(requested) > 100:
        raise ValueError("application_ids cannot contain more than 100 items")
    models = list_applications(installed_only=True, webspace_id=webspace_id)
    if requested:
        models = [
            item
            for item in models
            if str((item.get("application") or {}).get("application_id") or "")
            in requested
        ]
    if len(models) > 100:
        raise ValueError(
            "update assessment is limited to 100 Applications; "
            "provide application_ids to narrow the scope"
        )
    items = []
    for model in models:
        application = model.get("application") or {}
        installation = model.get("installation") or {}
        active_release = model.get("active_release") or {}
        effective = model.get("effective_release") or {}
        target_release = effective.get("release") or {}
        item = {
            "application_id": str(application.get("application_id") or ""),
            "title": str(
                (application.get("display") or {}).get("title")
                or application.get("application_id")
                or ""
            ),
            "installed_version": active_release.get("version"),
            "available_version": target_release.get("version"),
            "target_release_digest": effective.get("release_digest"),
            "installation_revision": installation.get("revision"),
            "update_available": bool(model.get("update_available")),
            "eligible": bool(
                model.get("update_available")
                and installation
                and application.get("aggregate_backed", True)
            ),
            "attention": deepcopy(model.get("attention") or {}),
        }
        if model.get("update_available") and not item["eligible"]:
            item["blocked_reason"] = (
                "stable_installation_required"
                if not installation
                else "aggregate_lifecycle_required"
            )
        items.append(item)
    items.sort(
        key=lambda item: (
            not item["eligible"],
            item["title"].casefold(),
            item["application_id"],
        )
    )
    return {
        "schema": "adaos.application.update_assessment.v1",
        "assessed_count": len(items),
        "update_count": sum(1 for item in items if item["update_available"]),
        "eligible_count": sum(1 for item in items if item["eligible"]),
        "blocked_count": sum(1 for item in items if item.get("blocked_reason")),
        "items": items,
    }


def plan_available_updates(
    *,
    application_ids: Sequence[str] | None,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Create one durable review record containing exact per-Application plans."""

    actor, subnet, _, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    store = ApplicationUpdateBatchStore(_state_dir())
    batch_id = update_batch_id(subnet, key)
    try:
        return store.get(batch_id)
    except FileNotFoundError:
        pass

    assessment = assess_updates(
        application_ids=application_ids,
        webspace_id=webspace_id,
    )
    operations = []
    skipped = []
    for item in assessment["items"]:
        if not item["eligible"]:
            if item["update_available"]:
                skipped.append(
                    {
                        "application_id": item["application_id"],
                        "reason": item.get("blocked_reason") or "not_eligible",
                    }
                )
            continue
        child_key = (
            "batch-update:"
            + canonical_payload_digest(
                {"batch_id": batch_id, "application_id": item["application_id"]}
            ).split(":", 1)[1]
        )
        operation = plan_update(
            item["application_id"],
            release_digest=item["target_release_digest"],
            expected_revision=int(item["installation_revision"] or 0),
            actor_ref=actor,
            subnet_ref=subnet,
            capability="applications.plan",
            idempotency_key=child_key,
        )
        operations.append(
            {
                "application_id": item["application_id"],
                "title": item["title"],
                "installed_version": item["installed_version"],
                "available_version": item["available_version"],
                "operation_id": operation["operation_id"],
                "operation_plan_digest": operation["plan_digest"],
                "apply_idempotency_key": child_key,
                "status": "planned",
            }
        )
    created_at = utc_now()
    digest_payload = {
        "schema": "adaos.application.update_batch_plan.v1",
        "batch_id": batch_id,
        "subnet_ref": subnet,
        "webspace_id": str(webspace_id or "desktop").strip() or "desktop",
        "operations": [
            {
                key: item[key]
                for key in (
                    "application_id",
                    "operation_id",
                    "operation_plan_digest",
                )
            }
            for item in operations
        ],
    }
    batch = {
        "schema": "adaos.application.update_batch.v1",
        "batch_id": batch_id,
        "status": "planned",
        "actor_ref": actor,
        "subnet_ref": subnet,
        "idempotency_key": key,
        "webspace_id": digest_payload["webspace_id"],
        "plan_digest": canonical_payload_digest(digest_payload),
        "assessment": assessment,
        "operations": operations,
        "skipped": skipped,
        "created_at": created_at,
        "updated_at": created_at,
    }
    return store.save(batch)


def get_update_batch(batch_id: str) -> dict[str, Any]:
    return ApplicationUpdateBatchStore(_state_dir()).get(batch_id)


def apply_update_batch(
    batch_id: str,
    *,
    plan_digest: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    webspace_id: str = "desktop",
) -> dict[str, Any]:
    """Apply or resume an exact reviewed update batch with durable partial results."""

    actor, subnet, _, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.apply",
    )
    store = ApplicationUpdateBatchStore(_state_dir())
    batch = store.get(batch_id)
    if batch.get("subnet_ref") != subnet:
        raise ValueError("Application update batch belongs to another subnet")
    if batch.get("plan_digest") != str(plan_digest or "").strip():
        raise ValueError("Application update batch plan digest does not match")
    if batch.get("status") in {"succeeded", "partial", "failed"}:
        return batch
    known_apply_key = str(batch.get("apply_idempotency_key") or "")
    if known_apply_key and known_apply_key != key:
        raise ValueError("Application update batch apply identity does not match")
    batch["apply_idempotency_key"] = key
    batch["status"] = "applying"
    batch["updated_at"] = utc_now()
    store.save(batch)

    outcomes = list(batch.get("operations") or [])
    for index, item in enumerate(outcomes):
        if item.get("status") == "succeeded":
            continue
        try:
            receipt = apply_operation(
                str(item.get("operation_id") or ""),
                plan_digest=str(item.get("operation_plan_digest") or ""),
                actor_ref=actor,
                subnet_ref=subnet,
                capability="applications.apply",
                idempotency_key=str(item.get("apply_idempotency_key") or ""),
                webspace_id=str(webspace_id or batch.get("webspace_id") or "desktop"),
            )
            item["status"] = str(receipt.get("status") or "unknown")
            item["receipt"] = receipt
        except Exception as exc:  # Preserve each independent result for recovery.
            item["status"] = "failed"
            item["error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:500],
            }
        outcomes[index] = item
        batch["operations"] = outcomes
        batch["updated_at"] = utc_now()
        store.save(batch)

    succeeded = sum(1 for item in outcomes if item.get("status") == "succeeded")
    failed = len(outcomes) - succeeded
    batch["status"] = (
        "succeeded" if failed == 0 else ("failed" if succeeded == 0 else "partial")
    )
    batch["summary"] = {
        "total": len(outcomes),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": len(batch.get("skipped") or []),
    }
    batch["updated_at"] = utc_now()
    return store.save(batch)


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
    return (
        _service()
        .plan_operation(
            application_id,
            "remove",
            expected_revision=expected_revision,
            actor_ref=actor,
            subnet_ref=subnet,
            capability=granted,
            idempotency_key=key,
            data_policy=data_policy,
        )
        .to_dict()
    )


def plan_relocate_component(
    application_id: str,
    *,
    component_ref: str,
    target_node_id: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Plan an exact CAS-guarded change to one component placement."""

    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return (
        _service()
        .plan_operation(
            application_id,
            "relocate_component",
            component_ref=component_ref,
            target_node_id=target_node_id,
            expected_revision=expected_revision,
            actor_ref=actor,
            subnet_ref=subnet,
            capability=granted,
            idempotency_key=key,
        )
        .to_dict()
    )


def plan_install_component(
    application_id: str,
    *,
    component_ref: str,
    target_node_id: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Plan re-enabling one release component on an exact target node."""

    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return (
        _service()
        .plan_operation(
            application_id,
            "install_component",
            component_ref=component_ref,
            target_node_id=target_node_id,
            expected_revision=expected_revision,
            actor_ref=actor,
            subnet_ref=subnet,
            capability=granted,
            idempotency_key=key,
        )
        .to_dict()
    )


def plan_remove_component(
    application_id: str,
    *,
    component_ref: str,
    expected_revision: int,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Plan selective component uninstall without removing the Application."""

    actor, subnet, granted, key = _mutation_identity(
        actor_ref,
        subnet_ref,
        capability,
        idempotency_key,
        required_capability="applications.plan",
    )
    return (
        _service()
        .plan_operation(
            application_id,
            "remove_component",
            component_ref=component_ref,
            expected_revision=expected_revision,
            actor_ref=actor,
            subnet_ref=subnet,
            capability=granted,
            idempotency_key=key,
        )
        .to_dict()
    )


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
    return (
        _service()
        .plan_operation(
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
        )
        .to_dict()
    )


def apply_operation(
    operation_id: str,
    *,
    plan_digest: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    idempotency_key: str,
    webspace_id: str = "desktop",
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
    result = (
        _service()
        .apply_operation(
            operation_id,
            plan_digest=plan_digest,
            idempotency_key=key,
            actor_ref=actor,
            subnet_ref=subnet,
            capability=granted,
        )
        .to_dict()
    )
    kind = str(result.get("kind") or "").strip()
    application_id = str(result.get("application_id") or "").strip()
    if (
        str(result.get("status") or "").strip() == "succeeded"
        and application_id
        and kind in {"install", "remove"}
    ):
        try:
            result["home"] = _sync_home_installation(
                application_id,
                installed=kind == "install",
                webspace_id=str(webspace_id or "desktop").strip() or "desktop",
            )
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            result["home"] = {
                "schema": "adaos.application.home_projection.v1",
                "application_id": application_id,
                "webspace_id": str(webspace_id or "desktop").strip() or "desktop",
                "status": "sync_failed",
                "error": type(exc).__name__,
            }
    return result


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


def simulate_removal(
    application_id: str, *, data_policy: str = "retain"
) -> dict[str, Any]:
    return _service().simulate_removal(application_id, data_policy=data_policy)


def explain_plan(operation_id: str) -> dict[str, Any]:
    operation = _service().store.get_operation(operation_id)
    return {
        "operation_id": operation.operation_id,
        "plan_digest": operation.plan_digest,
        "plan": dict(operation.plan),
        "conflicts": list(operation.plan.get("conflicts") or []),
        "requires_snapshot": bool(
            (operation.plan.get("snapshot") or {}).get("required")
        ),
    }


__all__ = [
    "accept_development_report",
    "apply_operation",
    "apply_update_batch",
    "assess_updates",
    "change_application_access",
    "decide_application_access",
    "explain_plan",
    "export_application_access_snapshot",
    "get_application",
    "get_application_access_surface",
    "get_application_setup",
    "get_application_privacy_report",
    "get_application_placement_options",
    "get_application_update_review",
    "get_development_report",
    "get_development_report_status",
    "get_development_report_triage",
    "get_identity",
    "get_operation",
    "get_prerelease_rollout",
    "get_runtime_selection",
    "get_subscription",
    "get_update_batch",
    "get_users_access_surface",
    "grant_application_access",
    "import_application_access_snapshot",
    "issue_trial_access",
    "list_application_access",
    "list_application_access_audit",
    "list_application_access_reviews",
    "list_application_components",
    "list_application_placements",
    "list_applications",
    "list_catalog",
    "list_development_projects",
    "list_development_report_appeals",
    "list_development_report_intakes",
    "list_development_reports",
    "list_operations",
    "list_publisher_development_report_appeals",
    "list_releases",
    "list_trial_access",
    "plan_install",
    "plan_install_component",
    "plan_relocate_component",
    "plan_remove",
    "plan_remove_component",
    "plan_trial_link_install",
    "plan_update",
    "plan_available_updates",
    "plan_update_track",
    "poll_operation_events",
    "profile_application_permissions",
    "put_application_connected_account",
    "record_prerelease_health",
    "reorder_home_application",
    "request_development_report_resync",
    "resolve_development_report_appeal",
    "resolve_trial_link",
    "revoke_application_access",
    "revoke_trial_access",
    "select_runtime",
    "set_home_pinned",
    "set_development_report_status",
    "set_prerelease_rollout",
    "simulate_application_access",
    "simulate_removal",
    "submit_development_report",
    "submit_development_report_appeal",
    "sync_development_reports",
    "triage_development_report",
    "update_application_configuration",
    "update_application_credential",
    "verify_application_release",
    "verify_development_report_release",
]
