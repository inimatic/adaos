from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from adaos.domain.artifact_release import canonical_payload_digest, sha256_digest
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    compile_definition,
    validate_workflow_record,
    workflow_definition_digest,
)
from adaos.services.workflow_artifacts import WorkflowArtifactLimits, canonical_workflow_digest
from adaos.services.workflow_registry import (
    WorkflowAdapterRegistry,
    platform_workflow_adapter_registry,
)


WORKFLOW_AUTHORING_CONTEXT_SCHEMA = "adaos.workflow.authoring_context.v1"
WORKFLOW_AUTHORING_ATTEMPT_SCHEMA = "adaos.workflow.authoring_attempt.v1"


class WorkflowAuthoringError(ValueError):
    """Raised when workflow authoring context or provenance cannot be trusted."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _abi_root() -> Path:
    return Path(__file__).resolve().parents[1] / "abi"


def workflow_abi_schema_records() -> tuple[dict[str, str], ...]:
    records: list[dict[str, str]] = []
    for path in sorted(_abi_root().glob("workflow.*.schema.json")):
        raw = path.read_bytes()
        try:
            schema = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowAuthoringError(f"invalid workflow ABI schema {path.name}: {exc}") from exc
        schema_id = str(schema.get("$id") or "").strip()
        if not schema_id:
            raise WorkflowAuthoringError(f"workflow ABI schema has no $id: {path.name}")
        records.append(
            {
                "filename": path.name,
                "schema_id": schema_id,
                "digest": sha256_digest(raw),
            }
        )
    return tuple(records)


def default_workflow_role_policy() -> dict[str, Any]:
    return {
        "roles": [
            {
                "role": "guest",
                "authenticated": False,
                "permission_source": "verified_authority_plane",
                "permission_ceiling": [],
            },
            {
                "role": "registered",
                "authenticated": True,
                "permission_source": "verified_authority_plane",
                "permission_ceiling": [],
            },
        ],
        "unknown_role_policy": "deny",
        "unknown_permission_policy": "deny",
        "role_self_assignment": "rejected",
    }


def workflow_role_policy_digest(
    definition: CompiledWorkflowDefinition | Mapping[str, Any],
    *,
    role_policy: Mapping[str, Any] | None = None,
) -> str:
    compiled = (
        definition
        if isinstance(definition, CompiledWorkflowDefinition)
        else compile_definition(definition)
    )
    payload = {
        "role_policy": copy.deepcopy(
            dict(role_policy or default_workflow_role_policy())
        ),
        "transition_authorities": [
            {
                "transition_id": item.transition_id,
                "authority": copy.deepcopy(dict(item.descriptor["authority"])),
            }
            for item in sorted(compiled.transitions, key=lambda value: value.transition_id)
        ],
    }
    return canonical_payload_digest(payload)


def workflow_authoring_context(
    *,
    current_definition: CompiledWorkflowDefinition | Mapping[str, Any] | None = None,
    adapters: WorkflowAdapterRegistry | None = None,
    domain_invariants: Sequence[Mapping[str, Any]] = (),
    examples: Sequence[Mapping[str, Any]] = (),
    role_policy: Mapping[str, Any] | None = None,
    limits: WorkflowArtifactLimits | None = None,
    context_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    compiled = _compiled_or_none(current_definition)
    current_digest = workflow_definition_digest(compiled) if compiled is not None else None
    generated = generated_at or _now()
    unsigned = {
        "schema": WORKFLOW_AUTHORING_CONTEXT_SCHEMA,
        "context_id": context_id
        or f"workflow-authoring:{(current_digest or 'new').removeprefix('sha256:')[:16]}",
        "generated_at": generated,
        "workflow_type": compiled.workflow_type if compiled is not None else None,
        "definition_version": compiled.definition_version if compiled is not None else None,
        "current_definition_digest": current_digest,
        "abi_schemas": [dict(item) for item in workflow_abi_schema_records()],
        "adapter_catalog": [
            dict(item)
            for item in (adapters or platform_workflow_adapter_registry()).registry_entries()
        ],
        "role_policy": copy.deepcopy(dict(role_policy or default_workflow_role_policy())),
        "domain_invariants": [dict(item) for item in domain_invariants],
        "examples": [dict(item) for item in examples],
        "limits": asdict(limits or WorkflowArtifactLimits()),
    }
    context = {**unsigned, "context_digest": canonical_payload_digest(unsigned)}
    validate_workflow_record(WORKFLOW_AUTHORING_CONTEXT_SCHEMA, context)
    return context


def _compiled_or_none(
    definition: CompiledWorkflowDefinition | Mapping[str, Any] | None,
) -> CompiledWorkflowDefinition | None:
    if definition is None:
        return None
    return definition if isinstance(definition, CompiledWorkflowDefinition) else compile_definition(definition)


class WorkflowAuthoringHistoryStore:
    def __init__(
        self,
        path: Path | str,
        *,
        max_attempts: int = 50,
        max_repairs: int = 20,
    ) -> None:
        self.path = Path(path)
        self.max_attempts = max(1, int(max_attempts))
        self.max_repairs = max(0, min(20, int(max_repairs)))

    def load(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowAuthoringError(f"cannot read workflow authoring history: {exc}") from exc
        if not isinstance(raw, list):
            raise WorkflowAuthoringError("workflow authoring history must contain a JSON array")
        records = tuple(validate_workflow_record(WORKFLOW_AUTHORING_ATTEMPT_SCHEMA, item) for item in raw)
        return records

    def record_attempt(
        self,
        *,
        context: Mapping[str, Any],
        attempt_id: str,
        model: Mapping[str, Any],
        validation_report: Mapping[str, Any],
        candidate_definition: Mapping[str, Any] | None = None,
        repair_history: Sequence[Mapping[str, Any]] = (),
        status: str | None = None,
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        context_record = validate_workflow_record(WORKFLOW_AUTHORING_CONTEXT_SCHEMA, context)
        diagnostics = [dict(item) for item in list(validation_report.get("diagnostics") or [])[:500]]
        candidate_digest = (
            canonical_workflow_digest(candidate_definition)
            if candidate_definition is not None
            else None
        )
        attempt = {
            "schema": WORKFLOW_AUTHORING_ATTEMPT_SCHEMA,
            "attempt_id": str(attempt_id),
            "context_id": str(context_record["context_id"]),
            "context_digest": str(context_record["context_digest"]),
            "workflow_type": (
                validation_report.get("workflow_type")
                if validation_report.get("workflow_type") is not None
                else context_record.get("workflow_type")
            ),
            "model": copy.deepcopy(dict(model)),
            "candidate_definition_digest": candidate_digest,
            "validation_report_digest": canonical_payload_digest(dict(validation_report)),
            "status": status
            or ("validation_passed" if validation_report.get("valid") is True else "validation_failed"),
            "diagnostics": diagnostics,
            "repair_history": [
                dict(item) for item in list(repair_history)[-self.max_repairs :]
            ],
            "recorded_at": recorded_at or _now(),
        }
        validate_workflow_record(WORKFLOW_AUTHORING_ATTEMPT_SCHEMA, attempt)
        records = [dict(item) for item in self.load()]
        records.append(attempt)
        records = records[-self.max_attempts :]
        self._write(records)
        return attempt

    def _write(self, records: Sequence[Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [dict(item) for item in records],
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


__all__ = [
    "WORKFLOW_AUTHORING_ATTEMPT_SCHEMA",
    "WORKFLOW_AUTHORING_CONTEXT_SCHEMA",
    "WorkflowAuthoringError",
    "WorkflowAuthoringHistoryStore",
    "default_workflow_role_policy",
    "workflow_abi_schema_records",
    "workflow_authoring_context",
    "workflow_role_policy_digest",
]
