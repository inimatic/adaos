from __future__ import annotations

import hashlib
from collections.abc import Mapping
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


def _authoring_contract() -> dict[str, Any]:
    """Return the exact compact contract used to author Project access metadata."""

    return {
        "schema": "adaos.builder.application_permission_authoring.v1",
        "permission_profile_schema": "adaos.application.permission_profile.v1",
        "canonical_identifier": {
            "pattern": "^[a-z0-9][a-z0-9_.-]{0,127}$",
            "meaning": "stable machine identifier; never prose or localized copy",
            "fields": [
                "permission_profile.{required,optional}[].id",
                "permission_profile.{required,optional}[].approval_policy",
                "permission_profile.secrets[].{id,provider,binding}",
                "permission_profile.data_practices.collected[]",
                "permission_profile.data_practices.sent_off_device[]",
                "permission_profile.data_practices.linked_to_user[]",
                "permission_profile.{llm_model_use,notifications,background_actions,external_providers}[].id",
                "application_roles[].{id,grants,assignable_to,requires_permissions}",
                "application_roles[].default_for keys and values",
            ],
            "valid_examples": [
                "volunteer_contact",
                "shift_assignment",
                "until_record_deleted",
            ],
            "invalid_examples": [
                "private phone and email",
                "Until a coordinator removes records",
            ],
        },
        "semantics": {
            "permission_ids": "runtime capabilities used by owned skills; declare each as required or optional with a concise purpose",
            "role_grants": "application-scoped action identifiers granted by the role",
            "role_requires_permissions": "runtime permission IDs needed to exercise the role grants",
            "role_defaults": (
                "declare exactly one application_roles[].default_for.owner for the local "
                "publisher; member, child and guest defaults remain product choices"
            ),
            "privacy_labels": "human- and policy-facing summary derived from data_practices; it does not replace canonical data category IDs",
            "retention": "optional concise text, at most 120 characters; a stable identifier such as until_record_deleted is preferred",
        },
        "tool_runtime_contract": {
            "permissions": (
                "skill.tools[].permissions is trusted component evidence; narrow it to the "
                "permission used by that tool. Skill-level capabilities remain a compatible "
                "fallback, but apply to every tool."
            ),
            "application_access": {
                "permission": (
                    "permission_profile ID consumed by this tool; when omitted it is derived "
                    "from side_effects"
                ),
                "capability": (
                    "application action checked against application_roles[].grants; when omitted "
                    "the permission ID is used"
                ),
            },
            "example": {
                "side_effects": "local_write",
                "permissions": ["workspace.write"],
                "application_access": {
                    "permission": "workspace.write",
                    "capability": "roster.manage",
                },
            },
        },
        "platform_roles": ["owner", "co_owner", "admin", "member", "child", "guest"],
        "trial_evidence_contract": {
            "artifact_path": "tests/test_application_contract.py",
            "required_when": "the owning Project declares a permission_profile",
            "with_application_roles": (
                "exercise the owner/member/child/guest access matrix for every declared role"
            ),
            "without_application_roles": (
                "prove permission enforcement is delegated to the trusted Core/Root boundary "
                "and that the package does not create a local role store"
            ),
            "admission": (
                "Automation must execute and seal this exact package-relative test artifact; "
                "passing equivalent tests under another filename is not Trial evidence"
            ),
        },
        "validation_boundary": (
            "The model may repair an invalid declaration, but the declaration grants no authority "
            "and Automation cannot complete until trusted validation accepts it."
        ),
    }


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
            "authoring_contract": _authoring_contract(),
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
            # Ownership is resolved, so the invalid declaration is safe to expose
            # as repair input. It remains non-authoritative and trusted candidate
            # validation below still rejects it until the strict parser succeeds.
            "status": "present",
            "schema": APPLICATION_PERMISSION_CONTEXT_SCHEMA,
            "project_ref": project_ref,
            "manifest_ref": f"projects/{manifest_path.parent.name}/project.yaml",
            "manifest_digest": project_digest,
            "declaration_status": "invalid",
            "authority_status": "invalid",
            "repair_required": True,
            "authoring_contract": _authoring_contract(),
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
        "authority_status": "valid" if declaration_status == "present" else "undeclared",
        "repair_required": declaration_status != "present",
        "authoring_contract": _authoring_contract(),
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
            "Narrow each tool's permissions and map application_access capability to an application role grant when domain actions differ from permission IDs.",
            "For durable uploaded bytes declare storage.blob and separate authorized upload/read tools; workspace.write gates upload and workspace.read gates retrieval.",
            "Declare application_roles only when the application has differentiated rights; enforce rights in tools, not only in UI visibility.",
            "When roles are declared, mark exactly one owner-compatible role with default_for: {owner: <same-role-id>} so Builder can provision publisher access without guessing.",
            "Create tests/test_application_contract.py for every declared permission_profile. With application roles, test the owner/member/child/guest matrix; without roles, prove trusted Core/Root enforcement and no local role store. Trial admission recognizes this exact sealed artifact path.",
            "Record secrets, external providers, model use, notifications, background work and data practices explicitly.",
            "Use canonical machine identifiers for data-practice categories; keep prose in purposes, titles, retention policy, catalog copy or README.",
        ],
        "diagnostics": diagnostics,
    }


__all__ = ["APPLICATION_PERMISSION_CONTEXT_SCHEMA", "application_permissions_context"]
