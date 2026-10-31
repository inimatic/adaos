"""Private Core journal locations pinned to a frozen authoring scope.

The owner receipt selects a location, not task meaning, a Run or an accepted UI.
Recovery resolves the retained receipt without consulting current selection.
Legacy DEV journals are neither imported nor silently substituted.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .authoring_scope import resolve_authoring_scope
from .task_spec import seal, verify_identity
from .workflow_authority import WorkflowAuthority

_SCHEMA = "adaos.builder.generation_journal.v1"
_MAX_OWNER_BYTES = 16 * 1024


class GenerationJournalRejected(ValueError):
    pass


class GenerationJournals:
    def __init__(self, workflow):
        self.workflow = workflow
        self.root = (
            Path(os.path.abspath(workflow.state_dir))
            / "builder"
            / "generation_journals"
        )

    def _directory(self, journal_ref):
        if not isinstance(journal_ref, str) or not re.fullmatch(
            r"generation-journal:[a-f0-9]{24}", journal_ref
        ):
            raise GenerationJournalRejected("Invalid Core generation journal reference")
        return WorkflowAuthority._safe(self.root / journal_ref.partition(":")[2])

    def _read(self, journal_ref):
        path = WorkflowAuthority._safe(self._directory(journal_ref) / "owner.json")
        try:
            with path.open("rb") as stream:
                raw = stream.read(_MAX_OWNER_BYTES + 1)
            if len(raw) > _MAX_OWNER_BYTES:
                raise ValueError("Owner receipt exceeds bounded size")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise TypeError("Owner receipt is not an object")
            verify_identity(value, id_key="journal_ref", prefix="generation-journal:")
            scope = value["source_scope"]
            if (
                value["schema"] != _SCHEMA
                or value["journal_ref"] != journal_ref
                or set(scope)
                != {"project_ref", "object_type", "object_id", "manifest_digest"}
                or scope["object_type"] not in {"scenario", "skill"}
            ):
                raise ValueError("Owner receipt identity mismatch")
            return value
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise GenerationJournalRejected(
                "Core generation journal owner is missing or invalid; explicit recovery required"
            ) from error

    def prepare(self, project_ref):
        scope = resolve_authoring_scope(self.workflow, project_ref)
        owner = seal(
            {"schema": _SCHEMA, "source_scope": scope},
            id_key="journal_ref",
            prefix="generation-journal:",
        )
        directory = self._directory(owner["journal_ref"])
        lock = WorkflowAuthority._safe(self.root / ("." + directory.name + ".lock"))
        with mutation_lock(lock, timeout_s=30):
            path = WorkflowAuthority._safe(directory / "owner.json")
            if path.exists():
                if self._read(owner["journal_ref"]) != owner:
                    raise GenerationJournalRejected(
                        "Core generation journal owner conflicts"
                    )
            else:
                if directory.exists() and any(directory.iterdir()):
                    raise GenerationJournalRejected(
                        "Core generation journal lost its owner; explicit recovery required"
                    )
                directory.mkdir(parents=True, exist_ok=True)
                atomic_write_json(path, owner)
        return owner

    def resolve(self, journal_ref):
        self._read(journal_ref)
        return self._directory(journal_ref)
