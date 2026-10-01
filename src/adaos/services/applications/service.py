from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Callable, Mapping

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from adaos.domain.application import (
    Application,
    ApplicationInstallation,
    ApplicationOperation,
    ApplicationRelease,
    ApplicationSubscription,
    RuntimeSelection,
    utc_now,
)
from adaos.domain.artifact_release import WorkspaceLock, canonical_payload_digest
from adaos.domain.application_access import classify_access_profile_diff
from adaos.services.artifact_pipeline.releases import normalize_version_spec
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.trial_activation import (
    shared_skill_contract_fingerprint,
)
from adaos.services.mutation_lock import mutation_lock

from .store import ApplicationStore
from .runtime_channel import ApplicationRuntimeChannel


class ApplicationServiceError(RuntimeError):
    pass


class ApplicationPlanConflict(ApplicationServiceError):
    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        super().__init__("Application plan contains active component conflicts")
        self.conflicts = conflicts


ApplicationExecutor = Callable[[Mapping[str, Any]], Mapping[str, Any]]
ApplicationOperationPublisher = Callable[[Mapping[str, Any]], Any]

# Auto-update idempotency names the planning contract as well as the requested
# release. Bump this only when the same immutable update intent can produce a
# materially different reviewed plan (for example after adding native CBS
# rebinding evidence). Existing operations remain immutable audit records.
APPLICATION_OPERATION_PLANNER_REVISION = "resolved-dependency-cbs-rebinding.v2"


def _require_authority(
    *,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    required_capability: str,
) -> dict[str, str]:
    actor = str(actor_ref or "").strip()
    subnet = str(subnet_ref or "").strip()
    granted = str(capability or "").strip()
    if not actor:
        raise ApplicationServiceError("actor_ref is required")
    if not subnet.startswith("subnet:"):
        raise ApplicationServiceError("subnet_ref must use subnet:<id>")
    accepted = {required_capability}
    if required_capability == "applications.plan":
        accepted.add("applications.trial.install")
    if granted not in accepted:
        raise ApplicationServiceError(f"{required_capability} capability is required")
    return {"actor_ref": actor, "subnet_ref": subnet, "capability": granted}


class ApplicationService:
    """Product-level Application lifecycle over Artifact/Deployment evidence."""

    def __init__(
        self,
        store: ApplicationStore,
        *,
        executor: ApplicationExecutor | None = None,
        operation_publisher: ApplicationOperationPublisher | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.operation_publisher = operation_publisher

    def _publish_operation(self, operation: ApplicationOperation) -> None:
        if self.operation_publisher is None:
            return
        self.operation_publisher(
            {
                "schema": "adaos.application.operation_notification.v1",
                "event_id": f"{operation.operation_id}:{operation.revision}",
                "application_id": operation.application_id,
                "operation_id": operation.operation_id,
                "operation_revision": operation.revision,
                "status": operation.status,
                "occurred_at": operation.updated_at,
                "operation": operation.to_dict(),
            }
        )

    def register(
        self, application: Application, *, expected_revision: int = 0
    ) -> Application:
        return self.store.save_application(
            application, expected_revision=expected_revision
        )

    def register_release(self, release: ApplicationRelease) -> ApplicationRelease:
        return self.store.put_release(release)

    def _application_is_present(self, application_id: str) -> bool:
        try:
            installation = self.store.get_installation(application_id)
        except FileNotFoundError:
            installation = None
        if installation is not None and installation.status != "removed":
            return True
        return any(
            item.application_id == application_id
            for item in self.store.list_runtime_selections()
        )

    def _present_managed_projects(
        self, owner_application_id: str
    ) -> tuple[Application, ...]:
        return tuple(
            item
            for item in self.store.list_managed_applications(owner_application_id)
            if self._application_is_present(item.application_id)
        )

    def _assert_management_preconditions(
        self, application: Application, operation_kind: str
    ) -> None:
        if application.kind == "project":
            if operation_kind == "select_track":
                raise ApplicationServiceError(
                    "managed Project update track is controlled by its owner Application"
                )
            if operation_kind == "install" and not self._application_is_present(
                str(application.owner_application_id)
            ):
                raise ApplicationServiceError(
                    "managed Project requires its owner Application to be installed or selected"
                )
        if application.kind == "application" and operation_kind == "remove":
            present_projects = self._present_managed_projects(
                application.application_id
            )
            if present_projects:
                project_ids = ", ".join(
                    item.application_id for item in present_projects
                )
                raise ApplicationServiceError(
                    "owner Application cannot be removed while managed Projects are active: "
                    + project_ids
                )

    def ensure_install_access(
        self,
        application_id: str,
        *,
        release_digest: str,
        subnet_ref: str,
        issuer_ref: str,
        reviewed_permissions: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Materialize permissions accepted at the install approval boundary.

        ``grant_on_install`` declarations are safe for unattended/system
        reconciliation.  An authenticated user applying an exact reviewed
        plan has also explicitly accepted that plan's permission review, so
        its bounded permission set is durable for that user and release.
        Runtime pending actions remain responsible for permissions outside
        that reviewed set.
        """

        release = self.store.get_release(application_id, release_digest)
        declarations = (
            *release.permission_profile.required,
            *release.permission_profile.optional,
        )
        install_permissions = {
            item.permission_id
            for item in declarations
            if item.approval_policy == "grant_on_install"
        }
        reviewed = {
            str(item or "").strip().lower()
            for item in reviewed_permissions
            if str(item or "").strip()
        }
        declared = set(release.permission_profile.flat_permissions)
        if not reviewed.issubset(declared):
            raise ApplicationServiceError(
                "reviewed install permissions are not declared by the release"
            )
        issuer = str(issuer_ref or "").strip()
        subnet = str(subnet_ref or "").strip().removeprefix("subnet:")
        if not subnet:
            raise ApplicationServiceError(
                "subnet_ref is required for install-time Application access"
            )
        subject_ref = issuer if issuer.startswith("user:") else f"user:{subnet}"
        # Only a concrete user can turn a reviewed plan into durable personal
        # authority. System reconciliation remains limited to declarations
        # that explicitly opt into grant_on_install.
        if issuer.startswith("user:"):
            install_permissions.update(reviewed)
            # Updating an unchanged permission profile must not revoke the
            # user's already-reviewed authority merely because the update UI
            # only asks about the delta. Carry the prior ceiling forward, but
            # never beyond permissions declared by the target release.
            for prior in self.store.list_application_access_grants(
                application_id,
                subject_ref=subject_ref,
            ):
                if prior.status == "active":
                    install_permissions.update(
                        set(prior.permission_ceiling).intersection(declared)
                    )
        permissions = tuple(
            sorted(
                install_permissions
            )
        )
        if not permissions:
            return {
                "schema": "adaos.application.install_access.v1",
                "status": "not_required",
                "application_id": application_id,
                "release_digest": release_digest,
                "permissions": [],
            }
        owner_roles = tuple(
            sorted(
                role.role_id
                for role in release.application_roles
                if role.default_for.get("owner") == role.role_id
            )
        )
        # A reviewed install performed by an authenticated user must grant the
        # declared install-time permissions to that user.  The old subnet
        # surrogate (user:<subnet-id>) was never the browser subject and left
        # owners facing an unfulfillable approval prompt immediately after a
        # successful install.  System callers retain the surrogate fallback;
        # bootstrap resolves the actual local owner before calling us.
        for grant in self.store.list_application_access_grants(
            application_id,
            subject_ref=subject_ref,
        ):
            if (
                grant.status == "active"
                and grant.reviewed_permission_profile_digest
                == release.permission_profile.digest
                and set(permissions).issubset(grant.permission_ceiling)
                and set(owner_roles).issubset(grant.application_roles)
            ):
                return {
                    "schema": "adaos.application.install_access.v1",
                    "status": "ready",
                    "application_id": application_id,
                    "release_digest": release_digest,
                    "permissions": list(permissions),
                    "application_roles": list(owner_roles),
                    "grant": grant.to_dict(),
                    "created": False,
                }

        from .access import ApplicationAccessService

        grant = ApplicationAccessService(self).grant_access(
            application_id,
            release_digest=release_digest,
            subject_ref=subject_ref,
            application_roles=owner_roles,
            permission_ceiling=permissions,
            issuer_ref=str(issuer_ref or "system:application-install"),
            idempotency_key=f"grant-on-install:{release_digest}",
            constraints={
                "subject_kind": "user",
                "platform_role": "owner",
                "profile_binding": True,
                "session_bound": False,
                "durable_approvals": True,
                "provisioned_by": "application_install",
            },
        )
        return {
            "schema": "adaos.application.install_access.v1",
            "status": "ready",
            "application_id": application_id,
            "release_digest": release_digest,
            "permissions": list(permissions),
            "application_roles": list(owner_roles),
            "grant": grant.to_dict(),
            "created": True,
        }

    def reconcile_workspace_installation(
        self, application_id: str, release_digest: str, workspace_lock: WorkspaceLock
    ) -> ApplicationInstallation:
        """Adopt a native publication only after its exact closure is active."""
        application = self.store.get_application(application_id)
        release = self.store.get_release(application_id, release_digest)
        packages = {item.key: item.digest for item in workspace_lock.components}
        if not any(
            slot.project_id == application.legacy_project_id
            and slot.release_digest == release_digest
            for slot in workspace_lock.slots
        ):
            raise ApplicationServiceError(
                "Workspace does not contain this exact Application release"
            )
        if any(
            packages.get(item.key) != item.digest
            for item in release.project_release.components
        ):
            raise ApplicationServiceError(
                "Workspace Application package closure differs from the release"
            )
        if any(
            packages.get(item.key) != item.package_digest
            for item in release.project_release.resolved_dependencies
        ):
            raise ApplicationServiceError(
                "Workspace Application dependency closure differs from the release"
            )
        refs = tuple(self._release_components(release))
        shared_project_bindings = self._release_shared_project_bindings(release)
        try:
            current = self.store.get_installation(application_id)
        except FileNotFoundError:
            current = None
        if (
            current
            and current.status == "active"
            and current.installed_release_digest == release_digest
            and current.component_refs == refs
            and current.shared_project_bindings == shared_project_bindings
        ):
            return current
        if current and current.status not in {"active", "removed"}:
            raise ApplicationServiceError(
                "Resolve the in-progress Application installation first"
            )
        value = (
            replace(
                current,
                installed_release_digest=release_digest,
                component_refs=refs,
                shared_project_bindings=shared_project_bindings,
                status="active",
                revision=current.revision + 1,
                updated_at=utc_now(),
            )
            if current
            else ApplicationInstallation(
                installation_id=f"installation:{application_id}",
                application_id=application_id,
                installed_release_digest=release_digest,
                component_refs=refs,
                shared_project_bindings=shared_project_bindings,
                data_policy="retain",
                status="active",
                revision=1,
            )
        )
        return self.store.save_installation(
            value, expected_revision=current.revision if current else 0
        )

    def list_releases(self, application_id: str) -> list[dict[str, Any]]:
        channels = self.store.get_channels(application_id).get("channels") or {}
        return [
            {
                **release.to_dict(),
                "channels": [
                    name
                    for name, digest in channels.items()
                    if digest == release.release_digest
                ],
            }
            for release in self.store.list_releases(application_id)
        ]

    def move_channel(
        self,
        application_id: str,
        channel: str,
        release_digest: str,
        *,
        publisher_ref: str,
        expected_release_digest: str | None,
    ) -> dict[str, Any]:
        application = self.store.get_application(application_id)
        if application.publisher_ref != publisher_ref:
            raise ApplicationServiceError(
                "only the Application publisher may move channels"
            )
        release = self.store.get_release(application_id, release_digest)
        if release.publisher_ref != publisher_ref:
            raise ApplicationServiceError("release publisher does not own Application")
        channels = self.store.get_channels(application_id).get("channels") or {}
        channel_id = str(channel or "").strip().lower()
        if channel_id == "prerelease" and not channels.get("stable"):
            raise ApplicationServiceError(
                "public prerelease requires an existing stable release"
            )
        if channel_id == "stable" and channels.get("stable"):
            if channels.get("prerelease") != release_digest:
                raise ApplicationServiceError(
                    "later stable must promote the exact current prerelease digest"
                )
        updated = self.store.set_channel(
            application_id,
            channel_id,
            release_digest,
            expected_release_digest=expected_release_digest,
        )
        if (
            channel_id == "stable"
            and updated.get("channels", {}).get("prerelease") == release_digest
        ):
            updated = self.store.set_channel(
                application_id,
                "prerelease",
                None,
                expected_release_digest=release_digest,
            )
        return updated

    def set_subscription(
        self,
        application_id: str,
        *,
        update_track: str,
        update_policy: str,
        paused: bool,
        expected_revision: int,
        observed_release_digest: str | None = None,
        pinned_release_digest: str | None = None,
    ) -> ApplicationSubscription:
        try:
            current = self.store.get_subscription(application_id)
        except FileNotFoundError:
            current = None
        if (current.revision if current else 0) != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(
                expected=expected_revision, observed=current.revision if current else 0
            )
        value = ApplicationSubscription(
            application_id=application_id,
            update_track=update_track,  # type: ignore[arg-type]
            update_policy=update_policy,  # type: ignore[arg-type]
            observed_release_digest=observed_release_digest,
            pinned_release_digest=pinned_release_digest,
            paused=paused,
            revision=expected_revision + 1,
        )
        return self.store.save_subscription(value, expected_revision=expected_revision)

    def effective_release(
        self,
        application_id: str,
        *,
        subscriber_subnet_ref: str | None = None,
        include_release: bool = True,
    ) -> dict[str, Any]:
        channels = dict(self.store.get_channels(application_id).get("channels") or {})
        try:
            subscription = self.store.get_subscription(application_id)
        except FileNotFoundError:
            subscription = None
        return self._effective_release_from_state(
            application_id,
            channels=channels,
            subscription=subscription,
            subscriber_subnet_ref=subscriber_subnet_ref,
            include_release=include_release,
        )

    def _effective_release_from_state(
        self,
        application_id: str,
        *,
        channels: Mapping[str, Any],
        subscription: ApplicationSubscription | None,
        subscriber_subnet_ref: str | None,
        include_release: bool,
    ) -> dict[str, Any]:
        """Resolve a release from one already-consistent aggregate snapshot."""

        if subscription is None:
            subscription = ApplicationSubscription(
                application_id=application_id,
                update_track="stable",
                update_policy="auto_compatible",
                revision=1,
            )
        if (
            subscription.update_policy == "pinned"
            and subscription.pinned_release_digest
        ):
            digest = subscription.pinned_release_digest
            effective_channel = "pinned"
        elif subscription.update_track == "prerelease" and channels.get("prerelease"):
            digest = str(channels["prerelease"])
            effective_channel = "prerelease"
        else:
            digest = str(channels.get("stable") or "")
            effective_channel = "stable"
        if not digest:
            return {
                "application_id": application_id,
                "update_track": subscription.update_track,
                "effective_channel": None,
                "release_digest": None,
                "reason": "channel_unavailable",
            }
        rollout = None
        if effective_channel == "prerelease":
            from .rollout import ApplicationRolloutService

            rollout = ApplicationRolloutService(self).assignment(
                application_id,
                digest,
                subscriber_subnet_ref=subscriber_subnet_ref,
            )
            if not rollout["eligible"]:
                digest = str(channels.get("stable") or "")
                effective_channel = "stable" if digest else None
        if not digest:
            return {
                "application_id": application_id,
                "update_track": subscription.update_track,
                "effective_channel": None,
                "release_digest": None,
                "reason": "rollout_not_eligible",
                "rollout": rollout,
            }
        result = {
            "application_id": application_id,
            "update_track": subscription.update_track,
            "effective_channel": effective_channel,
            "release_digest": digest,
            "reason": "resolved"
            if rollout is None or rollout["eligible"]
            else "stable_rollout_fallback",
            "rollout": rollout,
        }
        if include_release:
            result["release"] = self.store.get_release(application_id, digest).to_dict()
        return result

    def select_runtime(
        self,
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
    ) -> RuntimeSelection:
        _require_authority(
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability=capability,
            required_capability="applications.apply",
        )
        self.store.get_release(application_id, release_digest)
        try:
            current = self.store.get_runtime_selection(webspace_id, application_id)
        except FileNotFoundError:
            current = None
        observed_revision = current.revision if current is not None else 0
        if observed_revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(
                expected=expected_revision, observed=observed_revision
            )
        if current is None:
            value = RuntimeSelection(
                webspace_id=webspace_id,
                application_id=application_id,
                source=source,  # type: ignore[arg-type]
                release_digest=release_digest,
                runtime_root_ref=runtime_root_ref,
                revision=1,
            )
        else:
            value = current.advance(
                expected_revision=expected_revision,
                source=source,
                release_digest=release_digest,
                runtime_root_ref=runtime_root_ref,
            )
        return self.store.save_runtime_selection(
            value, expected_revision=expected_revision
        )

    @staticmethod
    def _native_admission_status(
        *,
        application_id: str,
        release_digest: str,
        admission: Mapping[str, Any] | None,
    ) -> tuple[bool, str]:
        """Qualify one exact Application admission for runtime activation.

        Prototype/Scenario admissions intentionally remain on the compatibility
        lifecycle.  An Application admission, on the other hand, is an
        activation precondition and must be complete rather than being treated
        as a best-effort hint returned by the deployment provider.
        """

        if not isinstance(admission, Mapping):
            return False, "admission_absent"
        expected_ref = f"application:{application_id}"
        observed_ref = str(admission.get("application_ref") or "").strip()
        if observed_ref != expected_ref:
            return False, "compatibility_admission"
        if str(admission.get("status") or "") != "admitted":
            raise ApplicationServiceError("native CBS admission is not admitted")
        if str(admission.get("project_release_digest") or "") != release_digest:
            raise ApplicationServiceError(
                "native CBS admission does not match the installed release"
            )
        admission_digest = str(admission.get("admission_digest") or "")
        if not admission_digest.startswith("sha256:"):
            raise ApplicationServiceError(
                "native CBS admission has no immutable digest"
            )
        try:
            total = int(admission.get("requirements_total"))
            resolved = int(admission.get("requirements_resolved"))
        except (TypeError, ValueError) as exc:
            raise ApplicationServiceError(
                "native CBS admission requirement counts are invalid"
            ) from exc
        resolutions = [
            dict(item)
            for item in admission.get("resolutions") or ()
            if isinstance(item, Mapping)
        ]
        plans = [
            dict(item)
            for item in admission.get("plans") or ()
            if isinstance(item, Mapping)
        ]
        if (
            total <= 0
            or resolved != total
            or len(resolutions) != total
            or len(plans) != total
        ):
            raise ApplicationServiceError("native CBS admission is incomplete")
        resolution_identities = {
            (
                str(item.get("resolution_ref") or ""),
                str(item.get("resolution_digest") or ""),
            )
            for item in resolutions
            if str(item.get("project_release_digest") or "") == release_digest
        }
        planned_identities = {
            (
                str(item.get("application_resolution_ref") or ""),
                str(item.get("application_resolution_digest") or ""),
            )
            for item in plans
        }
        if (
            len(resolution_identities) != total
            or resolution_identities != planned_identities
            or any(not ref or not digest for ref, digest in resolution_identities)
        ):
            raise ApplicationServiceError(
                "native CBS admission resolution plans are not exact"
            )
        return True, "native_application_admission"

    def _activate_native_runtime(
        self,
        *,
        application_id: str,
        release_digest: str,
        admission: Mapping[str, Any] | None,
        runtime_target: Mapping[str, Any],
        actor_ref: str,
        subnet_ref: str,
    ) -> dict[str, Any]:
        native, reason = self._native_admission_status(
            application_id=application_id,
            release_digest=release_digest,
            admission=admission,
        )
        if not native:
            return {
                "status": "compatibility_runtime",
                "changed": False,
                "reason": reason,
            }
        webspace_id = str(runtime_target.get("webspace_id") or "").strip()
        source = str(runtime_target.get("source") or "")
        runtime_root_ref = str(runtime_target.get("runtime_root_ref") or "")
        try:
            expected_revision = int(runtime_target.get("expected_revision"))
        except (TypeError, ValueError) as exc:
            raise ApplicationServiceError(
                "native CBS runtime target revision is invalid"
            ) from exc
        if (
            not webspace_id
            or source != "stable_installation"
            or runtime_root_ref != "workspace"
        ):
            raise ApplicationServiceError("native CBS runtime target is invalid")
        try:
            current = self.store.get_runtime_selection(webspace_id, application_id)
        except FileNotFoundError:
            current = None
        if (
            current is not None
            and current.source == "stable_installation"
            and current.release_digest == release_digest
            and current.runtime_root_ref == "workspace"
        ):
            return {
                "status": "current",
                "changed": False,
                "admission_digest": admission.get("admission_digest"),
                "selection": current.to_dict(),
            }
        observed_revision = current.revision if current is not None else 0
        if observed_revision != expected_revision:
            raise ApplicationServiceError(
                "native CBS runtime target changed after plan review"
            )
        selected = self.select_runtime(
            webspace_id=webspace_id,
            application_id=application_id,
            source="stable_installation",
            release_digest=release_digest,
            runtime_root_ref="workspace",
            expected_revision=expected_revision,
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability="applications.apply",
        )
        return {
            "status": "activated",
            "changed": True,
            "admission_digest": admission.get("admission_digest"),
            "selection": selected.to_dict(),
        }

    def reconcile_native_runtime_selection(
        self,
        *,
        application_id: str,
        release_digest: str,
        admission: Mapping[str, Any] | None,
        webspace_id: str = "desktop",
        actor_ref: str,
        subnet_ref: str,
        allow_exact_trial_transition: bool = False,
    ) -> dict[str, Any]:
        """Adopt an already installed exact native release without overriding Trial."""

        installation = self.store.get_installation(application_id)
        if (
            installation.status != "active"
            or installation.installed_release_digest != release_digest
        ):
            return {"status": "not_current", "changed": False}
        try:
            current = self.store.get_runtime_selection(webspace_id, application_id)
        except FileNotFoundError:
            current = None
        if current is not None and current.source != "stable_installation":
            if not (
                allow_exact_trial_transition
                and current.release_digest == release_digest
                and current.runtime_root_ref.startswith("trial:")
            ):
                return {
                    "status": "trial_active",
                    "changed": False,
                    "selection": current.to_dict(),
                }
        return self._activate_native_runtime(
            application_id=application_id,
            release_digest=release_digest,
            admission=admission,
            runtime_target={
                "webspace_id": webspace_id,
                "source": "stable_installation",
                "runtime_root_ref": "workspace",
                "expected_revision": current.revision if current is not None else 0,
            },
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
        )

    def reconcile_runtime_selection(
        self,
        webspace_id: str,
        application_id: str,
        *,
        runtime_root_exists: Callable[[str], bool],
        rematerialize: Callable[[RuntimeSelection], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        selection = self.store.get_runtime_selection(webspace_id, application_id)
        if runtime_root_exists(selection.runtime_root_ref):
            return {
                "status": "ready",
                "selection": selection.to_dict(),
                "repaired": False,
            }
        if rematerialize is None:
            return {
                "status": "missing_runtime_root",
                "selection": selection.to_dict(),
                "repaired": False,
                "recovery_reason": "immutable_release_rematerialization_required",
            }
        result = dict(rematerialize(selection))
        if not runtime_root_exists(selection.runtime_root_ref):
            return {
                "status": "recovery_failed",
                "selection": selection.to_dict(),
                "repaired": False,
                "result": result,
            }
        return {
            "status": "ready",
            "selection": selection.to_dict(),
            "repaired": True,
            "result": result,
        }

    def component_references(self) -> dict[str, Any]:
        references: dict[str, list[dict[str, Any]]] = {}
        for installation in self.store.list_installations():
            if installation.status == "removed":
                continue
            for component in installation.component_refs:
                key = str(component["component_ref"])
                references.setdefault(key, []).append(
                    {
                        "application_id": installation.application_id,
                        "installation_id": installation.installation_id,
                        "package_digest": component["package_digest"],
                        "lifecycle": component["lifecycle"],
                        "active_runtime_leases": list(
                            installation.active_runtime_leases
                        ),
                        "rollback_holds": list(installation.rollback_holds),
                        "uncertain_operation_refs": list(
                            installation.uncertain_operation_refs
                        ),
                    }
                )
        return {
            "schema": "adaos.application.component_reference_index.v1",
            "components": {key: references[key] for key in sorted(references)},
        }

    def _release_components(self, release: ApplicationRelease) -> list[dict[str, Any]]:
        lifecycle_by_ref: dict[str, str] = {}
        composition = release.project_release.composition_lock
        if composition is not None:
            lifecycle_by_ref = {
                member.ref: member.lifecycle for member in composition.members
            }
        components = [
            {
                "component_ref": component.key,
                "package_digest": component.digest,
                "lifecycle": lifecycle_by_ref.get(component.key, "bound"),
            }
            for component in release.project_release.components
        ]
        by_ref = {item["component_ref"]: item for item in components}
        for dependency in release.project_release.resolved_dependencies:
            candidate = {
                "component_ref": dependency.key,
                "package_digest": dependency.package_digest,
                "lifecycle": "shared",
            }
            previous = by_ref.get(dependency.key)
            if previous is not None and previous != candidate:
                raise ApplicationServiceError(
                    "resolved dependency conflicts with an owned release component"
                )
            if previous is None:
                components.append(candidate)
                by_ref[dependency.key] = candidate
        return sorted(components, key=lambda item: item["component_ref"])

    @staticmethod
    def _release_shared_project_bindings(
        release: ApplicationRelease,
    ) -> tuple[dict[str, Any], ...]:
        composition = release.project_release.composition_lock
        if composition is None:
            return ()
        return tuple(
            {
                "provider_project_ref": item.project_ref,
                "project_release_digest": item.release_digest,
                "version_spec": item.version_spec,
            }
            for item in composition.project_dependencies
        )

    def _shared_component_materialization(
        self,
        application_id: str,
        components: list[dict[str, Any]],
        *,
        allow_reuse: bool,
    ) -> list[dict[str, Any]]:
        references = self.component_references()["components"]
        result: list[dict[str, Any]] = []
        for raw in components:
            item = dict(raw)
            providers = [
                ref
                for ref in references.get(item["component_ref"], ())
                if ref.get("application_id") != application_id
                and ref.get("package_digest") == item["package_digest"]
            ]
            reusable = bool(
                allow_reuse and item.get("lifecycle") == "shared" and providers
            )
            item["materialization"] = "reuse" if reusable else "activate"
            item["reused_from_application_ids"] = sorted(
                {
                    str(ref.get("application_id") or "")
                    for ref in providers
                    if str(ref.get("application_id") or "")
                }
            )
            result.append(item)
        return result

    def _release_package_refs(
        self, release: ApplicationRelease
    ) -> dict[str, Any]:
        """Resolve complete immutable package identities for lifecycle proof.

        Owned components carry full package references in ProjectRelease while
        resolved dependencies deliberately carry only compact lock data.  Read
        already materialized dependencies from the content-addressed store and
        ask the authenticated deployment release transport for missing target
        identities.  A mismatch remains fail-closed.
        """

        result = {item.key: item for item in release.project_release.components}
        dependencies = {
            item.key: item for item in release.project_release.resolved_dependencies
        }
        missing = [key for key in dependencies if key not in result]
        package_store = ContentAddressedPackageStore(
            self.store.state_dir / "artifact_pipeline" / "packages"
        )
        for key in tuple(missing):
            dependency = dependencies[key]
            try:
                _archive, verified = package_store.read_verified(
                    dependency.package_digest
                )
            except (FileNotFoundError, OSError, ValueError):
                continue
            package = verified.ref
            if (
                package.key != key
                or package.version != dependency.version
                or package.digest != dependency.package_digest
            ):
                raise ApplicationServiceError(
                    "resolved dependency package identity changed"
                )
            result[key] = package
            missing.remove(key)
        if missing:
            provider = getattr(self.executor, "release_packages", None)
            if callable(provider):
                supplied = provider(
                    release.project_release.project_id,
                    release.release_digest,
                )
                for package in supplied:
                    dependency = dependencies.get(package.key)
                    if dependency is None or package.key not in missing:
                        continue
                    if (
                        package.version != dependency.version
                        or package.digest != dependency.package_digest
                    ):
                        raise ApplicationServiceError(
                            "release transport dependency identity changed"
                        )
                    result[package.key] = package
                    missing.remove(package.key)
        return result

    def _component_conflicts(
        self,
        application_id: str,
        components: list[dict[str, Any]],
        *,
        admitted_rebindings: tuple[Mapping[str, Any], ...] = (),
    ) -> list[dict[str, Any]]:
        target = {item["component_ref"]: item["package_digest"] for item in components}
        admitted = {
            (
                str(item.get("consumer_application_id") or ""),
                str(item.get("component_ref") or ""),
                str(item.get("from_package_digest") or ""),
                str(item.get("to_package_digest") or ""),
            )
            for item in admitted_rebindings
        }
        conflicts: list[dict[str, Any]] = []
        for installation in self.store.list_installations():
            if (
                installation.application_id == application_id
                or installation.status == "removed"
            ):
                continue
            for current in installation.component_refs:
                requested = target.get(current["component_ref"])
                if requested and requested != current["package_digest"]:
                    transition = (
                        installation.application_id,
                        current["component_ref"],
                        current["package_digest"],
                        requested,
                    )
                    if transition in admitted:
                        continue
                    conflicts.append(
                        {
                            "component_ref": current["component_ref"],
                            "requested_digest": requested,
                            "active_digest": current["package_digest"],
                            "active_application_id": installation.application_id,
                            "reason": "side_by_side_component_versions_not_supported",
                        }
                    )
        return sorted(
            conflicts,
            key=lambda item: (item["component_ref"], item["active_application_id"]),
        )

    def _shared_dependency_rebindings(
        self,
        application_id: str,
        release: ApplicationRelease,
        components: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        """Find consumers that can follow a compatible provider release.

        This is deliberately narrower than matching a component name.  A
        consumer may follow either a shared Project dependency or a directly
        resolved shared component dependency.  In both cases its immutable
        version range must admit the provider's target version and its current
        local binding must still point at the provider's active package.
        """

        try:
            provider_installation = self.store.get_installation(application_id)
        except FileNotFoundError:
            return ()
        if provider_installation.status == "removed":
            return ()
        try:
            provider_release = self.store.get_release(
                application_id, provider_installation.installed_release_digest
            )
        except FileNotFoundError:
            return ()
        current_project_digest = str(
            provider_release.project_release.release_digest or ""
        )
        target_project = release.project_release
        target_project_digest = str(target_project.release_digest or "")
        if not current_project_digest or not target_project_digest:
            return ()
        target_by_ref = {
            item["component_ref"]: item["package_digest"] for item in components
        }
        current_packages = self._release_package_refs(provider_release)
        target_packages = self._release_package_refs(release)
        provider_ref = f"project:{target_project.project_id}"
        result: list[dict[str, Any]] = []
        for installation in self.store.list_installations():
            if (
                installation.application_id == application_id
                or installation.status == "removed"
            ):
                continue
            try:
                consumer_release = self.store.get_release(
                    installation.application_id,
                    installation.installed_release_digest,
                )
            except FileNotFoundError:
                continue
            composition = consumer_release.project_release.composition_lock
            immutable_lock = next(
                (
                    item
                    for item in (
                        composition.project_dependencies
                        if composition is not None
                        else ()
                    )
                    if item.project_ref == provider_ref
                ),
                None,
            )
            local_lock = next(
                (
                    item
                    for item in installation.shared_project_bindings
                    if item["provider_project_ref"] == provider_ref
                ),
                None,
            )
            observed_project_digest = str(
                (local_lock or {}).get("project_release_digest")
                or (immutable_lock.release_digest if immutable_lock else "")
            )
            version_spec = str(
                (local_lock or {}).get("version_spec")
                or (immutable_lock.version_spec if immutable_lock else "")
            )
            resolved = {
                item.key: item
                for item in consumer_release.project_release.resolved_dependencies
            }
            for current in installation.component_refs:
                component_ref = str(current["component_ref"])
                requested = target_by_ref.get(component_ref)
                dependency = resolved.get(component_ref)
                current_package = current_packages.get(component_ref)
                target_package = target_packages.get(component_ref)
                if (
                    not requested
                    or requested == current["package_digest"]
                    or current["lifecycle"] != "shared"
                ):
                    continue
                project_compatible = False
                if (
                    immutable_lock is not None
                    and observed_project_digest == current_project_digest
                ):
                    try:
                        normalized = normalize_version_spec(version_spec)
                        project_compatible = not normalized or Version(
                            target_project.version
                        ) in SpecifierSet(normalized)
                    except Exception:
                        project_compatible = False
                if project_compatible and (
                    local_lock is not None
                    or (
                        dependency is not None
                        and dependency.package_digest == current["package_digest"]
                    )
                ):
                    result.append(
                        {
                            "consumer_application_id": installation.application_id,
                            "consumer_installation_id": installation.installation_id,
                            "expected_consumer_revision": installation.revision,
                            "component_ref": component_ref,
                            "from_package_digest": current["package_digest"],
                            "to_package_digest": requested,
                            "provider_project_ref": provider_ref,
                            "from_project_release_digest": current_project_digest,
                            "to_project_release_digest": target_project_digest,
                            "admitted_by_version_spec": version_spec,
                        }
                    )
                    continue

                # A component can be consumed directly without a Project-level
                # dependency (for example the desktop's shared voice runtime).
                # The provider relationship is proven by the active provider
                # package digest; the consumer's immutable dependency range is
                # the authority for advancing its local materialization.
                if (
                    immutable_lock is not None
                    or dependency is None
                    or current_package is None
                    or target_package is None
                    or current_package.digest != current["package_digest"]
                    or target_package.digest != requested
                ):
                    continue
                try:
                    direct_spec = normalize_version_spec(dependency.version_spec)
                    direct_compatible = not direct_spec or Version(
                        target_package.version
                    ) in SpecifierSet(direct_spec)
                except Exception:
                    direct_compatible = False
                if direct_compatible:
                    result.append(
                        {
                            "consumer_application_id": installation.application_id,
                            "consumer_installation_id": installation.installation_id,
                            "expected_consumer_revision": installation.revision,
                            "component_ref": component_ref,
                            "from_package_digest": current["package_digest"],
                            "to_package_digest": requested,
                            "binding_kind": "direct_component",
                            "from_component_version": current_package.version,
                            "to_component_version": target_package.version,
                            "admitted_by_version_spec": dependency.version_spec,
                        }
                    )
                    continue

                # Native CBS identity is independent of package topology and
                # package-version ranges.  When an exact direct dependency is
                # shared by another Application, admit a provider upgrade only
                # after recomputing exact contract + BindingDelivery equality
                # from verified immutable package bytes on this node.  The
                # target package may not have been materialized yet, so use the
                # deployment runtime's authenticated package transport without
                # changing activation authority.
                try:
                    ensure = getattr(self.executor, "ensure_verified_package", None)
                    if callable(ensure):
                        ensure(target_package)
                    package_store = ContentAddressedPackageStore(
                        self.store.state_dir / "artifact_pipeline" / "packages"
                    )
                    active_fingerprint = shared_skill_contract_fingerprint(
                        current_package, package_store
                    )
                    target_fingerprint = shared_skill_contract_fingerprint(
                        target_package, package_store
                    )
                except Exception:
                    continue
                if active_fingerprint != target_fingerprint:
                    continue
                evidence = {
                    "schema": "adaos.application.shared_cbs_rebinding_evidence.v1",
                    "component_ref": component_ref,
                    "from_package_digest": current["package_digest"],
                    "to_package_digest": requested,
                    "contract_fingerprint": active_fingerprint,
                    "admitted_by": (
                        "exact_capability_binding_and_entrypoint_equivalence"
                    ),
                }
                result.append(
                    {
                        "consumer_application_id": installation.application_id,
                        "consumer_installation_id": installation.installation_id,
                        "expected_consumer_revision": installation.revision,
                        "component_ref": component_ref,
                        "from_package_digest": current["package_digest"],
                        "to_package_digest": requested,
                        "binding_kind": "native_cbs_contract",
                        "from_component_version": current_package.version,
                        "to_component_version": target_package.version,
                        "admitted_by": evidence["admitted_by"],
                        "contract_fingerprint": active_fingerprint,
                        "evidence_digest": canonical_payload_digest(evidence),
                    }
                )
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    item["consumer_application_id"],
                    item["component_ref"],
                ),
            )
        )

    def _prepare_shared_dependency_rebindings(
        self, raw_rebindings: Any
    ) -> tuple[tuple[ApplicationInstallation, ...], dict[str, int]]:
        rows = tuple(raw_rebindings or ())
        by_application: dict[str, list[Mapping[str, Any]]] = {}
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ApplicationServiceError(
                    "shared dependency rebinding plan is invalid"
                )
            application_id = str(raw.get("consumer_application_id") or "")
            by_application.setdefault(application_id, []).append(raw)
        updates: list[ApplicationInstallation] = []
        expected_revisions: dict[str, int] = {}
        for application_id, changes in sorted(by_application.items()):
            if not application_id:
                raise ApplicationServiceError(
                    "shared dependency rebinding consumer is required"
                )
            current = self.store.get_installation(application_id)
            expected = int(changes[0].get("expected_consumer_revision") or 0)
            if current.revision != expected or any(
                int(item.get("expected_consumer_revision") or 0) != expected
                for item in changes
            ):
                raise ApplicationServiceError(
                    "shared dependency consumer revision changed after review"
                )
            transitions = {
                str(item.get("component_ref") or ""): (
                    str(item.get("from_package_digest") or ""),
                    str(item.get("to_package_digest") or ""),
                )
                for item in changes
            }
            observed_refs: set[str] = set()
            component_refs: list[dict[str, Any]] = []
            for component in current.component_refs:
                component_ref = str(component["component_ref"])
                transition = transitions.get(component_ref)
                if transition is None:
                    component_refs.append(dict(component))
                    continue
                before, after = transition
                if (
                    component["lifecycle"] != "shared"
                    or component["package_digest"] != before
                    or not after
                ):
                    raise ApplicationServiceError(
                        "shared dependency binding changed after review"
                    )
                observed_refs.add(component_ref)
                component_refs.append(
                    {
                        **dict(component),
                        "package_digest": after,
                    }
                )
            if observed_refs != set(transitions):
                raise ApplicationServiceError(
                    "shared dependency binding is absent after review"
                )
            project_change = next(
                (
                    item
                    for item in changes
                    if str(item.get("provider_project_ref") or "")
                    and str(item.get("to_project_release_digest") or "")
                ),
                None,
            )
            project_bindings = current.shared_project_bindings
            if project_change is not None:
                project_bindings = tuple(
                    {
                        **dict(binding),
                        "project_release_digest": (
                            str(project_change["to_project_release_digest"])
                            if binding["provider_project_ref"]
                            == project_change["provider_project_ref"]
                            else binding["project_release_digest"]
                        ),
                    }
                    for binding in (
                        current.shared_project_bindings
                        or self._release_shared_project_bindings(
                            self.store.get_release(
                                current.application_id,
                                current.installed_release_digest,
                            )
                        )
                    )
                )
            updates.append(
                replace(
                    current,
                    component_refs=tuple(component_refs),
                    shared_project_bindings=project_bindings,
                    revision=current.revision + 1,
                    updated_at=utc_now(),
                )
            )
            expected_revisions[application_id] = current.revision
        return tuple(updates), expected_revisions

    @staticmethod
    def _compatibility_summary(release: ApplicationRelease) -> dict[str, Any]:
        composition = release.project_release.composition_lock
        compatibility = (
            dict(composition.compatibility) if composition is not None else {}
        )
        return {
            "platform": compatibility,
            "permissions": list(release.project_release.permissions),
            "migration": {
                "required": bool(release.project_release.migrations),
                "count": len(release.project_release.migrations),
                "rollback_mode": "snapshot_restore",
            },
            "contract_locks_present": release.project_release.contract_locks_present,
            "validation_evidence_count": len(
                release.project_release.validation_evidence
            ),
        }

    @staticmethod
    def _permission_review(
        release: ApplicationRelease,
        *,
        previous_release: ApplicationRelease | None = None,
    ) -> dict[str, Any]:
        profile = release.permission_profile
        items = [
            {**item.to_dict(), "requirement": requirement}
            for requirement, declarations in (
                ("required", profile.required),
                ("optional", profile.optional),
            )
            for item in declarations
        ]
        diff = None
        approval_permissions = list(profile.flat_permissions)
        if previous_release is not None:
            diff = classify_access_profile_diff(
                previous_release.permission_profile,
                profile,
                old_roles=previous_release.application_roles,
                new_roles=release.application_roles,
            )
            changes = diff["permission_changes"]
            approval_permissions = sorted(
                {
                    *changes.get("added", ()),
                    *changes.get("elevated", ()),
                }
            )
        return {
            "schema": "adaos.application.permission_review.v1",
            "profile_digest": profile.digest,
            "items": items,
            "required": [item.permission_id for item in profile.required],
            "optional": [item.permission_id for item in profile.optional],
            "approval_required": bool(approval_permissions),
            "approval_permissions": approval_permissions,
            "diff": diff,
        }

    def plan_operation(
        self,
        application_id: str,
        kind: str,
        *,
        actor_ref: str,
        subnet_ref: str,
        capability: str,
        idempotency_key: str,
        expected_revision: int,
        release_digest: str | None = None,
        data_policy: str = "retain",
        update_track: str | None = None,
        update_policy: str = "auto_compatible",
        paused: bool = False,
        pinned_release_digest: str | None = None,
        access_redemption_id: str | None = None,
        component_ref: str | None = None,
        target_node_id: str | None = None,
        webspace_id: str = "desktop",
    ) -> ApplicationOperation:
        authority = _require_authority(
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability=capability,
            required_capability="applications.plan",
        )
        operation_kind = str(kind or "").strip().lower()
        if operation_kind not in {
            "install",
            "update",
            "remove",
            "select_track",
            "relocate_component",
            "install_component",
            "remove_component",
        }:
            raise ApplicationServiceError(
                "Core operation kind must be install, update, remove, select_track, "
                "relocate_component, install_component, or remove_component"
            )
        if data_policy not in {"retain", "delete", "snapshot_then_delete"}:
            raise ApplicationServiceError("data_policy is invalid")
        application = self.store.get_application(application_id)
        self._assert_management_preconditions(application, operation_kind)
        try:
            current_subscription = self.store.get_subscription(application_id)
        except FileNotFoundError:
            current_subscription = None
        deployment_snapshot: dict[str, Any] | None = None
        installation_revision: int | None = None
        if operation_kind == "select_track":
            current = None
            observed_revision = (
                current_subscription.revision if current_subscription is not None else 0
            )
        elif operation_kind in {
            "relocate_component",
            "install_component",
            "remove_component",
        }:
            try:
                current = self.store.get_installation(application_id)
            except FileNotFoundError:
                current = None
            if current is None or current.status != "active":
                raise ApplicationServiceError(
                    "Application must be installed before changing component placement"
                )
            installation_revision = current.revision
            snapshot_provider = getattr(self.executor, "deployment_snapshot", None)
            if not callable(snapshot_provider):
                raise ApplicationServiceError(
                    "Application placement planning is not configured"
                )
            raw_snapshot = snapshot_provider(application_id)
            if not isinstance(raw_snapshot, Mapping):
                raise ApplicationServiceError("Application deployment is missing")
            deployment_snapshot = dict(raw_snapshot)
            observed_revision = int(deployment_snapshot.get("revision") or 0)
        else:
            try:
                current = self.store.get_installation(application_id)
            except FileNotFoundError:
                current = None
            observed_revision = current.revision if current is not None else 0
        if observed_revision != expected_revision:
            from .store import ApplicationRevisionConflict

            raise ApplicationRevisionConflict(
                expected=expected_revision, observed=observed_revision
            )
        if (
            operation_kind == "install"
            and current is not None
            and current.status != "removed"
        ):
            raise ApplicationServiceError("Application is already installed")
        if operation_kind in {"update", "remove"} and (
            current is None or current.status == "removed"
        ):
            raise ApplicationServiceError(
                f"Application must be installed before {operation_kind}"
            )
        placement_change = None
        if operation_kind in {
            "relocate_component",
            "install_component",
            "remove_component",
        }:
            selected_component = str(component_ref or "").strip()
            if not selected_component:
                raise ApplicationServiceError("component_ref is required")
            placements = [
                dict(item)
                for item in (deployment_snapshot or {}).get("placements") or ()
                if isinstance(item, Mapping)
            ]
            current_placement = next(
                (
                    item
                    for item in placements
                    if str(item.get("component_ref") or "") == selected_component
                ),
                None,
            )
            if current_placement is None:
                raise ApplicationServiceError(
                    "component_ref is absent from the current Application deployment"
                )
            placement_disabled = str(current_placement.get("mode") or "") == "disabled"
            if operation_kind == "install_component" and not placement_disabled:
                raise ApplicationServiceError(
                    "component_ref is already installed in the Application deployment"
                )
            if operation_kind != "install_component" and placement_disabled:
                raise ApplicationServiceError(
                    "component_ref is already uninstalled from the Application deployment"
                )
            active_placements = [
                item for item in placements if str(item.get("mode") or "") != "disabled"
            ]
            if operation_kind == "remove_component" and len(active_placements) == 1:
                raise ApplicationServiceError(
                    "The final Application component requires full Application removal"
                )
            selected_target = str(target_node_id or "").strip()
            if (
                operation_kind in {"relocate_component", "install_component"}
                and not selected_target
            ):
                raise ApplicationServiceError(
                    "target_node_id is required for component placement"
                )
            component_descriptor = None
            if operation_kind == "install_component":
                release_digest_for_deployment = str(
                    (deployment_snapshot or {}).get("release_digest") or ""
                )
                release_for_deployment = self.store.get_release(
                    application_id,
                    release_digest_for_deployment,
                )
                component_descriptor = next(
                    (
                        item
                        for item in self._release_components(release_for_deployment)
                        if item["component_ref"] == selected_component
                    ),
                    None,
                )
                if component_descriptor is None:
                    raise ApplicationServiceError(
                        "component_ref is absent from the installed Application release"
                    )
            placement_change = {
                "component_ref": selected_component,
                "from": current_placement,
                "target_node_id": (
                    selected_target
                    if operation_kind in {"relocate_component", "install_component"}
                    else None
                ),
                "effect": (
                    "relocate"
                    if operation_kind == "relocate_component"
                    else "install"
                    if operation_kind == "install_component"
                    else "uninstall"
                ),
                "component": component_descriptor,
            }
        if operation_kind == "remove" and not bool(
            application.protection.get("active_installation_removable", True)
        ):
            raise ApplicationServiceError(
                "active protected system Application cannot remove itself; use a declared recovery surface"
            )
        release: ApplicationRelease | None = None
        components: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        shared_dependency_rebindings: tuple[dict[str, Any], ...] = ()
        compatibility: dict[str, Any] = {}
        previous_release: ApplicationRelease | None = None
        if operation_kind in {"install", "update"}:
            if not release_digest:
                effective = self.effective_release(
                    application_id,
                    subscriber_subnet_ref=subnet_ref,
                )
                release_digest = str(effective.get("release_digest") or "")
            if not release_digest:
                raise ApplicationServiceError(
                    "no exact release is available for operation"
                )
            release = self.store.get_release(application_id, release_digest)
            channels = self.store.get_channels(application_id).get("channels") or {}
            public_release = application.visibility == "public" and (
                channels.get("stable") == release_digest
                or (
                    channels.get("stable") is not None
                    and channels.get("prerelease") == release_digest
                )
            )
            publisher_local = subnet_ref == application.publisher_ref
            access_receipt = None
            if not publisher_local and not public_release:
                if not access_redemption_id:
                    raise ApplicationServiceError(
                        "release installation requires a Trial access redemption"
                    )
                access_receipt = self.store.get_trial_redemption(access_redemption_id)
                if (
                    access_receipt.get("application_id") != application_id
                    or access_receipt.get("release_digest") != release_digest
                    or access_receipt.get("recipient_subnet_ref") != subnet_ref
                ):
                    raise ApplicationServiceError(
                        "Trial access redemption does not authorize this installation"
                    )
            components = self._shared_component_materialization(
                application_id,
                self._release_components(release),
                allow_reuse=operation_kind == "install",
            )
            if operation_kind == "update":
                shared_dependency_rebindings = self._shared_dependency_rebindings(
                    application_id, release, components
                )
            conflicts = self._component_conflicts(
                application_id,
                components,
                admitted_rebindings=shared_dependency_rebindings,
            )
            compatibility = self._compatibility_summary(release)
            if operation_kind == "update" and current is not None:
                previous_release = self.store.get_release(
                    application_id, current.installed_release_digest
                )
        removal = (
            self.simulate_removal(application_id, data_policy=data_policy)
            if operation_kind == "remove"
            else None
        )
        subscription_change = None
        if operation_kind == "select_track":
            subscription_change = ApplicationSubscription(
                application_id=application_id,
                update_track=str(update_track or "stable"),  # type: ignore[arg-type]
                update_policy=update_policy,  # type: ignore[arg-type]
                observed_release_digest=(
                    current_subscription.observed_release_digest
                    if current_subscription is not None
                    else None
                ),
                pinned_release_digest=pinned_release_digest,
                paused=paused,
                revision=expected_revision + 1,
            ).to_dict()
        subscription_default = None
        if (
            operation_kind == "install"
            and current_subscription is None
            and application.kind == "application"
        ):
            subscription_default = {
                "update_track": "stable",
                "update_policy": "auto_compatible",
                "observed_release_digest": release_digest,
                "paused": False,
            }
        snapshot = {
            "required": operation_kind == "update",
            "mode": "snapshot_restore" if operation_kind == "update" else "none",
            "consistency_boundary": "artifact_activation_transaction"
            if operation_kind == "update"
            else None,
            "source_release_digest": current.installed_release_digest
            if current is not None and operation_kind == "update"
            else None,
            "retention": "until_successor_release_verified"
            if operation_kind == "update"
            else None,
        }
        runtime_target = None
        if operation_kind in {"install", "update"}:
            selected_webspace = str(webspace_id or "").strip()
            if not selected_webspace:
                raise ApplicationServiceError("webspace_id is required")
            try:
                current_runtime = self.store.get_runtime_selection(
                    selected_webspace, application_id
                )
            except FileNotFoundError:
                current_runtime = None
            runtime_target = {
                "webspace_id": selected_webspace,
                "source": "stable_installation",
                "release_digest": release_digest,
                "runtime_root_ref": "workspace",
                "expected_revision": (
                    current_runtime.revision if current_runtime is not None else 0
                ),
                "previous_source": (
                    current_runtime.source if current_runtime is not None else None
                ),
                "previous_release_digest": (
                    current_runtime.release_digest
                    if current_runtime is not None
                    else None
                ),
            }
        plan = {
            "schema": "adaos.application.operation_plan.v1",
            "application_id": application_id,
            "legacy_project_id": application.legacy_project_id,
            "application_kind": application.kind,
            "owner_application_id": application.owner_application_id,
            "actor_ref": actor_ref,
            "subnet_ref": subnet_ref,
            "authority": authority,
            "idempotency_key": idempotency_key,
            "kind": operation_kind,
            "expected_revision": expected_revision,
            "release_digest": release_digest,
            "review_summary": (
                f"Review {operation_kind} for Application {application_id}."
            ),
            "permissions": (
                list(release.project_release.permissions) if release is not None else []
            ),
            "permission_review": (
                self._permission_review(
                    release,
                    previous_release=previous_release,
                )
                if release is not None
                else None
            ),
            "components": components,
            "conflicts": conflicts,
            "shared_dependency_rebindings": [
                dict(item) for item in shared_dependency_rebindings
            ],
            "compatibility": compatibility,
            "snapshot": snapshot,
            "removal": removal,
            "data_policy": data_policy,
            "subscription_change": subscription_change,
            "subscription_default": subscription_default,
            "runtime_target": runtime_target,
            "placement_change": placement_change,
            "installation_revision": installation_revision,
            "access": {
                "mode": (
                    "publisher_local"
                    if publisher_local
                    else "public_channel"
                    if public_release
                    else "trial_redemption"
                ),
                "redemption_id": access_redemption_id,
            }
            if operation_kind in {"install", "update"}
            else None,
        }
        plan_digest = canonical_payload_digest(plan)
        identity = hashlib.sha256(
            f"{plan_digest}:{idempotency_key}".encode("utf-8")
        ).hexdigest()[:32]
        operation = ApplicationOperation(
            operation_id=f"appop.{identity}",
            application_id=application_id,
            kind=operation_kind,
            status="planned",
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            plan_digest=plan_digest,
            idempotency_key=idempotency_key,
            expected_revision=expected_revision,
            revision=1,
            plan=plan,
        )
        stored = self.store.put_operation(operation)
        self._publish_operation(stored)
        return stored

    def _transition_operation(
        self,
        operation: ApplicationOperation,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        recovery_reason: str | None = None,
    ) -> ApplicationOperation:
        updated = replace(
            operation,
            status=status,
            result=dict(result or operation.result),
            recovery_reason=recovery_reason,
            revision=operation.revision + 1,
            updated_at=utc_now(),
        )
        saved = self.store.save_operation(updated, expected_revision=operation.revision)
        self._publish_operation(saved)
        return saved

    def _executor_materialized_exact_installation(
        self,
        operation: ApplicationOperation,
        installation: ApplicationInstallation | None,
        *,
        snapshot_ref: str | None,
    ) -> bool:
        """Recognize the exact installation committed by the deployment executor.

        The project deployment executor owns the atomic workspace activation and
        may reconcile the ApplicationInstallation before returning.  That is not
        a concurrent writer when the resulting revision and complete reviewed
        closure are exactly the ones this operation requested.
        """
        if operation.kind not in {"install", "update"} or installation is None:
            return False
        if operation.plan.get("shared_dependency_rebindings"):
            # Rebinding other installations is committed by this service as one
            # compare-and-swap batch and cannot be inferred from the provider's
            # installation alone.
            return False
        if installation.revision != operation.expected_revision + 1:
            return False
        if installation.status != "active":
            return False
        if installation.installed_release_digest != str(
            operation.plan.get("release_digest") or ""
        ):
            return False

        def component_identity(raw: Mapping[str, Any]) -> tuple[str, str, str]:
            return (
                str(raw.get("component_ref") or ""),
                str(raw.get("package_digest") or ""),
                str(raw.get("lifecycle") or ""),
            )

        reviewed_components = tuple(
            sorted(
                (
                    component_identity(item)
                    for item in operation.plan.get("components") or ()
                    if isinstance(item, Mapping)
                ),
                key=lambda item: item[0],
            )
        )
        materialized_components = tuple(
            component_identity(item) for item in installation.component_refs
        )
        if materialized_components != reviewed_components:
            return False
        release = self.store.get_release(
            operation.application_id,
            str(operation.plan.get("release_digest") or ""),
        )
        if (
            installation.shared_project_bindings
            != self._release_shared_project_bindings(release)
        ):
            return False
        if installation.data_policy != str(
            operation.plan.get("data_policy") or "retain"
        ):
            return False
        return installation.snapshot_ref == snapshot_ref

    def apply_operation(
        self,
        operation_id: str,
        *,
        plan_digest: str,
        idempotency_key: str,
        actor_ref: str,
        subnet_ref: str,
        capability: str,
    ) -> ApplicationOperation:
        """Serialize authority changes for one Application aggregate.

        Registry auto-update, an operator-reviewed batch, and recovery can all
        observe the same revision.  Only one may execute its expensive runtime
        transition; followers revalidate after the winner releases the lease.
        """

        operation = self.store.get_operation(operation_id)
        token = hashlib.sha256(operation.application_id.encode("utf-8")).hexdigest()
        lease = self.store.root / "operation-leases" / f"{token}.lock"
        with mutation_lock(lease, timeout_s=900.0):
            return self._apply_operation_locked(
                operation_id,
                plan_digest=plan_digest,
                idempotency_key=idempotency_key,
                actor_ref=actor_ref,
                subnet_ref=subnet_ref,
                capability=capability,
            )

    def _apply_operation_locked(
        self,
        operation_id: str,
        *,
        plan_digest: str,
        idempotency_key: str,
        actor_ref: str,
        subnet_ref: str,
        capability: str,
    ) -> ApplicationOperation:
        operation = self.store.get_operation(operation_id)
        _require_authority(
            actor_ref=actor_ref,
            subnet_ref=subnet_ref,
            capability=capability,
            required_capability="applications.apply",
        )
        if operation.actor_ref != actor_ref or operation.subnet_ref != subnet_ref:
            raise ApplicationServiceError(
                "operation authority does not match reviewed plan"
            )
        # Older Applications releases projected ``operation_id`` as the
        # reviewed idempotency identity.  The operation id is derived from the
        # exact plan digest and original idempotency key, and authority was
        # checked above, so accepting that stable operation identity is just
        # as fail-closed as accepting the original key.  New consumers should
        # send ``operation.idempotency_key`` directly.
        idempotency_identity_matches = idempotency_key in {
            operation.idempotency_key,
            operation.operation_id,
        }
        if operation.plan_digest != plan_digest or not idempotency_identity_matches:
            raise ApplicationServiceError(
                "reviewed plan or idempotency identity does not match"
            )
        if operation.status == "succeeded":
            return operation
        if operation.status != "planned":
            raise ApplicationServiceError(
                f"operation cannot apply from {operation.status}"
            )
        conflicts = list(operation.plan.get("conflicts") or [])
        if conflicts:
            raise ApplicationPlanConflict(conflicts)
        if operation.kind in {"install", "update", "remove"}:
            try:
                current_installation = self.store.get_installation(
                    operation.application_id
                )
            except FileNotFoundError:
                current_installation = None
            current_revision = (
                current_installation.revision
                if current_installation is not None
                else 0
            )
            if current_revision != operation.expected_revision:
                from .store import ApplicationRevisionConflict

                raise ApplicationRevisionConflict(
                    expected=operation.expected_revision,
                    observed=current_revision,
                )
        application = self.store.get_application(operation.application_id)
        self._assert_management_preconditions(application, operation.kind)
        if operation.kind == "select_track":
            raw_subscription = operation.plan.get("subscription_change")
            if not isinstance(raw_subscription, Mapping):
                raise ApplicationServiceError(
                    "select_track plan is missing subscription state"
                )
            subscription = ApplicationSubscription.from_mapping(raw_subscription)
            self.store.save_subscription(
                subscription, expected_revision=operation.expected_revision
            )
            return self._transition_operation(
                operation,
                "succeeded",
                result={"subscription": subscription.to_dict()},
            )
        if self.executor is None:
            raise ApplicationServiceError(
                "Application operation executor is not configured"
            )
        install_access = None
        if operation.kind in {"install", "update"}:
            permission_review = operation.plan.get("permission_review")
            reviewed_permissions = (
                tuple(permission_review.get("approval_permissions") or ())
                if isinstance(permission_review, Mapping)
                else ()
            )
            install_access = self.ensure_install_access(
                operation.application_id,
                release_digest=str(operation.plan.get("release_digest") or ""),
                subnet_ref=operation.subnet_ref,
                issuer_ref=operation.actor_ref,
                reviewed_permissions=reviewed_permissions,
            )
        applying = self._transition_operation(operation, "applying")
        try:
            result = dict(self.executor(operation.plan))
        except Exception as exc:
            self._transition_operation(
                applying,
                "unknown",
                recovery_reason=f"executor_outcome_unknown:{type(exc).__name__}:{exc}",
            )
            raise
        snapshot_ref = None
        snapshot_plan = operation.plan.get("snapshot")
        if isinstance(snapshot_plan, Mapping) and bool(snapshot_plan.get("required")):
            receipt = result.get("snapshot_receipt")
            expected_source = str(snapshot_plan.get("source_release_digest") or "")
            if (
                not isinstance(receipt, Mapping)
                or not str(receipt.get("snapshot_ref") or "").strip()
                or str(receipt.get("source_release_digest") or "") != expected_source
                or str(receipt.get("consistency_boundary") or "")
                != str(snapshot_plan.get("consistency_boundary") or "")
            ):
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="required_snapshot_receipt_invalid",
                )
            snapshot_ref = str(receipt["snapshot_ref"])
            self.store.put_snapshot_receipt(snapshot_ref, receipt)
        if not bool(result.get("ok")) or str(result.get("status") or "") not in {
            "succeeded",
            "active",
            "removed",
        }:
            if snapshot_ref is not None:
                restore = result.get("restore_receipt")
                if (
                    not isinstance(restore, Mapping)
                    or str(restore.get("snapshot_ref") or "") != snapshot_ref
                    or str(restore.get("restored_release_digest") or "")
                    != str((snapshot_plan or {}).get("source_release_digest") or "")
                    or str(restore.get("status") or "") != "restored"
                ):
                    return self._transition_operation(
                        applying,
                        "unknown",
                        result=result,
                        recovery_reason="snapshot_restore_outcome_unknown",
                    )
                self.store.put_snapshot_receipt(
                    f"restore:{snapshot_ref}",
                    restore,
                )
            return self._transition_operation(
                applying,
                "failed",
                result=result,
                recovery_reason=str(result.get("reason") or "executor_rejected"),
            )
        if operation.kind == "install_component":
            try:
                current_installation = self.store.get_installation(
                    operation.application_id
                )
            except FileNotFoundError:
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="installation_missing_after_component_install",
                )
            reviewed_installation_revision = int(
                operation.plan.get("installation_revision") or 0
            )
            if current_installation.revision != reviewed_installation_revision:
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="installation_revision_changed_after_component_install",
                )
            change = operation.plan.get("placement_change")
            component = change.get("component") if isinstance(change, Mapping) else None
            component_ref = (
                str(component.get("component_ref") or "").strip()
                if isinstance(component, Mapping)
                else ""
            )
            if not component_ref or any(
                str(item.get("component_ref") or "") == component_ref
                for item in current_installation.component_refs
            ):
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="installed_component_invalid_or_already_present",
                )
            installation = replace(
                current_installation,
                component_refs=(*current_installation.component_refs, dict(component)),
                revision=current_installation.revision + 1,
                updated_at=utc_now(),
            )
            self.store.save_installation(
                installation,
                expected_revision=current_installation.revision,
            )
            return self._transition_operation(
                applying,
                "succeeded",
                result={**result, "installation": installation.to_dict()},
            )
        if operation.kind == "relocate_component":
            return self._transition_operation(
                applying,
                "succeeded",
                result=result,
            )
        if operation.kind == "remove_component":
            try:
                current_installation = self.store.get_installation(
                    operation.application_id
                )
            except FileNotFoundError:
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="installation_missing_after_component_removal",
                )
            reviewed_installation_revision = int(
                operation.plan.get("installation_revision") or 0
            )
            if current_installation.revision != reviewed_installation_revision:
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="installation_revision_changed_after_component_removal",
                )
            change = operation.plan.get("placement_change")
            component_ref = (
                str(change.get("component_ref") or "").strip()
                if isinstance(change, Mapping)
                else ""
            )
            remaining_components = tuple(
                item
                for item in current_installation.component_refs
                if str(item.get("component_ref") or "") != component_ref
            )
            if not component_ref or len(remaining_components) >= len(
                current_installation.component_refs
            ):
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="removed_component_absent_from_installation",
                )
            installation = replace(
                current_installation,
                component_refs=remaining_components,
                revision=current_installation.revision + 1,
                updated_at=utc_now(),
            )
            self.store.save_installation(
                installation,
                expected_revision=current_installation.revision,
            )
            return self._transition_operation(
                applying,
                "succeeded",
                result={**result, "installation": installation.to_dict()},
            )
        current: ApplicationInstallation | None
        try:
            current = self.store.get_installation(operation.application_id)
        except FileNotFoundError:
            current = None
        observed_revision = current.revision if current is not None else 0
        executor_materialized_installation = False
        if observed_revision != operation.expected_revision:
            executor_materialized_installation = (
                self._executor_materialized_exact_installation(
                    operation,
                    current,
                    snapshot_ref=snapshot_ref,
                )
            )
            if not executor_materialized_installation:
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason="installation_revision_changed_after_execution",
                )
        if operation.kind in {"install", "update"}:
            if executor_materialized_installation:
                assert current is not None
                installation = current
            else:
                installation = ApplicationInstallation(
                    installation_id=current.installation_id
                    if current
                    else f"installation:{operation.application_id}",
                    application_id=operation.application_id,
                    installed_release_digest=str(operation.plan["release_digest"]),
                    component_refs=tuple(operation.plan.get("components") or ()),
                    shared_project_bindings=self._release_shared_project_bindings(
                        self.store.get_release(
                            operation.application_id,
                            str(operation.plan["release_digest"]),
                        )
                    ),
                    data_policy=str(operation.plan.get("data_policy") or "retain"),
                    status="active",
                    revision=observed_revision + 1,
                    legacy_deployment_id=current.legacy_deployment_id
                    if current
                    else None,
                    snapshot_ref=snapshot_ref,
                    created_at=current.created_at if current else utc_now(),
                    updated_at=utc_now(),
                )
        else:
            assert current is not None
            legacy_selections = tuple(
                item
                for item in self.store.list_runtime_selections()
                if item.application_id == operation.application_id
            )
            ApplicationRuntimeChannel(
                self.store.state_dir, operation.application_id
            ).retire(legacy=legacy_selections)
            installation = replace(
                current,
                status="removed",
                data_policy=str(
                    operation.plan.get("data_policy") or current.data_policy
                ),
                revision=current.revision + 1,
                updated_at=utc_now(),
            )
        shared_rebindings: tuple[ApplicationInstallation, ...] = ()
        shared_expected_revisions: dict[str, int] = {}
        if operation.kind == "update":
            try:
                (
                    shared_rebindings,
                    shared_expected_revisions,
                ) = self._prepare_shared_dependency_rebindings(
                    operation.plan.get("shared_dependency_rebindings")
                )
            except Exception as exc:
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=result,
                    recovery_reason=(
                        "shared_dependency_rebinding_changed_after_execution:"
                        f"{type(exc).__name__}:{exc}"
                    ),
                )
        if executor_materialized_installation:
            pass
        elif shared_rebindings:
            expected_revisions = {
                operation.application_id: observed_revision,
                **shared_expected_revisions,
            }
            self.store.save_installations_batch(
                (installation, *shared_rebindings),
                expected_revisions=expected_revisions,
            )
        else:
            self.store.save_installation(
                installation, expected_revision=observed_revision
            )
        subscription_result = None
        raw_subscription_default = operation.plan.get("subscription_default")
        if operation.kind == "install" and isinstance(
            raw_subscription_default, Mapping
        ):
            try:
                subscription_result = self.store.get_subscription(
                    operation.application_id
                )
            except FileNotFoundError:
                subscription_result = self.store.save_subscription(
                    ApplicationSubscription(
                        application_id=operation.application_id,
                        update_track=str(
                            raw_subscription_default.get("update_track") or "stable"
                        ),  # type: ignore[arg-type]
                        update_policy=str(
                            raw_subscription_default.get("update_policy")
                            or "auto_compatible"
                        ),  # type: ignore[arg-type]
                        observed_release_digest=str(
                            raw_subscription_default.get("observed_release_digest")
                            or ""
                        )
                        or None,
                        paused=bool(raw_subscription_default.get("paused", False)),
                        revision=1,
                    ),
                    expected_revision=0,
                )
        operation_result = {**result, "installation": installation.to_dict()}
        if shared_rebindings:
            operation_result["shared_dependency_rebindings"] = [
                item.to_dict() for item in shared_rebindings
            ]
        if install_access is not None:
            operation_result["install_access"] = install_access
        if subscription_result is not None:
            operation_result["subscription"] = subscription_result.to_dict()
        if operation.kind in {"install", "update"}:
            raw_runtime_target = operation.plan.get("runtime_target")
            if not isinstance(raw_runtime_target, Mapping):
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=operation_result,
                    recovery_reason="native_cbs_runtime_target_missing",
                )
            try:
                native_activation = self._activate_native_runtime(
                    application_id=operation.application_id,
                    release_digest=str(operation.plan.get("release_digest") or ""),
                    admission=(
                        result.get("cbs_admission")
                        if isinstance(result.get("cbs_admission"), Mapping)
                        else None
                    ),
                    runtime_target=raw_runtime_target,
                    actor_ref=actor_ref,
                    subnet_ref=subnet_ref,
                )
            except Exception as exc:
                return self._transition_operation(
                    applying,
                    "unknown",
                    result=operation_result,
                    recovery_reason=(
                        "native_cbs_runtime_activation_failed:"
                        f"{type(exc).__name__}:{exc}"
                    ),
                )
            operation_result["native_cbs_activation"] = native_activation
        return self._transition_operation(
            applying,
            "succeeded",
            result=operation_result,
        )

    def reconcile_operation(
        self,
        operation_id: str,
        *,
        observer: Callable[[ApplicationOperation], Mapping[str, Any]],
    ) -> ApplicationOperation:
        operation = self.store.get_operation(operation_id)
        if operation.status not in {"unknown", "applying", "reconciling"}:
            return operation
        reconciling = (
            operation
            if operation.status == "reconciling"
            else self._transition_operation(operation, "reconciling")
        )
        observed = dict(observer(reconciling))
        status = str(observed.get("status") or "unknown")
        if status not in {"succeeded", "failed", "unknown"}:
            status = "unknown"
        return self._transition_operation(
            reconciling,
            status,
            result=observed,
            recovery_reason=None
            if status != "unknown"
            else str(observed.get("reason") or "authoritative_state_inconclusive"),
        )

    def simulate_removal(
        self, application_id: str, *, data_policy: str = "retain"
    ) -> dict[str, Any]:
        installation = self.store.get_installation(application_id)
        index = self.component_references()["components"]
        components: list[dict[str, Any]] = []
        for component in installation.component_refs:
            others = [
                item
                for item in index.get(component["component_ref"], [])
                if item["application_id"] != application_id
            ]
            holds = [
                hold
                for item in index.get(component["component_ref"], [])
                for field_name in (
                    "active_runtime_leases",
                    "rollback_holds",
                    "uncertain_operation_refs",
                )
                for hold in item.get(field_name, [])
            ]
            components.append(
                {
                    "component_ref": component["component_ref"],
                    "package_digest": component["package_digest"],
                    "remove_package": not others and not holds,
                    "retained_by_applications": sorted(
                        {item["application_id"] for item in others}
                    ),
                    "retained_by_holds": sorted(set(holds)),
                }
            )
        return {
            "application_id": application_id,
            "installation_revision": installation.revision,
            "components": components,
            "data_outcome": data_policy,
        }

    def _read_model(
        self,
        application: Application,
        *,
        installation: ApplicationInstallation | None,
        subscription: ApplicationSubscription | None,
        runtime_selections: list[RuntimeSelection],
        operation: ApplicationOperation | None,
        subscriber_subnet_ref: str | None,
    ) -> dict[str, Any]:
        channels = dict(
            self.store.get_channels(application.application_id).get("channels") or {}
        )
        local_beta = any(item.source == "local_trial" for item in runtime_selections)
        local_beta_digests = {
            item.release_digest
            for item in runtime_selections
            if item.source == "local_trial"
        }
        effective_installed = installation is not None or local_beta
        prerelease_following = bool(
            subscription and subscription.update_track == "prerelease"
        )
        effective = self.effective_release(
            application.application_id,
            subscriber_subnet_ref=subscriber_subnet_ref,
        )
        update_available = bool(
            installation
            and effective.get("release_digest")
            and effective["release_digest"] != installation.installed_release_digest
        )

        def release_for(digest: str | None) -> dict[str, Any] | None:
            if not digest:
                return None
            try:
                return self.store.get_release(
                    application.application_id, digest
                ).to_dict()
            except FileNotFoundError:
                return None

        installed_release = release_for(
            installation.installed_release_digest if installation else None
        )
        local_beta_releases = [
            release
            for digest in sorted(local_beta_digests)
            if (release := release_for(digest)) is not None
        ]
        local_beta_release = (
            local_beta_releases[0] if len(local_beta_releases) == 1 else None
        )
        active_release = local_beta_release or installed_release

        return {
            "application": application.to_dict(),
            # An active local Trial exclusively supplies this Application in at
            # least one Webspace.  It is therefore installed from the user's
            # perspective even though it deliberately has no Stable
            # ApplicationInstallation record yet.
            "installed": effective_installed,
            "installation": installation.to_dict() if installation else None,
            "available": bool(channels.get("stable"))
            or application.visibility != "public",
            "update_available": update_available,
            "pinned": bool(subscription and subscription.update_policy == "pinned"),
            "prerelease_following": prerelease_following,
            "use_prerelease": local_beta or prerelease_following,
            "local_beta_active": local_beta,
            "runtime_selections": [item.to_dict() for item in runtime_selections],
            "auto_update_enabled": bool(
                subscription.update_policy == "auto_compatible"
                if subscription is not None
                else installation is not None
            ),
            "retired": application.lifecycle in {"retired", "archived"},
            "subscription": subscription.to_dict() if subscription else None,
            "channels": channels,
            # Stable installation and selected Trial are separate lifecycle
            # facts.  Consumers that render the effective runtime should use
            # active_release while migration/rollback logic keeps using
            # installed_release as its stable baseline.
            "installed_release": installed_release,
            "local_beta_release": local_beta_release,
            "local_beta_releases": local_beta_releases,
            "active_release": active_release,
            "marketplace_release": release_for(channels.get("stable")),
            "prerelease_release": release_for(channels.get("prerelease")),
            "effective_release": effective,
            "operation": operation.to_dict() if operation else None,
        }

    def _read_summary_model(
        self,
        application: Application,
        *,
        installation: ApplicationInstallation | None,
        subscription: ApplicationSubscription | None,
        runtime_selections: list[RuntimeSelection],
        operation: ApplicationOperation | None,
        subscriber_subnet_ref: str | None,
        channels: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build one bounded catalog row without expanding release closures."""

        channels = dict(channels)
        local_beta = any(item.source == "local_trial" for item in runtime_selections)
        local_beta_digests = {
            item.release_digest
            for item in runtime_selections
            if item.source == "local_trial"
        }
        effective_installed = installation is not None or local_beta
        prerelease_following = bool(
            subscription and subscription.update_track == "prerelease"
        )
        effective = self._effective_release_from_state(
            application.application_id,
            channels=channels,
            subscription=subscription,
            subscriber_subnet_ref=subscriber_subnet_ref,
            include_release=False,
        )
        update_available = bool(
            installation
            and effective.get("release_digest")
            and effective["release_digest"] != installation.installed_release_digest
        )
        release_cache: dict[str, dict[str, Any] | None] = {}

        def release_for(digest: str | None) -> dict[str, Any] | None:
            token = str(digest or "").strip()
            if not token:
                return None
            if token not in release_cache:
                try:
                    release_cache[token] = self.store.get_release_summary(
                        application.application_id, token
                    )
                except FileNotFoundError:
                    release_cache[token] = None
            return release_cache[token]

        installed_release = release_for(
            installation.installed_release_digest if installation else None
        )
        local_beta_releases = [
            release
            for digest in sorted(local_beta_digests)
            if (release := release_for(digest)) is not None
        ]
        local_beta_release = (
            local_beta_releases[0] if len(local_beta_releases) == 1 else None
        )
        installation_summary = None
        if installation is not None:
            value = installation.to_dict()
            installation_summary = {
                key: value[key]
                for key in (
                    "schema",
                    "installation_id",
                    "application_id",
                    "installed_release_digest",
                    "data_policy",
                    "status",
                    "revision",
                    "created_at",
                    "updated_at",
                )
                if key in value
            }
        operation_summary = None
        if operation is not None:
            value = operation.to_dict()
            operation_summary = {
                key: value[key]
                for key in (
                    "schema",
                    "operation_id",
                    "application_id",
                    "kind",
                    "status",
                    "revision",
                    "recovery_reason",
                    "created_at",
                    "updated_at",
                )
                if key in value
            }
        return {
            "schema": "adaos.application.catalog_summary.v1",
            "application": application.to_dict(),
            "installed": effective_installed,
            "installation": installation_summary,
            "available": bool(channels.get("stable"))
            or application.visibility != "public",
            "update_available": update_available,
            "pinned": bool(subscription and subscription.update_policy == "pinned"),
            "prerelease_following": prerelease_following,
            "use_prerelease": local_beta or prerelease_following,
            "local_beta_active": local_beta,
            "runtime_selections": [item.to_dict() for item in runtime_selections],
            "auto_update_enabled": bool(
                subscription.update_policy == "auto_compatible"
                if subscription is not None
                else installation is not None
            ),
            "retired": application.lifecycle in {"retired", "archived"},
            "subscription": subscription.to_dict() if subscription else None,
            "channels": channels,
            "installed_release": installed_release,
            "local_beta_release": local_beta_release,
            "local_beta_releases": local_beta_releases,
            "active_release": local_beta_release or installed_release,
            "marketplace_release": release_for(channels.get("stable")),
            "prerelease_release": release_for(channels.get("prerelease")),
            "effective_release": effective,
            "operation": operation_summary,
        }

    def get_model(
        self,
        application_id: str,
        *,
        subscriber_subnet_ref: str | None = None,
    ) -> dict[str, Any]:
        application = self.store.get_application(application_id)
        try:
            installation = self.store.get_installation(application_id)
            if installation.status == "removed":
                installation = None
        except FileNotFoundError:
            installation = None
        try:
            subscription = self.store.get_subscription(application_id)
        except FileNotFoundError:
            subscription = None
        runtime_selections = [
            item
            for item in self.store.list_runtime_selections()
            if item.application_id == application_id
        ]
        operations = self.store.list_operations(application_id)
        model = self._read_model(
            application,
            installation=installation,
            subscription=subscription,
            runtime_selections=runtime_selections,
            operation=operations[0] if operations else None,
            subscriber_subnet_ref=subscriber_subnet_ref,
        )
        if application.kind == "project":
            owner = self.store.get_application(str(application.owner_application_id))
            model["owner_application"] = owner.to_dict()
            model["managed_projects"] = []
        else:
            model["owner_application"] = None
            project_summaries = []
            for project in self.store.list_managed_applications(
                application.application_id
            ):
                project_summaries.append(
                    {
                        "schema": "adaos.application.managed_project_summary.v1",
                        "application": project.to_dict(),
                        "installed": self._application_is_present(
                            project.application_id
                        ),
                    }
                )
            model["managed_projects"] = project_summaries
        return model

    def list_models(
        self,
        *,
        installed_only: bool = False,
        subscriber_subnet_ref: str | None = None,
        summary: bool = False,
    ) -> list[dict[str, Any]]:
        installations = {
            item.application_id: item
            for item in self.store.list_installations()
            if item.status != "removed"
        }
        subscriptions = {
            item.application_id: item for item in self.store.list_subscriptions()
        }
        selections: dict[str, list[RuntimeSelection]] = {}
        for selection in self.store.list_runtime_selections():
            selections.setdefault(selection.application_id, []).append(selection)
        operations: dict[str, ApplicationOperation] = {}
        for operation in self.store.list_operations():
            operations.setdefault(operation.application_id, operation)
        channel_sets = self.store.list_channel_sets() if summary else {}
        models: list[dict[str, Any]] = []
        for application in self.store.list_applications():
            installation = installations.get(application.application_id)
            subscription = subscriptions.get(application.application_id)
            runtime_selections = selections.get(application.application_id, [])
            if (
                installed_only
                and installation is None
                and not any(item.source == "local_trial" for item in runtime_selections)
            ):
                continue
            reader = self._read_summary_model if summary else self._read_model
            reader_kwargs: dict[str, Any] = {}
            if summary:
                reader_kwargs["channels"] = dict(
                    channel_sets.get(application.application_id, {}).get("channels")
                    or {}
                )
            models.append(
                reader(
                    application,
                    installation=installation,
                    subscription=subscription,
                    runtime_selections=runtime_selections,
                    operation=operations.get(application.application_id),
                    subscriber_subnet_ref=subscriber_subnet_ref,
                    **reader_kwargs,
                )
            )
        return models


__all__ = [
    "ApplicationExecutor",
    "ApplicationPlanConflict",
    "ApplicationService",
    "ApplicationServiceError",
]
