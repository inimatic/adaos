from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from adaos.domain.application_access import (
    ApplicationAccessContractError,
    ApplicationPermissionProfile,
    is_high_risk_permission,
    normalize_application_roles,
)


APPLICATION_PERMISSION_CONTEXT_SCHEMA = "adaos.builder.application_permissions.v1"


def _manifest(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        raw = path.read_bytes()
        value = yaml.safe_load(raw.decode("utf-8-sig")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}, None
    return (
        dict(value) if isinstance(value, Mapping) else {},
        f"sha256:{hashlib.sha256(raw).hexdigest()}",
    )


def _owned_refs(project: Mapping[str, Any]) -> list[str]:
    components = project.get("components")
    if not isinstance(components, Mapping):
        return []
    return list(
        dict.fromkeys(
            str(item.get("ref") or "").strip()
            for item in components.get("owned") or []
            if isinstance(item, Mapping) and str(item.get("ref") or "").strip()
        )
    )


def _project_owner(
    component_ref: str,
    *,
    requested_project_ref: str | None,
    dev_projects_root: Path,
) -> tuple[str | None, Path | None, list[str]]:
    requested = str(requested_project_ref or "").strip()
    if requested.startswith("project:"):
        project_id = requested.split(":", 1)[1].strip()
        path = dev_projects_root / project_id / "project.yaml"
        if not path.is_file():
            return requested, None, ["owning DEV Project manifest is not available"]
        project, _ = _manifest(path)
        if project and (component_ref == requested or component_ref in _owned_refs(project)):
            return requested, path, []
        return None, None, ["requested Project does not own the Automation target"]

    owners: list[tuple[str, Path]] = []
    for path in sorted(dev_projects_root.glob("*/project.yaml")):
        project, _ = _manifest(path)
        project_id = str(project.get("id") or path.parent.name).strip()
        project_ref = f"project:{project_id}"
        if component_ref == project_ref or component_ref in _owned_refs(project):
            owners.append((project_ref, path))
    if len(owners) == 1:
        return owners[0][0], owners[0][1], []
    if not owners:
        return None, None, ["no owning DEV Project was resolved for the Automation target"]
    return None, None, ["Automation target has ambiguous DEV Project ownership"]


def _component_capabilities(
    project: Mapping[str, Any],
    *,
    dev_skills_root: Path,
) -> tuple[list[str], list[dict[str, Any]]]:
    inferred: list[str] = []
    sources: list[dict[str, Any]] = []
    for ref in _owned_refs(project):
        if not ref.startswith("skill:"):
            continue
        skill_id = ref.split(":", 1)[1]
        manifest, digest = _manifest(dev_skills_root / skill_id / "skill.yaml")
        capabilities = [
            str(item).strip().lower()
            for item in manifest.get("capabilities") or []
            if str(item).strip()
        ]
        inferred.extend(capabilities)
        sources.append(
            {
                "component_ref": ref,
                "manifest_ref": f"skills/{skill_id}/skill.yaml",
                "manifest_digest": digest,
                "capabilities": sorted(dict.fromkeys(capabilities)),
            }
        )
    return sorted(dict.fromkeys(inferred)), sources


def application_permissions_context(
    *,
    component_ref: str,
    requested_project_ref: str | None,
    dev_projects_root: Path,
    dev_skills_root: Path,
) -> dict[str, Any]:
    """Build the permission/role authoring input for one Builder Automation run.

    A missing declaration is model work, not missing context. Invalid or
    ambiguous authority is different: the packet becomes ambiguous and is
    rejected before model submission.
    """

    project_ref, manifest_path, diagnostics = _project_owner(
        component_ref,
        requested_project_ref=requested_project_ref,
        dev_projects_root=Path(dev_projects_root),
    )
    if manifest_path is None:
        return {
            "status": "present" if project_ref else "ambiguous",
            "schema": APPLICATION_PERMISSION_CONTEXT_SCHEMA,
            "project_ref": project_ref,
            "declaration_status": "unavailable",
            "authority_status": "unavailable" if project_ref else "ambiguous",
            "diagnostics": diagnostics,
        }

    project, project_digest = _manifest(manifest_path)
    inferred, inference_sources = _component_capabilities(
        project,
        dev_skills_root=Path(dev_skills_root),
    )
    declaration_status = "present" if isinstance(project.get("permission_profile"), Mapping) else "missing"
    try:
        profile = ApplicationPermissionProfile.from_mapping(
            project.get("permission_profile")
            if isinstance(project.get("permission_profile"), Mapping)
            else None,
            legacy_permissions=project.get("permissions") or (),
        )
        roles = normalize_application_roles(
            project.get("application_roles") or (),
            known_permissions=profile.flat_permissions,
        )
    except ApplicationAccessContractError as exc:
        return {
            "status": "ambiguous",
            "schema": APPLICATION_PERMISSION_CONTEXT_SCHEMA,
            "project_ref": project_ref,
            "manifest_ref": f"projects/{manifest_path.parent.name}/project.yaml",
            "manifest_digest": project_digest,
            "declaration_status": "invalid",
            "diagnostics": [str(exc)],
            "statically_inferred": inferred,
            "inference_sources": inference_sources,
        }

    declared = list(profile.flat_permissions)
    undeclared = sorted(set(inferred) - set(declared))
    unused = sorted(set(declared) - set(inferred))
    high_risk_undeclared = [item for item in undeclared if is_high_risk_permission(item)]
    role_matrix = {
        platform_role: [
            role.role_id
            for role in roles
            if platform_role in role.assignable_to
        ]
        for platform_role in ("owner", "co_owner", "admin", "member", "child", "guest")
    }
    return {
        "status": "present",
        "schema": APPLICATION_PERMISSION_CONTEXT_SCHEMA,
        "project_ref": project_ref,
        "manifest_ref": f"projects/{manifest_path.parent.name}/project.yaml",
        "manifest_digest": project_digest,
        "declaration_status": declaration_status,
        "profile": profile.to_dict(),
        "profile_digest": profile.digest,
        "roles": [role.to_dict() for role in roles],
        "role_matrix": role_matrix,
        "declared": declared,
        "statically_inferred": inferred,
        "undeclared_inferred": undeclared,
        "undeclared_high_risk": high_risk_undeclared,
        "unused_declared": unused,
        "inference_sources": inference_sources,
        "authoring_requirements": [
            "Keep project permission_profile aligned with every owned skill capability.",
            "Declare application_roles only when the application has differentiated rights; enforce rights in tools, not only in UI visibility.",
            "Add owner/member/child/guest access-matrix tests for every declared application role.",
            "Record secrets, external providers, model use, notifications, background work and data practices explicitly.",
        ],
        "diagnostics": diagnostics,
    }


__all__ = ["APPLICATION_PERMISSION_CONTEXT_SCHEMA", "application_permissions_context"]
