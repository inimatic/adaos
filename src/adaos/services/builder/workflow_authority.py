"""Sticky opt-in Core storage for workflow authority, distinct from DEV mirrors.

The immutable adoption receipt is a location fence, not a second current head.
An initial copy preserves exact logical state. Receipt failure after the copy
is recoverable from that Core state, never by overwriting it from DEV again.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .task_spec import seal, verify_identity

_MAX_STATE_BYTES = 512 * 1024


class WorkflowAuthorityRejected(ValueError):
    pass


class WorkflowAuthority:
    def __init__(self, state_dir):
        self.state_dir = Path(os.path.abspath(state_dir))

    @staticmethod
    def _safe(path):
        for part in (path, *path.parents):
            try:
                metadata = part.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise WorkflowAuthorityRejected(
                    "Workflow authority must not traverse links"
                )
        return path

    def directory(self, project_ref):
        if not isinstance(project_ref, str):
            raise WorkflowAuthorityRejected("Invalid workflow authority target")
        kind, separator, identity = project_ref.partition(":")
        if (
            not separator
            or kind not in {"project", "scenario", "skill"}
            or not identity
            or len(project_ref) > 4096
            or "\0" in project_ref
        ):
            raise WorkflowAuthorityRejected("Invalid workflow authority target")
        key = hashlib.sha256(project_ref.encode("utf-8")).hexdigest()
        return self._safe(
            self.state_dir / "builder" / "workflow_authority" / kind / key
        )

    def head_path(self, project_ref):
        return self._safe(self.directory(project_ref) / "current.json")

    def receipt_path(self, project_ref):
        return self._safe(self.directory(project_ref) / "adoption.json")

    def resolve(self, project_ref):
        """Return Core authority or None for a genuinely unadopted workflow."""
        head, receipt = self.head_path(project_ref), self.receipt_path(project_ref)
        if receipt.exists() and not head.is_file():
            raise WorkflowAuthorityRejected(
                "Adopted workflow authority is missing; explicit recovery required"
            )
        if head.exists():
            if not head.is_file():
                raise WorkflowAuthorityRejected("Invalid workflow authority file")
            return head
        return None

    @staticmethod
    def _read(path):
        return WorkflowAuthority._decode(path.read_bytes())

    @staticmethod
    def _decode(raw):
        if len(raw) > _MAX_STATE_BYTES:
            raise WorkflowAuthorityRejected(
                "Workflow authority exceeds bounded state size"
            )
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as error:
            raise WorkflowAuthorityRejected("Workflow authority is corrupt") from error
        if not isinstance(value, dict):
            raise WorkflowAuthorityRejected("Workflow authority requires an object")
        return value

    @staticmethod
    def _bounded(value):
        if (
            len(
                (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            > _MAX_STATE_BYTES
        ):
            raise WorkflowAuthorityRejected(
                "Workflow authority exceeds bounded state size"
            )

    def validate(self, project_ref, value):
        try:
            receipt = value["workflow_authority"]
            verify_identity(receipt, id_key="adoption_id", prefix="workflow-adoption:")
            if (
                receipt["schema"] != "adaos.builder.workflow_authority_adoption.v1"
                or receipt["project_ref"] != project_ref
            ):
                raise WorkflowAuthorityRejected("Workflow authority identity mismatch")
            path = self.receipt_path(project_ref)
            if path.exists() and self._read(path) != receipt:
                raise WorkflowAuthorityRejected(
                    "Workflow adoption receipt conflicts with its head"
                )
            return receipt
        except WorkflowAuthorityRejected:
            raise
        except (ValueError, KeyError, TypeError) as error:
            raise WorkflowAuthorityRejected(
                "Workflow authority identity is invalid"
            ) from error

    def adopt(self, project_ref, source_path):
        """Copy once; recover an interrupted fence without overwriting late state.

        The source is read only when no Core head/receipt exists. Its pathname
        is supplied by the owning workflow service, never by a model request.
        """
        directory = self.directory(project_ref)
        with mutation_lock(self._safe(directory / ".adoption.lock"), timeout_s=30):
            head = self.resolve(project_ref)
            copied = head is None
            if copied:
                source = self._safe(Path(os.path.abspath(source_path)))
                raw = source.read_bytes()
                value = self._decode(raw)
                if "workflow_authority" in value:
                    raise WorkflowAuthorityRejected(
                        "A DEV mirror cannot bootstrap lost Core authority"
                    )
                source_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
                receipt = seal(
                    {
                        "schema": "adaos.builder.workflow_authority_adoption.v1",
                        "project_ref": project_ref,
                        "source_digest": source_digest,
                    },
                    id_key="adoption_id",
                    prefix="workflow-adoption:",
                )
                value["workflow_authority"] = receipt
                self._bounded(value)
                head = self.head_path(project_ref)
                atomic_write_json(head, value)
            else:
                value = self._read(head)
                receipt = self.validate(project_ref, value)
            target = self.receipt_path(project_ref)
            if target.exists():
                if self._read(target) != receipt:
                    raise WorkflowAuthorityRejected(
                        "Workflow adoption receipt cannot be replaced"
                    )
            else:
                atomic_write_json(target, receipt)
            return {
                "adopted": True,
                "copied": copied,
                "adoption_ref": receipt["adoption_id"],
            }

    def write(self, project_ref, value):
        """Persist a managed workflow update after authority selection."""
        path = self.resolve(project_ref)
        if path is None:
            raise WorkflowAuthorityRejected("Workflow has not adopted Core authority")
        receipt = self.validate(project_ref, self._read(path))
        if value.get("workflow_authority") != receipt:
            raise WorkflowAuthorityRejected(
                "Workflow update cannot replace its adoption identity"
            )
        payload = copy.deepcopy(value)
        self._bounded(payload)
        atomic_write_json(path, payload)
