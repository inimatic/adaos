"""Application-level native CBS composition and WorkspaceLock activation.

The semantic resolver currently emits one ``ApplicationResolution`` per
requirement.  A production Application must nevertheless change workspace
authority once, with every admitted requirement present.  This module builds
an immutable application/workspace composition over those child resolutions
and commits the resulting exact set through the existing artifact activation
transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from adaos.domain.artifact_release import WorkspaceLock, canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationResolution,
    BindingInstance,
)
from adaos.services.artifact_pipeline.activation import (
    ActivationResult,
    WorkspaceActivationManager,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.capability_binding_state import ResolutionPlanner
from adaos.services.capability_binding_state.local_state import LocalIdentityStore


CBS_RESOLUTION_SET_SCHEMA = "adaos.application.cbs_resolution_set.v1"
CBS_PLAN_SET_SCHEMA = "adaos.application.cbs_plan_set.v1"


class NativeApplicationCBSActivationError(ValueError):
    """An admitted Application cannot be committed as one workspace authority."""


def _digest_token(value: str) -> str:
    token = str(value or "").removeprefix("sha256:")
    if len(token) != 64:
        raise NativeApplicationCBSActivationError("CBS digest identity is invalid")
    return token


def _deduplicate(
    values: Iterable[Mapping[str, Any]],
    *,
    identity: Callable[[Mapping[str, Any]], str],
    label: str,
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for raw in values:
        item = dict(raw)
        key = str(identity(item) or "").strip()
        if not key:
            raise NativeApplicationCBSActivationError(
                f"{label} has no stable identity"
            )
        previous = records.get(key)
        if previous is not None and previous != item:
            raise NativeApplicationCBSActivationError(
                f"{label} identity has conflicting revisions: {key}"
            )
        records[key] = item
    return [records[key] for key in sorted(records)]


@dataclass(frozen=True, slots=True)
class NativeApplicationCBSActivationResult:
    activation: ActivationResult
    resolution_set_ref: str
    resolution_set_digest: str
    plan_set_ref: str
    plan_set_digest: str

    def to_dict(self) -> dict[str, Any]:
        lock = self.activation.workspace_lock.to_dict()
        return {
            "schema": "adaos.application.cbs_workspace_activation.v1",
            "status": self.activation.status,
            "operation_id": self.activation.operation_id,
            "release_digest": self.activation.release_digest,
            "workspace_lock_revision": self.activation.workspace_lock.lock_revision,
            "workspace_lock_digest": lock["lock_digest"],
            "resolution_set_ref": self.resolution_set_ref,
            "resolution_set_digest": self.resolution_set_digest,
            "plan_set_ref": self.plan_set_ref,
            "plan_set_digest": self.plan_set_digest,
            "idempotent_replay": self.activation.idempotent_replay,
        }


@dataclass(slots=True)
class NativeApplicationCBSActivationService:
    state_dir: Path
    workspace_root: Path
    package_store: ContentAddressedPackageStore
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    activation_manager: WorkspaceActivationManager | None = None

    def __post_init__(self) -> None:
        self.state_dir = Path(self.state_dir).expanduser().resolve()
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        if self.activation_manager is None:
            self.activation_manager = WorkspaceActivationManager(
                workspace_root=self.workspace_root,
                package_store=self.package_store,
                state_root=self.state_dir,
            )

    @property
    def root(self) -> Path:
        return self.state_dir / "applications" / "cbs-activation-sets"

    @property
    def writer_lock_path(self) -> Path:
        return self.root / ".writer.lock"

    @property
    def identity_store(self) -> LocalIdentityStore:
        return LocalIdentityStore(
            self.state_dir / "capability-binding-state" / "local"
        )

    def _record_path(self, digest: str) -> Path:
        return self.root / "resolutions" / f"{_digest_token(digest)}.json"

    def _plan_path(self, digest: str) -> Path:
        return self.root / "plans" / f"{_digest_token(digest)}.json"

    def _put_resolution_set(self, record: Mapping[str, Any]) -> None:
        digest = str(record.get("resolution_set_digest") or "")
        path = self._record_path(digest)
        with mutation_lock(self.writer_lock_path):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != dict(record):
                    raise NativeApplicationCBSActivationError(
                        "CBS resolution-set digest collision"
                    )
            else:
                atomic_write_json(path, record)

    def _put_plan_set(self, record: Mapping[str, Any]) -> None:
        digest = str(record.get("plan_set_digest") or "")
        path = self._plan_path(digest)
        with mutation_lock(self.writer_lock_path):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != dict(record):
                    raise NativeApplicationCBSActivationError(
                        "CBS plan-set digest collision"
                    )
            else:
                atomic_write_json(path, record)

    def _load_current_members(self, lock: WorkspaceLock | None) -> list[dict[str, Any]]:
        if lock is None or not isinstance(lock.cbs, Mapping):
            return []
        cbs = dict(lock.cbs)
        ref = str(cbs.get("application_resolution_ref") or "")
        digest = str(cbs.get("application_resolution_digest") or "")
        if ref.startswith("application-resolution:workspace-set/"):
            path = self._record_path(digest)
            if not path.is_file():
                raise NativeApplicationCBSActivationError(
                    "active CBS resolution set is unavailable"
                )
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                raise NativeApplicationCBSActivationError(
                    "active CBS resolution set is malformed"
                )
            record = dict(value)
            expected = str(record.pop("resolution_set_digest", ""))
            if (
                expected != digest
                or canonical_payload_digest(record) != digest
                or record.get("schema") != CBS_RESOLUTION_SET_SCHEMA
            ):
                raise NativeApplicationCBSActivationError(
                    "active CBS resolution set failed integrity validation"
                )
            members = record.get("members")
            if not isinstance(members, list) or any(
                not isinstance(item, Mapping) for item in members
            ):
                raise NativeApplicationCBSActivationError(
                    "active CBS resolution set members are malformed"
                )
            return [dict(item) for item in members]

        # Preserve an older single-resolution CBS authority as an opaque member
        # until its owning lifecycle republishes it as an application set.
        return [
            {
                "application_ref": f"legacy-cbs:{digest}",
                "project_release_digest": None,
                "admission_digest": None,
                "resolutions": [
                    {
                        "resolution_ref": ref,
                        "resolution_digest": digest,
                    }
                ],
                "binding_instances": [
                    dict(item) for item in cbs.get("binding_instances") or ()
                ],
                "state_attachments": [
                    dict(item) for item in cbs.get("state_attachments") or ()
                ],
                "evidence": [
                    {
                        "legacy_evidence_set_digest": str(
                            cbs.get("evidence_set_digest") or ""
                        )
                    }
                ],
            }
        ]

    def _application_member(
        self,
        *,
        admission: Mapping[str, Any],
        resolutions: tuple[ApplicationResolution, ...],
    ) -> dict[str, Any]:
        binding_instances = _deduplicate(
            (
                item
                for resolution in resolutions
                for item in resolution.to_dict()["binding_instances"]
            ),
            identity=lambda item: str(item.get("ref") or ""),
            label="BindingInstance",
        )
        state_attachments = _deduplicate(
            (
                item
                for resolution in resolutions
                for item in resolution.to_dict()["state_attachments"]
            ),
            identity=lambda item: str(item.get("state_space_ref") or ""),
            label="StateSpace attachment",
        )
        evidence = _deduplicate(
            (
                item
                for resolution in resolutions
                for item in resolution.to_dict()["evidence"]
            ),
            identity=lambda item: str(
                item.get("claim_digest") or item.get("assessment_digest") or ""
            ),
            label="Evidence assessment",
        )
        return {
            "application_ref": str(admission["application_ref"]),
            "project_release_digest": str(admission["project_release_digest"]),
            "admission_digest": str(admission["admission_digest"]),
            "resolutions": [
                {
                    "resolution_ref": resolution.resolution_ref,
                    "resolution_digest": resolution.digest,
                }
                for resolution in sorted(
                    resolutions, key=lambda item: item.resolution_ref
                )
            ],
            "binding_instances": binding_instances,
            "state_attachments": state_attachments,
            "evidence": evidence,
        }

    def _compose_resolution_set(
        self,
        *,
        current: WorkspaceLock | None,
        admission: Mapping[str, Any],
        resolutions: tuple[ApplicationResolution, ...],
    ) -> dict[str, Any]:
        members = {
            str(item.get("application_ref") or ""): dict(item)
            for item in self._load_current_members(current)
        }
        application_ref = str(admission["application_ref"])
        members[application_ref] = self._application_member(
            admission=admission,
            resolutions=resolutions,
        )
        if not application_ref:
            raise NativeApplicationCBSActivationError(
                "CBS admission has no Application identity"
            )
        record: dict[str, Any] = {
            "schema": CBS_RESOLUTION_SET_SCHEMA,
            "workspace_ref": str(admission["workspace_ref"]),
            "members": [members[key] for key in sorted(members)],
        }
        digest = canonical_payload_digest(record)
        token = _digest_token(digest)[:24]
        record["resolution_set_ref"] = f"application-resolution:workspace-set/{token}"
        # The stable ref is descriptive metadata.  It is included in the exact
        # record digest so a reader cannot substitute another composition.
        digest = canonical_payload_digest(record)
        record["resolution_set_digest"] = digest
        self._put_resolution_set(record)
        return record

    def current_status(
        self,
        *,
        application_ref: str,
        project_release_digest: str,
        admission_digest: str,
    ) -> dict[str, Any]:
        """Inspect whether one exact admission is already active in WorkspaceLock."""

        manager = self.activation_manager
        if manager is None:  # pragma: no cover - guarded by __post_init__
            return {"current": False, "reason": "activation_manager_unavailable"}
        lock = manager.load_lock()
        if lock is None:
            return {"current": False, "reason": "workspace_lock_absent"}
        _, _, project_id = str(application_ref or "").partition(":")
        slot = next((item for item in lock.slots if item.slot_id == project_id), None)
        if slot is None:
            return {"current": False, "reason": "application_slot_absent"}
        if slot.release_digest != project_release_digest:
            return {
                "current": False,
                "reason": "application_slot_release_drifted",
                "observed_release_digest": slot.release_digest,
            }
        try:
            members = self._load_current_members(lock)
        except NativeApplicationCBSActivationError as exc:
            return {
                "current": False,
                "reason": "resolution_set_unavailable",
                "message": str(exc),
            }
        member = next(
            (
                item
                for item in members
                if str(item.get("application_ref") or "") == application_ref
            ),
            None,
        )
        if member is None:
            return {"current": False, "reason": "application_resolution_absent"}
        if (
            str(member.get("project_release_digest") or "")
            != project_release_digest
            or str(member.get("admission_digest") or "") != admission_digest
        ):
            return {"current": False, "reason": "application_resolution_drifted"}
        payload = lock.to_dict()
        return {
            "current": True,
            "reason": "exact_workspace_authority",
            "workspace_lock_revision": lock.lock_revision,
            "workspace_lock_digest": payload["lock_digest"],
            "application_resolution_ref": (
                dict(lock.cbs or {}).get("application_resolution_ref")
            ),
            "application_resolution_digest": (
                dict(lock.cbs or {}).get("application_resolution_digest")
            ),
        }

    @staticmethod
    def _aggregate_lock_payload(
        resolution_set: Mapping[str, Any],
        *,
        plan_set_ref: str,
        plan_set_digest: str,
    ) -> dict[str, Any]:
        members = [
            dict(item)
            for item in resolution_set.get("members") or ()
            if isinstance(item, Mapping)
        ]
        bindings = _deduplicate(
            (
                item
                for member in members
                for item in member.get("binding_instances") or ()
                if isinstance(item, Mapping)
            ),
            identity=lambda item: str(item.get("ref") or ""),
            label="BindingInstance",
        )
        states = _deduplicate(
            (
                item
                for member in members
                for item in member.get("state_attachments") or ()
                if isinstance(item, Mapping)
            ),
            identity=lambda item: str(item.get("state_space_ref") or ""),
            label="StateSpace attachment",
        )
        evidence = [
            dict(item)
            for member in members
            for item in member.get("evidence") or ()
            if isinstance(item, Mapping)
        ]
        return {
            "application_resolution_ref": str(
                resolution_set["resolution_set_ref"]
            ),
            "application_resolution_digest": str(
                resolution_set["resolution_set_digest"]
            ),
            "resolution_plan_ref": plan_set_ref,
            "resolution_plan_digest": plan_set_digest,
            "writer_protocol": "cbs-single-writer-v1",
            "binding_instances": bindings,
            "state_spaces": states,
            "state_attachments": states,
            "evidence_set_digest": canonical_payload_digest(evidence),
        }

    def activate(
        self,
        *,
        admission: Mapping[str, Any],
        release_plan: ReleasePlan,
        application_id: str,
        idempotency_key: str,
        actor_ref: str,
    ) -> NativeApplicationCBSActivationResult:
        if str(admission.get("status") or "") != "admitted":
            raise NativeApplicationCBSActivationError(
                "native CBS admission is not admitted"
            )
        application_ref = f"application:{str(application_id or '').strip()}"
        if str(admission.get("application_ref") or "") != application_ref:
            raise NativeApplicationCBSActivationError(
                "native CBS admission belongs to another Application"
            )
        release_digest = (
            release_plan.release.release_digest
            or release_plan.release.computed_digest()
        )
        if str(admission.get("project_release_digest") or "") != release_digest:
            raise NativeApplicationCBSActivationError(
                "native CBS admission belongs to another ProjectRelease"
            )
        resolutions = tuple(
            ApplicationResolution.from_mapping(item)
            for item in admission.get("resolutions") or ()
            if isinstance(item, Mapping)
        )
        total = int(admission.get("requirements_total") or 0)
        if not resolutions or len(resolutions) != total:
            raise NativeApplicationCBSActivationError(
                "native CBS admission does not contain every resolution"
            )
        if any(
            resolution.to_dict()["project_release_digest"] != release_digest
            for resolution in resolutions
        ):
            raise NativeApplicationCBSActivationError(
                "native CBS resolution closure differs from ProjectRelease"
            )
        if any(
            str(item.get("status") or "") != "admissible"
            for resolution in resolutions
            for item in resolution.to_dict()["evidence"]
        ):
            raise NativeApplicationCBSActivationError(
                "native CBS resolution evidence is not admissible"
            )

        instance_records = tuple(
            BindingInstance.from_mapping(item)
            for item in admission.get("binding_instance_records") or ()
            if isinstance(item, Mapping)
        )
        selected_instances = {
            (str(item.get("ref") or ""), str(item.get("revision_digest") or ""))
            for resolution in resolutions
            for item in resolution.to_dict()["binding_instances"]
        }
        if {
            (item.stable_ref, item.digest) for item in instance_records
        } != selected_instances:
            raise NativeApplicationCBSActivationError(
                "native CBS admission lacks exact BindingInstance revisions"
            )
        for instance in instance_records:
            self.identity_store.append(instance)

        manager = self.activation_manager
        if manager is None:  # pragma: no cover - guarded by __post_init__
            raise NativeApplicationCBSActivationError(
                "Workspace activation manager is unavailable"
            )
        current = manager.load_lock()
        status = self.current_status(
            application_ref=application_ref,
            project_release_digest=release_digest,
            admission_digest=str(admission.get("admission_digest") or ""),
        )
        if status["current"] is True and current is not None:
            cbs = dict(current.cbs or {})
            activation = ActivationResult(
                operation_id=manager.operation_id(
                    "native-cbs-current:"
                    f"{str(admission.get('admission_digest') or '')}"
                ),
                status="completed",
                workspace_lock=current,
                release_digest=release_digest,
                idempotent_replay=True,
            )
            return NativeApplicationCBSActivationResult(
                activation=activation,
                resolution_set_ref=str(cbs["application_resolution_ref"]),
                resolution_set_digest=str(cbs["application_resolution_digest"]),
                plan_set_ref=str(cbs["resolution_plan_ref"]),
                plan_set_digest=str(cbs["resolution_plan_digest"]),
            )
        resolution_set = self._compose_resolution_set(
            current=current,
            admission=admission,
            resolutions=resolutions,
        )
        planner = ResolutionPlanner(now=self.now)
        plans = tuple(
            planner.build(resolution, current_lock=current)
            for resolution in resolutions
        )
        current_payload = current.to_dict() if current is not None else None
        base_lock: dict[str, Any] = {
            "revision": current.lock_revision if current is not None else 0
        }
        if current_payload is not None:
            base_lock["digest"] = current_payload["lock_digest"]
        plan_set: dict[str, Any] = {
            "schema": CBS_PLAN_SET_SCHEMA,
            "application_resolution_set_ref": resolution_set[
                "resolution_set_ref"
            ],
            "application_resolution_set_digest": resolution_set[
                "resolution_set_digest"
            ],
            "base_lock": base_lock,
            "plans": [
                plan.to_dict()
                for plan in sorted(plans, key=lambda item: item.plan_ref)
            ],
        }
        provisional_digest = canonical_payload_digest(plan_set)
        plan_set_ref = (
            "resolution-plan:application-set/"
            f"{_digest_token(provisional_digest)[:24]}"
        )
        plan_set["plan_set_ref"] = plan_set_ref
        plan_set_digest = canonical_payload_digest(plan_set)
        plan_set["plan_set_digest"] = plan_set_digest
        self._put_plan_set(plan_set)
        cbs_lock = self._aggregate_lock_payload(
            resolution_set,
            plan_set_ref=plan_set_ref,
            plan_set_digest=plan_set_digest,
        )
        skip_policy = {
            "mode": "skip",
            "approved_by": str(actor_ref or "system:applications"),
            "reason": (
                "ProjectDeployment completed component activation and health "
                "before the final WorkspaceLock authority commit"
            ),
        }
        activation = manager.activate(
            release_plan,
            idempotency_key=f"native-cbs:{idempotency_key}",
            slot_id=release_plan.release.project_id,
            fetch_package=lambda package: self.package_store.read(package.digest),
            reload_policy=skip_policy,
            health_policy=skip_policy,
            permission_decision={
                "approved": True,
                "approved_by": str(actor_ref or "system:applications"),
                "reason": "permissions were approved by the reviewed Application operation",
            },
            expected_lock_digest=base_lock.get("digest"),
            cbs_lock=cbs_lock,
            application_resolution_digest=str(
                resolution_set["resolution_set_digest"]
            ),
            resolution_plan_digest=plan_set_digest,
        )
        return NativeApplicationCBSActivationResult(
            activation=activation,
            resolution_set_ref=str(resolution_set["resolution_set_ref"]),
            resolution_set_digest=str(resolution_set["resolution_set_digest"]),
            plan_set_ref=plan_set_ref,
            plan_set_digest=plan_set_digest,
        )


__all__ = [
    "CBS_PLAN_SET_SCHEMA",
    "CBS_RESOLUTION_SET_SCHEMA",
    "NativeApplicationCBSActivationError",
    "NativeApplicationCBSActivationResult",
    "NativeApplicationCBSActivationService",
]
