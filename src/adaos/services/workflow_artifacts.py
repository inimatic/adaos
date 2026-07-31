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
    WORKFLOW_DEFINITION_SCHEMA as GOVERNED_WORKFLOW_DEFINITION_SCHEMA,
    WORKFLOW_VALIDATION_REPORT_SCHEMA,
    WorkflowDefinitionError,
    compile_definition,
    validate_workflow_record,
    workflow_schema_diagnostics,
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
    validation_report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkflowDefinitionPayload:
    definition: dict[str, Any]
    compiled: CompiledWorkflowDefinition
    definition_digest: str
    raw_digest: str
    validation_report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkflowValidationResult:
    report: dict[str, Any]
    definition: dict[str, Any] | None = None
    compiled: CompiledWorkflowDefinition | None = None


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


def _identity_map(values: Any, identity: str) -> dict[str, Any]:
    if not isinstance(values, list):
        return {}
    return {
        str(item.get(identity)): dict(item)
        for item in values
        if isinstance(item, Mapping) and str(item.get(identity) or "").strip()
    }


def workflow_graph_diff(
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe semantic graph changes using stable workflow identities."""

    result: dict[str, Any] = {
        "baseline_digest": canonical_workflow_digest(previous) if previous is not None else None,
    }
    for output, field, identity in (
        ("states", "states", "id"),
        ("commands", "commands", "id"),
        ("transitions", "transitions", "transition_id"),
    ):
        before = _identity_map(previous.get(field) if previous is not None else [], identity)
        after = _identity_map(current.get(field), identity)
        result[output] = {
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
            "changed": sorted(
                key
                for key in set(before).intersection(after)
                if canonical_workflow_digest(before[key]) != canonical_workflow_digest(after[key])
            ),
        }
    return result


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


def _workflow_metrics(raw: bytes, definition: Mapping[str, Any] | None) -> dict[str, int]:
    definition = definition or {}
    transitions = definition.get("transitions")
    transitions = transitions if isinstance(transitions, list) else []
    adapter_refs: set[tuple[str, str]] = set()
    for raw_transition in transitions:
        if not isinstance(raw_transition, Mapping):
            continue
        for guard in raw_transition.get("guards") or []:
            if isinstance(guard, Mapping) and str(guard.get("id") or "").strip():
                adapter_refs.add(("guard", str(guard["id"])))
        effect = raw_transition.get("effect")
        if isinstance(effect, Mapping):
            for key in ("activity", "compensation"):
                if str(effect.get(key) or "").strip():
                    adapter_refs.add((key, str(effect[key])))
    states = definition.get("states")
    states = states if isinstance(states, list) else []
    commands = definition.get("commands")
    commands = commands if isinstance(commands, list) else []
    return {
        "bytes": len(raw),
        "depth": _depth(definition) if definition else 0,
        "states": len(states),
        "commands": len(commands),
        "transitions": len(transitions),
        "terminal_states": sum(
            1 for item in states if isinstance(item, Mapping) and item.get("terminal") is True
        ),
        "adapter_refs": len(adapter_refs),
    }


def validate_workflow_definition_report(
    raw: bytes,
    *,
    registered_guards: set[str] | frozenset[str] | None = None,
    registered_activities: set[str] | frozenset[str] | None = None,
    limits: WorkflowArtifactLimits | None = None,
    previous_definition: Mapping[str, Any] | None = None,
) -> WorkflowValidationResult:
    """Return one bounded report suitable for humans, Builder, and LLM repair."""

    limits = limits or WorkflowArtifactLimits()
    diagnostics: list[dict[str, Any]] = []
    definition: dict[str, Any] | None = None
    compiled: CompiledWorkflowDefinition | None = None
    if len(raw) > limits.max_bytes:
        diagnostics.append(
            {
                "code": "workflow.limit.bytes",
                "severity": "error",
                "path": "$",
                "message": f"{WORKFLOW_FILENAME} exceeds {limits.max_bytes} bytes",
                "details": {"actual": len(raw), "limit": limits.max_bytes},
            }
        )
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            diagnostics.append(
                {
                    "code": "workflow.encoding.utf8",
                    "severity": "error",
                    "path": "$",
                    "message": f"{WORKFLOW_FILENAME} is not valid UTF-8 JSON",
                    "details": {"start": exc.start, "end": exc.end},
                }
            )
        else:
            try:
                value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
            except WorkflowArtifactError as exc:
                diagnostics.append(
                    {
                        "code": "workflow.json.duplicate_key",
                        "severity": "error",
                        "path": "$",
                        "message": str(exc),
                    }
                )
            except json.JSONDecodeError as exc:
                diagnostics.append(
                    {
                        "code": "workflow.json.invalid",
                        "severity": "error",
                        "path": f"$[{exc.lineno}:{exc.colno}]",
                        "message": f"invalid {WORKFLOW_FILENAME}: {exc.msg}",
                        "details": {"line": exc.lineno, "column": exc.colno, "position": exc.pos},
                    }
                )
            else:
                if not isinstance(value, Mapping):
                    diagnostics.append(
                        {
                            "code": "workflow.json.object_required",
                            "severity": "error",
                            "path": "$",
                            "message": f"{WORKFLOW_FILENAME} must contain one JSON object",
                        }
                    )
                else:
                    definition = dict(value)

    metrics = _workflow_metrics(raw, definition)
    if definition is not None:
        if metrics["depth"] > limits.max_depth:
            diagnostics.append(
                {
                    "code": "workflow.limit.depth",
                    "severity": "error",
                    "path": "$",
                    "message": f"{WORKFLOW_FILENAME} exceeds maximum depth {limits.max_depth}",
                    "details": {"actual": metrics["depth"], "limit": limits.max_depth},
                }
            )
        for field, maximum in (
            ("states", limits.max_states),
            ("commands", limits.max_commands),
            ("transitions", limits.max_transitions),
        ):
            if metrics[field] > maximum:
                diagnostics.append(
                    {
                        "code": f"workflow.limit.{field}",
                        "severity": "error",
                        "path": f"$.{field}",
                        "message": f"workflow {field} exceeds limit {maximum}",
                        "details": {"actual": metrics[field], "limit": maximum},
                    }
                )
        diagnostics.extend(
            workflow_schema_diagnostics(GOVERNED_WORKFLOW_DEFINITION_SCHEMA, definition)
        )
        if not diagnostics:
            try:
                compiled = compile_definition(
                    definition,
                    registered_guards=registered_guards,
                    registered_activities=registered_activities,
                )
            except WorkflowDefinitionError as exc:
                diagnostics.append(
                    {
                        "code": "workflow.semantic.invalid",
                        "severity": "error",
                        "path": "$",
                        "message": str(exc),
                    }
                )

    report = {
        "schema": WORKFLOW_VALIDATION_REPORT_SCHEMA,
        "valid": compiled is not None and not diagnostics,
        "workflow_type": (
            str(definition.get("workflow_type"))
            if definition is not None and definition.get("workflow_type") is not None
            else None
        ),
        "definition_version": (
            str(definition.get("definition_version"))
            if definition is not None and definition.get("definition_version") is not None
            else None
        ),
        "definition_digest": canonical_workflow_digest(definition) if definition is not None else None,
        "raw_digest": _sha256(raw),
        "diagnostics": diagnostics[:500],
        "metrics": metrics,
        "graph_diff": (
            workflow_graph_diff(definition, previous_definition)
            if compiled is not None
            else {
                "baseline_digest": (
                    canonical_workflow_digest(previous_definition)
                    if previous_definition is not None
                    else None
                ),
                "states": {"added": [], "removed": [], "changed": []},
                "commands": {"added": [], "removed": [], "changed": []},
                "transitions": {"added": [], "removed": [], "changed": []},
            }
        ),
    }
    validate_workflow_record(WORKFLOW_VALIDATION_REPORT_SCHEMA, report)
    return WorkflowValidationResult(report=report, definition=definition, compiled=compiled)


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
    previous_definition: Mapping[str, Any] | None = None,
) -> WorkflowDefinitionPayload:
    result = validate_workflow_definition_report(
        raw,
        registered_guards=registered_guards,
        registered_activities=registered_activities,
        limits=limits,
        previous_definition=previous_definition,
    )
    if not result.report["valid"] or result.definition is None or result.compiled is None:
        first = result.report["diagnostics"][0]
        message = str(first["message"])
        if str(first["code"]).startswith(("workflow.schema.", "workflow.semantic.")):
            message = f"invalid {WORKFLOW_FILENAME}: validation failed at {first['path']}: {message}"
        raise WorkflowArtifactError(message)
    return WorkflowDefinitionPayload(
        definition=copy.deepcopy(result.definition),
        compiled=result.compiled,
        definition_digest=str(result.report["definition_digest"]),
        raw_digest=str(result.report["raw_digest"]),
        validation_report=copy.deepcopy(result.report),
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
        validation_report=copy.deepcopy(payload.validation_report),
    )


__all__ = [
    "WORKFLOW_DEFINITION_SCHEMA",
    "WORKFLOW_FILENAME",
    "WorkflowArtifactError",
    "WorkflowArtifactLimits",
    "WorkflowDefinitionArtifact",
    "WorkflowDefinitionPayload",
    "WorkflowValidationResult",
    "canonical_workflow_bytes",
    "canonical_workflow_digest",
    "load_manifest_bound_workflow",
    "validate_workflow_definition_bytes",
    "validate_workflow_definition_report",
    "workflow_graph_diff",
    "workflow_manifest_reference",
]
