"""Sticky data-to-workflow routing, not a second publication head.

Registration follows Core workflow adoption while the publication/resource
commit guard is held. The unchanged old head can still authorize legacy data
after a failed commit; the accepted scoped head fences that legacy namespace.
"""

from __future__ import annotations

import hashlib
import json
import re

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .task_spec import seal, verify_identity
from .workflow_authority import WorkflowAuthority

_PROJECT = re.compile(r"project:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_WORKFLOW = re.compile(r"scenario:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class PrototypeDataOwnerRejected(ValueError):
    pass


class PrototypeDataOwners:
    def __init__(self, state_dir):
        self.authority = WorkflowAuthority(state_dir)
        self.root = self.authority.state_dir / "builder" / "prototype_data_owners"

    def _path(self, project_ref, suffix=".json"):
        if not isinstance(project_ref, str) or not _PROJECT.fullmatch(project_ref):
            raise PrototypeDataOwnerRejected("Invalid Prototype data owner")
        key = hashlib.sha256(project_ref.encode("utf-8")).hexdigest()
        return self.authority._safe(self.root / (key + suffix))

    def _read(self, path):
        self.authority._safe(path)
        with path.open("rb") as stream:
            payload = stream.read(16 * 1024 + 1)
        if len(payload) > 16 * 1024:
            raise PrototypeDataOwnerRejected(
                "Prototype data owner exceeds bounded size"
            )
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise PrototypeDataOwnerRejected("Invalid Prototype data owner record")
        return value

    def resolve(self, project_ref):
        # Preserve unsupported/unadopted legacy identities. Qualified Project
        # publication accepts only the canonical identity grammar above.
        if not isinstance(project_ref, str) or not _PROJECT.fullmatch(project_ref):
            return None
        path, receipt_path = (
            self._path(project_ref),
            self._path(project_ref, ".adoption.json"),
        )
        if not path.exists():
            if receipt_path.exists():
                raise PrototypeDataOwnerRejected(
                    "Prototype data owner missing; explicit recovery required"
                )
            return None
        value = self._read(path)
        verify_identity(value, id_key="owner_id", prefix="prototype-owner:")
        if (
            set(value)
            != {"schema", "project_ref", "workflow_ref", "owner_id", "digest"}
            or value["schema"] != "adaos.builder.prototype_data_owner.v1"
            or value["project_ref"] != project_ref
            or not isinstance(value["workflow_ref"], str)
            or not _WORKFLOW.fullmatch(value["workflow_ref"])
        ):
            raise PrototypeDataOwnerRejected("Invalid Prototype data owner binding")
        if receipt_path.exists():
            receipt = self._read(receipt_path)
            verify_identity(
                receipt, id_key="location_id", prefix="prototype-owner-location:"
            )
            if receipt != self._receipt(value):
                raise PrototypeDataOwnerRejected(
                    "Conflicting Prototype data owner location"
                )
        return value["workflow_ref"]

    @staticmethod
    def _receipt(value):
        return seal(
            {
                "schema": "adaos.builder.prototype_data_owner_location.v1",
                "project_ref": value["project_ref"],
                "owner_ref": value["owner_id"],
            },
            id_key="location_id",
            prefix="prototype-owner-location:",
        )

    def register(self, project_ref, workflow_ref):
        if not isinstance(workflow_ref, str) or not _WORKFLOW.fullmatch(workflow_ref):
            raise PrototypeDataOwnerRejected("Invalid Prototype workflow owner")
        head = self.authority.read(workflow_ref)
        if head is None:
            raise PrototypeDataOwnerRejected(
                "Prototype owner requires adopted Core workflow authority"
            )
        value = seal(
            {
                "schema": "adaos.builder.prototype_data_owner.v1",
                "project_ref": project_ref,
                "workflow_ref": workflow_ref,
            },
            id_key="owner_id",
            prefix="prototype-owner:",
        )
        path = self._path(project_ref)
        with mutation_lock(self._path(project_ref, ".lock"), timeout_s=30):
            before = self.resolve(project_ref)
            if before is not None and before != workflow_ref:
                raise PrototypeDataOwnerRejected(
                    "Prototype data owner requires explicit ownership migration"
                )
            if before is None:
                atomic_write_json(path, value)
            receipt = self._path(project_ref, ".adoption.json")
            if not receipt.exists():
                atomic_write_json(receipt, self._receipt(value))


def assert_no_unmigrated_project_data(state_dir, project_ref):
    from adaos.services.resources.prototype import PrototypeResourceService

    from .prototype_publications import PrototypePublicationRejected

    states = (
        PrototypeResourceService(state_dir=state_dir)
        ._store()
        .states(project_ref=project_ref)
    )
    for state in states.values():
        metadata = (state.get("definition") or {}).get("metadata") or {}
        if not state.get("publication_ref") and not metadata.get(
            "prototype_publication_ref"
        ):
            raise PrototypePublicationRejected(
                "Initial Project publication requires explicit legacy data migration"
            )
