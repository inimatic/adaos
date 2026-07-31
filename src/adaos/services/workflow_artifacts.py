from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    WorkflowDefinitionError,
    compile_definition,
)


WORKFLOW_FILENAME = "workflow.json"
WORKFLOW_DEFINITION_SCHEMA = "adaos.workflow.definition.v1"


class WorkflowArtifactError(WorkflowDefinitionError):
    """Raised when a manifest-bound workflow artifact is unsafe or inconsistent."""


@dataclass(frozen=True, slots=True)
class WorkflowArtifactLimits:
    max_bytes: int = 512 * 1024
    max_depth: int = 64
    max_states: int = 256
    max_commands: int = 512
    max_transitions: int = 1024


@dataclass(frozen=True, slots=True)
class WorkflowDefinitionArtifact:
    artifact_root: Path
    manifest_path: Path
    definition_path: Path
    definition: dict[str, Any]
    compiled: CompiledWorkflowDefinition
    definition_digest: str
    raw_digest: str


@dataclass(frozen=True, slots=True)
class WorkflowDefinitionPayload:
    definition: dict[str, Any]
    compiled: CompiledWorkflowDefinition
    definition_digest: str
    raw_digest: str


def _sha256(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def canonical_workflow_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_workflow_digest(value: Mapping[str, Any]) -> str:
    return _sha256(canonical_workflow_bytes(value))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowArtifactError(f"workflow.json contains duplicate key: {key}")
        result[key] = value
    return result


def _depth(value: Any) -> int:
    maximum = 1
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        maximum = max(maximum, depth)
        if isinstance(current, Mapping):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return maximum


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkflowArtifactError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise WorkflowArtifactError(f"{path.name} must contain an object")
    return dict(value)


def workflow_manifest_reference(
    manifest: Mapping[str, Any],
    *,
    allow_legacy_inline: bool = True,
) -> str | None:
    raw = manifest.get("workflow")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise WorkflowArtifactError("workflow manifest declaration must be an object")
    if "manifest" not in raw:
        if allow_legacy_inline:
            return None
        raise WorkflowArtifactError("inline governed workflow definitions are not supported")
    unknown = sorted(set(raw) - {"manifest"})
    if unknown:
        raise WorkflowArtifactError(
            "workflow manifest declaration contains unsupported fields: " + ", ".join(unknown)
        )
    reference = str(raw.get("manifest") or "").strip()
    if reference != WORKFLOW_FILENAME:
        raise WorkflowArtifactError(f"workflow.manifest must be exactly {WORKFLOW_FILENAME}")
    return reference


def validate_workflow_definition_bytes(
    raw: bytes,
    *,
    registered_guards: set[str] | frozenset[str] | None = None,
    registered_activities: set[str] | frozenset[str] | None = None,
    limits: WorkflowArtifactLimits | None = None,
) -> WorkflowDefinitionPayload:
    limits = limits or WorkflowArtifactLimits()
    if len(raw) > limits.max_bytes:
        raise WorkflowArtifactError(f"{WORKFLOW_FILENAME} exceeds {limits.max_bytes} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowArtifactError(f"{WORKFLOW_FILENAME} is not valid UTF-8 JSON") from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except WorkflowArtifactError:
        raise
    except json.JSONDecodeError as exc:
        raise WorkflowArtifactError(f"invalid {WORKFLOW_FILENAME}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise WorkflowArtifactError(f"{WORKFLOW_FILENAME} must contain one JSON object")
    definition = dict(value)
    if _depth(definition) > limits.max_depth:
        raise WorkflowArtifactError(f"{WORKFLOW_FILENAME} exceeds maximum depth {limits.max_depth}")
    for field, maximum in (
        ("states", limits.max_states),
        ("commands", limits.max_commands),
        ("transitions", limits.max_transitions),
    ):
        items = definition.get(field)
        if isinstance(items, list) and len(items) > maximum:
            raise WorkflowArtifactError(f"workflow {field} exceeds limit {maximum}")
    try:
        compiled = compile_definition(
            definition,
            registered_guards=registered_guards,
            registered_activities=registered_activities,
        )
    except WorkflowArtifactError:
        raise
    except WorkflowDefinitionError as exc:
        raise WorkflowArtifactError(f"invalid {WORKFLOW_FILENAME}: {exc}") from exc
    return WorkflowDefinitionPayload(
        definition=copy.deepcopy(definition),
        compiled=compiled,
        definition_digest=canonical_workflow_digest(definition),
        raw_digest=_sha256(raw),
    )


def _read_definition(
    path: Path,
    *,
    registered_guards: set[str] | frozenset[str] | None,
    registered_activities: set[str] | frozenset[str] | None,
    limits: WorkflowArtifactLimits,
) -> WorkflowDefinitionPayload:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorkflowArtifactError(f"cannot read {WORKFLOW_FILENAME}: {exc}") from exc
    return validate_workflow_definition_bytes(
        raw,
        registered_guards=registered_guards,
        registered_activities=registered_activities,
        limits=limits,
    )


def load_manifest_bound_workflow(
    artifact_root: Path | str,
    *,
    manifest_name: str,
    registered_guards: set[str] | frozenset[str] | None = None,
    registered_activities: set[str] | frozenset[str] | None = None,
    limits: WorkflowArtifactLimits | None = None,
    allow_legacy_inline: bool = True,
) -> WorkflowDefinitionArtifact | None:
    root = Path(artifact_root).expanduser().resolve()
    manifest_path = root / manifest_name
    if not manifest_path.is_file():
        raise WorkflowArtifactError(f"required component manifest is missing: {manifest_name}")
    manifest = _read_manifest(manifest_path)
    reference = workflow_manifest_reference(manifest, allow_legacy_inline=allow_legacy_inline)
    definition_path = root / WORKFLOW_FILENAME
    if reference is None:
        if definition_path.exists():
            raise WorkflowArtifactError(
                f"{WORKFLOW_FILENAME} exists but {manifest_name} does not reference it"
            )
        return None
    if not definition_path.is_file():
        raise WorkflowArtifactError(
            f"{manifest_name} references missing {WORKFLOW_FILENAME}"
        )
    payload = _read_definition(
        definition_path,
        registered_guards=registered_guards,
        registered_activities=registered_activities,
        limits=limits or WorkflowArtifactLimits(),
    )
    return WorkflowDefinitionArtifact(
        artifact_root=root,
        manifest_path=manifest_path,
        definition_path=definition_path,
        definition=copy.deepcopy(payload.definition),
        compiled=payload.compiled,
        definition_digest=payload.definition_digest,
        raw_digest=payload.raw_digest,
    )


__all__ = [
    "WORKFLOW_DEFINITION_SCHEMA",
    "WORKFLOW_FILENAME",
    "WorkflowArtifactError",
    "WorkflowArtifactLimits",
    "WorkflowDefinitionArtifact",
    "WorkflowDefinitionPayload",
    "canonical_workflow_bytes",
    "canonical_workflow_digest",
    "load_manifest_bound_workflow",
    "validate_workflow_definition_bytes",
    "workflow_manifest_reference",
]
