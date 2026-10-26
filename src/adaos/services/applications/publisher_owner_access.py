"""Core publisher-owner access materialization for an admitted exact release.

Legacy managed_by and idempotency identities are retained for existing grants.
"""
from typing import Any
from .access_management import ApplicationAccessManagementService


def publisher_owner_role_ids(release) -> tuple[tuple[str, ...], str]:
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


def ensure_publisher_owner_access(
    owner, service,
    application_id: str,
    *,
    release_digest: str,
    management=None,
) -> dict[str, Any]:
    """Materialize the publisher owner's reviewed access for an admitted release."""

    from adaos.services.personalization_runtime import current_user_id

    release = service.store.get_release(application_id, release_digest)
    if release.application_roles:
        role_ids, resolution = publisher_owner_role_ids(release)
    else:
        # A roleless Application still needs a reviewed permission grant.  Its
        # authority is the intersection of the release permission profile,
        # component capabilities and this ceiling; it deliberately has no
        # Application-local RBAC layer.
        role_ids, resolution = (), "permission_profile_only"
    owner_ref = f"user:{current_user_id(owner)}"
    constraints = {
        "platform_role": "owner",
        "subject_kind": "user",
        "managed_by": "builder.publisher_owner",
    }
    management = management or ApplicationAccessManagementService(service)
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
