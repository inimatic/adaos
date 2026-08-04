from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from jsonschema import Draft202012Validator

from adaos.services import conversation_interactions
from adaos.services.conversational_runtime import (
    action_policy_from_workflow_risk,
    build_conversation_output,
    build_noninvocation_intent_proposal,
    build_skill_intent_proposal,
    build_workflow_intent_proposal,
    conversation_output_from_workflow_execution,
    response_envelope_from_conversation_output,
    skill_invocation_from_intent_proposal,
    workflow_invocation_from_intent_proposal,
)
from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    WorkflowResolver,
    new_instance,
    validate_workflow_record,
    workflow_ref,
)
from adaos.services.workflow_artifacts import (
    WorkflowDefinitionArtifact,
    load_manifest_bound_workflow,
)


CONVERSATIONAL_DIR = "conversational"
CONVERSATIONAL_MANIFEST = "manifest.yaml"

CONVERSATION_OUTPUT_SCHEMA = "conversation.output.v1.schema.json"
PACKAGE_MANIFEST_SCHEMA = "conversational.package_manifest.v1.schema.json"
INPUT_SCHEMA = "conversational.input.v1.schema.json"
ENTITIES_SCHEMA = "conversational.entities.v1.schema.json"
EXAMPLES_SCHEMA = "conversational.examples.v1.schema.json"
MATCHERS_SCHEMA = "conversational.matchers.v1.schema.json"
AFFORDANCES_SCHEMA = "conversational.affordances.v1.schema.json"
REPAIR_SCHEMA = "conversational.repair.v1.schema.json"
OUTPUT_SOURCE_SCHEMA = "conversational.output.v1.schema.json"
STORY_SCHEMA = "conversational.story.v1.schema.json"
LOCALE_SCHEMA = "conversational.locale.v1.schema.json"
VALIDATION_REPORT_SCHEMA = "conversational.validation_report.v1.schema.json"

_BANNED_AFFORDANCE_WORKFLOW_KEYS = {
    "activity",
    "effect",
    "guards",
    "next_state",
    "source",
    "states",
    "target",
    "transitions",
}


class ConversationalArtifactError(ValueError):
    """Raised when a conversational package cannot be loaded safely."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConversationalArtifactError(f"YAML contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class ConversationalPackage:
    artifact_root: Path
    package_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    input_source: dict[str, Any]
    entities_source: dict[str, Any]
    examples_source: dict[str, Any]
    matchers_source: dict[str, Any]
    affordances_source: dict[str, Any]
    repair_source: dict[str, Any]
    output_source: dict[str, Any]
    stories: tuple[dict[str, Any], ...]
    story_paths: tuple[Path, ...]
    locale_sources: tuple[dict[str, Any], ...]
    locale_paths: tuple[Path, ...]
    workflow_artifact: WorkflowDefinitionArtifact | None
    package_digest: str


@dataclass(frozen=True, slots=True)
class ConversationalValidationResult:
    report: dict[str, Any]
    package: ConversationalPackage | None = None


def _sha256(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _canonical_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_sources(sources: Mapping[str, Any]) -> str:
    return _sha256(_canonical_bytes(sources))


def _abi_schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnostic(
    code: str,
    path: str,
    message: str,
    *,
    severity: str = "error",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
        "code": code,
        "severity": severity,
        "path": path,
        "message": message,
    }
    if details:
        value["details"] = dict(details)
    return value


def _schema_diagnostics(schema_name: str, value: Mapping[str, Any], path_prefix: str) -> list[dict[str, Any]]:
    validator = Draft202012Validator(_abi_schema(schema_name))
    errors = sorted(
        validator.iter_errors(dict(value)),
        key=lambda item: (list(item.absolute_path), item.validator, item.message),
    )
    diagnostics: list[dict[str, Any]] = []
    for error in errors:
        path = path_prefix + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}"
            for item in error.absolute_path
        )
        diagnostics.append(
            _diagnostic(
                f"conversational.schema.{str(error.validator).replace('$', 'ref')}",
                path,
                error.message,
                details={
                    "schema": schema_name,
                    "validator": str(error.validator),
                    "schema_path": "/".join(str(item) for item in error.absolute_schema_path),
                },
            )
        )
    return diagnostics


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    except ConversationalArtifactError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConversationalArtifactError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ConversationalArtifactError(f"{path} must contain a YAML object")
    return dict(value)


def _safe_rel(package_dir: Path, rel: str) -> Path:
    token = str(rel or "").strip()
    if not token:
        raise ConversationalArtifactError("empty package-relative path")
    raw = Path(token)
    if raw.is_absolute() or ".." in raw.parts:
        raise ConversationalArtifactError(f"unsafe package-relative path: {token}")
    resolved = (package_dir / raw).resolve()
    package_root = package_dir.resolve()
    if package_root not in (resolved, *resolved.parents):
        raise ConversationalArtifactError(f"path escapes conversational package: {token}")
    return resolved


def _load_source(
    package_dir: Path,
    rel: str,
    schema_name: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        path = _safe_rel(package_dir, rel)
    except ConversationalArtifactError as exc:
        diagnostics.append(_diagnostic("conversational.path.invalid", f"$.files.{rel}", str(exc)))
        return None
    if not path.is_file():
        diagnostics.append(
            _diagnostic(
                "conversational.file.missing",
                f"$.files.{rel}",
                f"referenced conversational source is missing: {rel}",
            )
        )
        return None
    try:
        value = _read_yaml_mapping(path)
    except ConversationalArtifactError as exc:
        diagnostics.append(_diagnostic("conversational.yaml.invalid", rel, str(exc)))
        return None
    diagnostics.extend(_schema_diagnostics(schema_name, value, rel))
    return value


def _id_index(values: Any, field: str = "id") -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in values:
        if isinstance(item, Mapping):
            token = str(item.get(field) or "").strip()
            if token:
                result[token] = dict(item)
    return result


def _duplicate_ids(values: Any, field: str = "id") -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, Mapping):
            continue
        token = str(item.get(field) or "").strip()
        if not token:
            continue
        if token in seen:
            duplicates.add(token)
        seen.add(token)
    return sorted(duplicates)


def _walk_keys(value: Any, prefix: str = "$") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            result.append((str(key), path))
            result.extend(_walk_keys(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_walk_keys(item, f"{prefix}[{index}]"))
    return result


def _transition_by_id(compiled: CompiledWorkflowDefinition) -> dict[str, Any]:
    return {transition.transition_id: transition for transition in compiled.transitions}


def _side_effect_matches(affordance_class: str, workflow_side_effect: str) -> bool:
    if workflow_side_effect == "none":
        return affordance_class in {"none", "read_only"}
    return affordance_class == workflow_side_effect


def _cross_check_package(
    *,
    manifest: Mapping[str, Any],
    input_source: Mapping[str, Any],
    entities_source: Mapping[str, Any],
    examples_source: Mapping[str, Any],
    matchers_source: Mapping[str, Any],
    affordances_source: Mapping[str, Any],
    repair_source: Mapping[str, Any],
    output_source: Mapping[str, Any],
    stories: Sequence[Mapping[str, Any]],
    story_paths: Sequence[Path],
    locale_sources: Sequence[Mapping[str, Any]],
    workflow_artifact: WorkflowDefinitionArtifact | None,
    operation_catalog: Mapping[str, Sequence[str]],
    require_operation_catalog: bool,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    package_id = str(manifest.get("package_id") or "").strip()
    for name, source in (
        ("input.yaml", input_source),
        ("entities.yaml", entities_source),
        ("examples.yaml", examples_source),
        ("matchers.yaml", matchers_source),
        ("affordances.yaml", affordances_source),
        ("repair.yaml", repair_source),
        ("output.yaml", output_source),
    ):
        source_package_id = str(source.get("package_id") or "").strip()
        if package_id and source_package_id and source_package_id != package_id:
            diagnostics.append(
                _diagnostic(
                    "conversational.package_id.mismatch",
                    name,
                    f"{name} package_id {source_package_id!r} does not match manifest {package_id!r}",
                )
            )

    for name, values in (
        ("input.yaml intents", input_source.get("intents")),
        ("entities.yaml entities", entities_source.get("entities")),
        ("examples.yaml examples", examples_source.get("examples")),
        ("examples.yaml hard_negatives", examples_source.get("hard_negatives")),
        ("matchers.yaml matchers", matchers_source.get("matchers")),
        ("affordances.yaml affordances", affordances_source.get("affordances")),
        ("repair.yaml policies", repair_source.get("policies")),
        ("output.yaml outputs", output_source.get("outputs")),
    ):
        for duplicate in _duplicate_ids(values):
            diagnostics.append(
                _diagnostic(
                    "conversational.id.duplicate",
                    name,
                    f"duplicate id in {name}: {duplicate}",
                )
            )

    affordances = _id_index(affordances_source.get("affordances"))
    intents = _id_index(input_source.get("intents"))
    entities = _id_index(entities_source.get("entities"))
    examples = _id_index(examples_source.get("examples"))
    matchers = _id_index(matchers_source.get("matchers"))
    outputs = _id_index(output_source.get("outputs"))
    output_actions = {
        str(action.get("action_id"))
        for output in outputs.values()
        for action in list(output.get("actions") or [])
        if isinstance(action, Mapping) and str(action.get("action_id") or "").strip()
    }
    compiled = workflow_artifact.compiled if workflow_artifact is not None else None
    transitions = _transition_by_id(compiled) if compiled is not None else {}
    commands = compiled.commands if compiled is not None else {}
    command_transitions: dict[str, list[Any]] = {}
    if compiled is not None:
        for transition in compiled.transitions:
            command_transitions.setdefault(transition.command, []).append(transition)

    workflow_refs = list(manifest.get("workflow_refs") or [])
    if workflow_refs and workflow_artifact is None:
        diagnostics.append(
            _diagnostic(
                "conversational.workflow.missing",
                "$.workflow_refs",
                "conversational package references a governed workflow but workflow.json is unavailable",
            )
        )
    else:
        if workflow_artifact is not None and not any(
            isinstance(ref, Mapping)
            and ref.get("workflow_type") == workflow_artifact.compiled.workflow_type
            for ref in workflow_refs
        ):
            diagnostics.append(
                _diagnostic(
                    "conversational.workflow_ref.missing",
                    "$.workflow_refs",
                    f"manifest does not reference workflow_type {workflow_artifact.compiled.workflow_type}",
                )
            )
        for index, ref in enumerate(workflow_refs if workflow_artifact is not None else []):
            if not isinstance(ref, Mapping):
                continue
            if ref.get("definition_digest") and ref.get("definition_digest") != workflow_artifact.definition_digest:
                diagnostics.append(
                    _diagnostic(
                        "conversational.workflow_ref.digest_mismatch",
                        f"$.workflow_refs[{index}].definition_digest",
                        "workflow definition digest does not match workflow.json",
                    )
                )

    for index, affordance in enumerate(list(affordances_source.get("affordances") or [])):
        if not isinstance(affordance, Mapping):
            continue
        affordance_id = str(affordance.get("id") or f"#{index}")
        for key, path in _walk_keys(affordance, f"affordances.yaml.affordances[{index}]"):
            if key in _BANNED_AFFORDANCE_WORKFLOW_KEYS:
                diagnostics.append(
                    _diagnostic(
                        "conversational.affordance.workflow_shape",
                        path,
                        f"affordance {affordance_id} must not define workflow key {key!r}",
                    )
                )
        for output_ref in list(affordance.get("output_refs") or []):
            if str(output_ref) not in outputs:
                diagnostics.append(
                    _diagnostic(
                        "conversational.affordance.output_ref_unknown",
                        f"affordances.yaml.affordances[{index}].output_refs",
                        f"affordance {affordance_id} references unknown output {output_ref}",
                    )
                )
        if affordance.get("kind") != "workflow_command":
            continue
        workflow = affordance.get("workflow")
        if not isinstance(workflow, Mapping):
            diagnostics.append(
                _diagnostic(
                    "conversational.affordance.workflow_required",
                    f"affordances.yaml.affordances[{index}].workflow",
                    f"workflow_command affordance {affordance_id} requires workflow command refs",
                )
            )
            continue
        if compiled is None:
            continue
        workflow_type = str(workflow.get("workflow_type") or "")
        command_id = str(workflow.get("command_id") or "")
        transition_id = str(workflow.get("transition_id") or "").strip()
        if workflow_type != compiled.workflow_type:
            diagnostics.append(
                _diagnostic(
                    "conversational.affordance.workflow_type_mismatch",
                    f"affordances.yaml.affordances[{index}].workflow.workflow_type",
                    f"affordance {affordance_id} targets {workflow_type}, expected {compiled.workflow_type}",
                )
            )
        if command_id not in commands:
            diagnostics.append(
                _diagnostic(
                    "conversational.affordance.command_unknown",
                    f"affordances.yaml.affordances[{index}].workflow.command_id",
                    f"affordance {affordance_id} references undeclared workflow command {command_id}",
                )
            )
            continue
        if command_id not in command_transitions:
            diagnostics.append(
                _diagnostic(
                    "conversational.affordance.command_has_no_transition",
                    f"affordances.yaml.affordances[{index}].workflow.command_id",
                    f"workflow command {command_id} is declared but has no transition",
                )
            )
        if transition_id:
            transition = transitions.get(transition_id)
            if transition is None:
                diagnostics.append(
                    _diagnostic(
                        "conversational.affordance.transition_unknown",
                        f"affordances.yaml.affordances[{index}].workflow.transition_id",
                        f"affordance {affordance_id} references unknown transition {transition_id}",
                    )
                )
            elif transition.command != command_id:
                diagnostics.append(
                    _diagnostic(
                        "conversational.affordance.transition_command_mismatch",
                        f"affordances.yaml.affordances[{index}].workflow",
                        f"transition {transition_id} uses command {transition.command}, not {command_id}",
                    )
                )
        for transition in command_transitions.get(command_id, []):
            required_caps = set(transition.descriptor["capability_requirements"].get("required") or [])
            declared_caps = set(affordance.get("required_capabilities") or [])
            missing_caps = sorted(required_caps - declared_caps)
            if missing_caps:
                diagnostics.append(
                    _diagnostic(
                        "conversational.affordance.capability_missing",
                        f"affordances.yaml.affordances[{index}].required_capabilities",
                        f"affordance {affordance_id} omits workflow-required capabilities: {', '.join(missing_caps)}",
                    )
                )
            workflow_risk = dict(transition.descriptor["risk"])
            affordance_policy = dict(affordance.get("action_policy") or {})
            workflow_side_effect = str(workflow_risk["side_effect"])
            affordance_class = str(affordance_policy.get("side_effect") or "")
            if not _side_effect_matches(affordance_class, workflow_side_effect):
                diagnostics.append(
                    _diagnostic(
                        "conversational.affordance.side_effect_mismatch",
                        f"affordances.yaml.affordances[{index}].action_policy.side_effect",
                        f"affordance {affordance_id} side effect {affordance_class!r} does not match workflow side_effect {workflow_side_effect!r}",
                    )
                )
            for key in ("risk_class", "confirmation"):
                workflow_key = "class" if key == "risk_class" else key
                if str(affordance_policy.get(key) or "") != str(workflow_risk.get(workflow_key) or ""):
                    diagnostics.append(
                        _diagnostic(
                            "conversational.affordance.action_policy_mismatch",
                            f"affordances.yaml.affordances[{index}].action_policy.{key}",
                            f"affordance {affordance_id} {key} does not match workflow risk contract",
                        )
                    )

    normalized_catalog = {
        str(skill_id): {str(operation) for operation in operations}
        for skill_id, operations in operation_catalog.items()
    }
    for index, affordance in enumerate(list(affordances_source.get("affordances") or [])):
        if not isinstance(affordance, Mapping) or affordance.get("kind") not in {"skill_invocation", "query"}:
            continue
        invocation = dict(affordance.get("skill_invocation") or {})
        skill_id = str(invocation.get("skill_id") or "")
        operation_id = str(invocation.get("operation_id") or "")
        if skill_id not in normalized_catalog and require_operation_catalog:
            diagnostics.append(
                _diagnostic(
                    "conversational.affordance.skill_unknown",
                    f"affordances.yaml.affordances[{index}].skill_invocation.skill_id",
                    f"affordance references skill without an admitted operation catalog: {skill_id}",
                )
            )
        elif skill_id in normalized_catalog and operation_id not in normalized_catalog[skill_id]:
            diagnostics.append(
                _diagnostic(
                    "conversational.affordance.operation_unknown",
                    f"affordances.yaml.affordances[{index}].skill_invocation.operation_id",
                    f"skill {skill_id} does not declare operation {operation_id}",
                )
            )

    for index, intent in enumerate(list(input_source.get("intents") or [])):
        if not isinstance(intent, Mapping):
            continue
        intent_id = str(intent.get("id") or f"#{index}")
        affordance_id = str(intent.get("affordance_id") or "").strip()
        if affordance_id and affordance_id not in affordances:
            diagnostics.append(
                _diagnostic(
                    "conversational.intent.affordance_unknown",
                    f"input.yaml.intents[{index}].affordance_id",
                    f"intent {intent_id} references unknown affordance {affordance_id}",
                )
            )
        workflow = intent.get("workflow")
        if isinstance(workflow, Mapping) and compiled is not None:
            command_id = str(workflow.get("command_id") or "")
            if command_id and command_id not in commands:
                diagnostics.append(
                    _diagnostic(
                        "conversational.intent.command_unknown",
                        f"input.yaml.intents[{index}].workflow.command_id",
                        f"intent {intent_id} references undeclared workflow command {command_id}",
                    )
                )
            if affordance_id and affordance_id in affordances:
                affordance_workflow = affordances[affordance_id].get("workflow")
                if isinstance(affordance_workflow, Mapping) and command_id != affordance_workflow.get("command_id"):
                    diagnostics.append(
                        _diagnostic(
                            "conversational.intent.affordance_command_mismatch",
                            f"input.yaml.intents[{index}].workflow.command_id",
                            f"intent {intent_id} command {command_id} does not match affordance {affordance_id}",
                        )
                    )
        skill_binding = intent.get("skill_invocation")
        if isinstance(skill_binding, Mapping) and affordance_id in affordances:
            if dict(affordances[affordance_id].get("skill_invocation") or {}) != dict(skill_binding):
                diagnostics.append(
                    _diagnostic(
                        "conversational.intent.affordance_operation_mismatch",
                        f"input.yaml.intents[{index}].skill_invocation",
                        f"intent {intent_id} skill operation does not match affordance {affordance_id}",
                    )
                )
        for example_id in list(intent.get("example_ids") or []):
            if str(example_id) not in examples:
                diagnostics.append(
                    _diagnostic(
                        "conversational.intent.example_unknown",
                        f"input.yaml.intents[{index}].example_ids",
                        f"intent {intent_id} references unknown example {example_id}",
                    )
                )
        for slot_index, slot in enumerate(list(intent.get("slots") or [])):
            if not isinstance(slot, Mapping):
                continue
            entity_id = str(slot.get("entity_type") or "").strip()
            if entity_id and entity_id not in entities:
                diagnostics.append(
                    _diagnostic(
                        "conversational.intent.entity_unknown",
                        f"input.yaml.intents[{index}].slots[{slot_index}].entity_type",
                        f"intent {intent_id} references unknown entity {entity_id}",
                    )
                )

    manifest_locales = {str(item) for item in list(manifest.get("locales") or [])}
    locale_ids: set[str] = set()
    for index, locale_source in enumerate(locale_sources):
        locale_id = str(locale_source.get("locale") or "")
        locale_ids.add(locale_id)
        source_package_id = str(locale_source.get("package_id") or "")
        if source_package_id and source_package_id != package_id:
            diagnostics.append(
                _diagnostic(
                    "conversational.package_id.mismatch",
                    f"locale[{index}]",
                    f"locale package_id {source_package_id!r} does not match manifest {package_id!r}",
                )
            )
    if locale_ids != manifest_locales:
        diagnostics.append(
            _diagnostic(
                "conversational.locale.coverage_mismatch",
                "manifest.yaml.locales",
                "locale files must cover every declared locale exactly",
                details={
                    "missing": sorted(manifest_locales - locale_ids),
                    "undeclared": sorted(locale_ids - manifest_locales),
                },
            )
        )

    for index, example in enumerate(list(examples_source.get("examples") or [])):
        if not isinstance(example, Mapping):
            continue
        example_id = str(example.get("id") or f"#{index}")
        intent_id = str(example.get("intent_id") or "")
        if intent_id not in intents:
            diagnostics.append(
                _diagnostic(
                    "conversational.example.intent_unknown",
                    f"examples.yaml.examples[{index}].intent_id",
                    f"example {example_id} references unknown intent {intent_id}",
                )
            )
        locale_id = str(example.get("locale") or "")
        if locale_id not in manifest_locales:
            diagnostics.append(
                _diagnostic(
                    "conversational.example.locale_unknown",
                    f"examples.yaml.examples[{index}].locale",
                    f"example {example_id} uses undeclared locale {locale_id}",
                )
            )
        for entity_index, annotation in enumerate(list(example.get("entities") or [])):
            if not isinstance(annotation, Mapping):
                continue
            entity_id = str(annotation.get("entity_id") or "")
            if entity_id not in entities:
                diagnostics.append(
                    _diagnostic(
                        "conversational.example.entity_unknown",
                        f"examples.yaml.examples[{index}].entities[{entity_index}].entity_id",
                        f"example {example_id} references unknown entity {entity_id}",
                    )
                )

    for index, matcher in enumerate(matchers.values()):
        matcher_id = str(matcher.get("id") or f"#{index}")
        intent_id = str(matcher.get("intent_id") or "")
        if intent_id not in intents:
            diagnostics.append(
                _diagnostic(
                    "conversational.matcher.intent_unknown",
                    f"matchers.yaml.matchers[{index}].intent_id",
                    f"matcher {matcher_id} references unknown intent {intent_id}",
                )
            )
        locale_id = str(matcher.get("locale") or "")
        if locale_id not in manifest_locales and locale_id != "und":
            diagnostics.append(
                _diagnostic(
                    "conversational.matcher.locale_unknown",
                    f"matchers.yaml.matchers[{index}].locale",
                    f"matcher {matcher_id} uses undeclared locale {locale_id}",
                )
            )
        if matcher.get("kind") == "regex":
            try:
                re.compile(str(matcher.get("pattern") or ""))
            except re.error as exc:
                diagnostics.append(
                    _diagnostic(
                        "conversational.matcher.regex_invalid",
                        f"matchers.yaml.matchers[{index}].pattern",
                        f"matcher {matcher_id} has invalid regex: {exc}",
                    )
                )

    for index, output in enumerate(list(output_source.get("outputs") or [])):
        if not isinstance(output, Mapping):
            continue
        output_id = str(output.get("id") or f"#{index}")
        for action in list(output.get("actions") or []):
            if not isinstance(action, Mapping):
                continue
            affordance_id = str(action.get("affordance_id") or "").strip()
            if affordance_id and affordance_id not in affordances:
                diagnostics.append(
                    _diagnostic(
                        "conversational.output.affordance_unknown",
                        f"output.yaml.outputs[{index}].actions",
                        f"output {output_id} action references unknown affordance {affordance_id}",
                    )
                )

    for index, policy in enumerate(list(repair_source.get("policies") or [])):
        if not isinstance(policy, Mapping):
            continue
        output_ref = str(policy.get("output_ref") or "")
        if output_ref not in outputs:
            diagnostics.append(
                _diagnostic(
                    "conversational.repair.output_ref_unknown",
                    f"repair.yaml.policies[{index}].output_ref",
                    f"repair policy references unknown output {output_ref}",
                )
            )

    repair_policy_ids = set(_id_index(repair_source.get("policies")))
    repair_policy_kinds = {
        str(policy.get("kind") or "")
        for policy in list(repair_source.get("policies") or [])
        if isinstance(policy, Mapping) and str(policy.get("kind") or "").strip()
    }

    for story_index, story in enumerate(stories):
        path_label = (
            str(story_paths[story_index].relative_to(story_paths[story_index].parents[2]))
            if story_index < len(story_paths)
            else f"story[{story_index}]"
        )
        story_kind = str(story.get("story_kind") or "")
        if story_kind == "workflow" and compiled is None:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.workflow_missing",
                    path_label,
                    "workflow story requires an admitted workflow definition",
                )
            )

        if story_kind == "workflow" and compiled is not None and story.get("workflow_type") != compiled.workflow_type:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.workflow_type_mismatch",
                    path_label,
                    f"story workflow_type {story.get('workflow_type')!r} does not match {compiled.workflow_type}",
                )
            )
        start = dict(story.get("start") or {})
        if story_kind == "workflow" and compiled is not None and str(start.get("state") or "") not in compiled.states:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.start_state_unknown",
                    f"{path_label}.start.state",
                    f"story starts from undeclared workflow state {start.get('state')!r}",
                )
            )
        for step_index, step in enumerate(list(story.get("steps") or [])):
            if not isinstance(step, Mapping):
                continue
            given = dict(step.get("given") or {})
            given_proposal = dict(given.get("proposal") or {})
            given_event = dict(given.get("event") or {})
            expect = dict(step.get("expect") or {})
            command_id = str(given_proposal.get("command") or given_event.get("command") or "").strip()
            if command_id and compiled is not None and command_id not in commands:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.command_unknown",
                        f"{path_label}.steps[{step_index}].given",
                        f"story references undeclared workflow command {command_id}",
                    )
                )
            transition_id = str(expect.get("transition_id") or "").strip()
            if transition_id and compiled is not None:
                transition = transitions.get(transition_id)
                if transition is None:
                    diagnostics.append(
                        _diagnostic(
                            "conversational.story.transition_unknown",
                            f"{path_label}.steps[{step_index}].expect.transition_id",
                            f"story references unknown transition {transition_id}",
                        )
                    )
                elif command_id and transition.command != command_id:
                    diagnostics.append(
                        _diagnostic(
                            "conversational.story.transition_command_mismatch",
                            f"{path_label}.steps[{step_index}].expect.transition_id",
                            f"transition {transition_id} uses command {transition.command}, not {command_id}",
                        )
                    )
            state = str(expect.get("state") or "").strip()
            if state and compiled is not None and state not in compiled.states:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.state_unknown",
                        f"{path_label}.steps[{step_index}].expect.state",
                        f"story expects undeclared workflow state {state}",
                    )
                )
            output = dict(expect.get("output") or {})
            output_ref = str(given.get("output_ref") or "").strip()
            if output_ref and output_ref not in outputs:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.output_ref_unknown",
                        f"{path_label}.steps[{step_index}].given.output_ref",
                        f"story references unknown output {output_ref}",
                    )
                )
            expected_output_ref = str(output.get("output_ref") or "").strip()
            if expected_output_ref and expected_output_ref not in outputs:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.output_ref_unknown",
                        f"{path_label}.steps[{step_index}].expect.output.output_ref",
                        f"story expects unknown output {expected_output_ref}",
                    )
                )
            repair_expect = expect.get("repair") if isinstance(expect.get("repair"), Mapping) else {}
            repair_reason = str(repair_expect.get("reason_code") or "").strip()
            if repair_reason and repair_reason not in repair_policy_ids | repair_policy_kinds:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.repair_policy_unknown",
                        f"{path_label}.steps[{step_index}].expect.repair.reason_code",
                        f"story references unknown repair policy or kind {repair_reason}",
                    )
                )
            if given_proposal.get("kind") == "skill_invocation":
                skill_id = str(given_proposal.get("skill_id") or "")
                operation_id = str(given_proposal.get("operation_id") or "")
                if skill_id not in normalized_catalog or operation_id not in normalized_catalog.get(skill_id, set()):
                    diagnostics.append(
                        _diagnostic(
                            "conversational.story.operation_unknown",
                            f"{path_label}.steps[{step_index}].given.proposal",
                            f"story invokes undeclared operation {skill_id}.{operation_id}",
                        )
                    )
            for action_id in list(output.get("actions") or []):
                if str(action_id) not in output_actions and str(action_id) not in affordances:
                    diagnostics.append(
                        _diagnostic(
                            "conversational.story.action_unknown",
                            f"{path_label}.steps[{step_index}].expect.output.actions",
                            f"story references unknown output action or affordance {action_id}",
                        )
                    )
    return diagnostics


def _catalog_output(
    *,
    output_ref: str,
    output_source: Mapping[str, Any],
    affordances_source: Mapping[str, Any],
    story_id: str,
    step_index: int,
    conversation_id: str,
    proposal: Mapping[str, Any] | None,
    instance: Mapping[str, Any] | None,
    command_id: str | None,
    workflow_event_id: str | None,
    package_id: str | None,
    package_digest: str | None,
) -> dict[str, Any]:
    template = _id_index(output_source.get("outputs")).get(output_ref)
    if template is None:
        raise ConversationalArtifactError(f"story output source is unknown: {output_ref}")
    affordances = _id_index(affordances_source.get("affordances"))
    workflow_ref_value = None
    if isinstance(instance, Mapping):
        workflow_ref_value = workflow_ref(
            "workflow",
            str(instance.get("instance_id") or ""),
            version=str(instance.get("definition_version") or "") or None,
            generation=int(instance.get("generation") or 0),
            digest=str(instance.get("definition_digest") or "") or None,
        )
    actions: list[dict[str, Any]] = []
    for action_source in list(template.get("actions") or []):
        if not isinstance(action_source, Mapping):
            continue
        affordance_id = str(action_source.get("affordance_id") or "").strip()
        affordance = affordances.get(affordance_id, {})
        policy = copy.deepcopy(
            dict(affordance.get("action_policy") or action_policy_from_workflow_risk("read"))
        )
        workflow_binding = dict(affordance.get("workflow") or {})
        skill_binding = dict(affordance.get("skill_invocation") or {})
        binding_kind = str(affordance.get("kind") or "none")
        actions.append(
            {
                "action_id": str(action_source.get("action_id") or ""),
                "label": str(action_source.get("label") or action_source.get("action_id") or ""),
                "command": str(workflow_binding.get("command_id") or "") or None,
                "risk_level": str(template.get("risk_level") or "none"),
                "target_refs": [workflow_ref_value] if workflow_ref_value else [],
                "requires_confirmation": policy.get("confirmation") != "none",
                "presentation_hint": "danger" if policy.get("risk_class") == "destructive" else "secondary",
                "binding": {
                    "kind": binding_kind if binding_kind in {"workflow_command", "skill_invocation", "query"} else "none",
                    "affordance_id": affordance_id or None,
                    "workflow_command": str(workflow_binding.get("command_id") or "") or None,
                    "skill_operation": str(skill_binding.get("operation_id") or "") or None,
                },
                "action_policy": policy,
            }
        )
    details = [
        {
            "label": str(item.get("label") or "detail"),
            "value": copy.deepcopy(item.get("value")),
            "sensitivity": "internal",
        }
        for item in list(template.get("details") or [])
        if isinstance(item, Mapping)
    ]
    return build_conversation_output(
        output_id=f"story:{story_id}:step:{step_index}:{output_ref}",
        conversation_id=conversation_id,
        kind=str(template.get("kind") or "result"),
        audience=str(template.get("audience") or "user"),
        risk_level=str(template.get("risk_level") or "none"),
        reason={
            "code": str(template.get("reason_code") or output_ref),
            "explanation": str(template.get("explanation") or template.get("summary") or "") or None,
            "retryable": str(template.get("kind") or "") in {"clarification", "confirmation", "repair"},
            "source": "conversation",
        },
        summary=str(template.get("summary") or ""),
        content_parts=[dict(item) for item in list(template.get("content_parts") or []) if isinstance(item, Mapping)],
        details=details,
        actions=actions,
        correlation={
            "turn_trace_id": f"story:{story_id}:turn:{step_index}",
            "intent_proposal_id": str((proposal or {}).get("proposal_id") or "") or None,
            "interaction_id": None,
            "workflow_ref": workflow_ref_value,
            "workflow_event_id": workflow_event_id,
            "command_id": command_id,
            "run_ref": None,
            "reply_route_ref": None,
        },
        next_expected_input={
            "kind": str(template.get("next_expected_input") or "none"),
            "interaction_id": None,
            "fields": [],
        },
        handoff_target=dict(template["handoff_target"]) if isinstance(template.get("handoff_target"), Mapping) else None,
        provenance={
            "source": "repair" if template.get("kind") == "repair" else "conversation",
            "package_ref": workflow_ref("artifact", f"conversational_package:{package_id}") if package_id else None,
            "package_digest": package_digest,
            "source_ref": workflow_ref("artifact", f"conversational_output:{output_ref}"),
            "source_digest": _sha256(_canonical_bytes(template)),
        },
        trace=dict((proposal or {}).get("trace") or {}) or None,
        metadata={"story_id": story_id, "source_output_ref": output_ref},
        now=f"2026-01-01T00:{step_index:02d}:30+00:00",
    )


def _story_projection_ref(
    instance: Mapping[str, Any],
    *,
    definition_version: str,
) -> dict[str, Any]:
    return workflow_ref(
        "workflow",
        str(instance["instance_id"]),
        version=definition_version,
        generation=int(instance["generation"]),
    )


def _expected_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def _story_interaction_projection(
    *,
    story_id: str,
    step_index: int,
    story_channel: str,
    workflow: CompiledWorkflowDefinition,
    resolver: WorkflowResolver,
    instance: Mapping[str, Any],
    actor_id: str,
    permissions: Sequence[str],
    roles: Sequence[str],
    expect: Mapping[str, Any],
    conversation_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    interaction_expect = dict(expect.get("interaction") or {})
    presentation_expect = dict(expect.get("presentation") or {})
    if not interaction_expect and not presentation_expect:
        return None, None, []

    diagnostics: list[dict[str, Any]] = []
    try:
        description = resolver.describe(
            workflow,
            instance,
            actor=actor_id,
            permissions=tuple(permissions),
            roles=tuple(roles),
        )
        interaction = conversation_interactions.interaction_from_workflow_description(
            description,
            conversation_id=conversation_id,
            owner="story.runner",
            interaction_id=f"interaction:{story_id}:{step_index}",
            workflow_ref=_story_projection_ref(
                instance,
                definition_version=workflow.definition_version,
            ),
            now=f"2026-01-01T00:{step_index:02d}:00+00:00",
            persist=False,
        )
    except Exception as exc:
        diagnostics.append(
            _diagnostic(
                "conversational.story.interaction_projection_failed",
                f"{story_id}.steps[{step_index}].expect.interaction",
                str(exc),
            )
        )
        return None, None, diagnostics

    expected_commands = _expected_list(interaction_expect.get("commands"))
    actual_commands = [str(item.get("command") or "") for item in interaction.get("actions") or []]
    if expected_commands and actual_commands != expected_commands:
        diagnostics.append(
            _diagnostic(
                "conversational.story.interaction_commands_mismatch",
                f"{story_id}.steps[{step_index}].expect.interaction.commands",
                "interaction commands do not match expected command identities",
                details={"expected": expected_commands, "actual": actual_commands},
            )
        )
    expected_generation = interaction_expect.get("expected_generation")
    if expected_generation is not None and any(
        int(item.get("expected_generation") if item.get("expected_generation") is not None else -1)
        != int(expected_generation)
        for item in interaction.get("actions") or []
    ):
        diagnostics.append(
            _diagnostic(
                "conversational.story.interaction_generation_mismatch",
                f"{story_id}.steps[{step_index}].expect.interaction.expected_generation",
                f"interaction actions do not target generation {expected_generation}",
            )
        )

    presentation: dict[str, Any] | None = None
    if presentation_expect:
        channel = str(presentation_expect.get("channel") or story_channel or "text")
        try:
            profile = conversation_interactions.standard_capability_profile(
                channel,
                persist=False,
            )
            presentation = conversation_interactions.negotiate_presentation(
                interaction,
                profile,
                persist=False,
                now=f"2026-01-01T00:{step_index:02d}:00+00:00",
            )
        except Exception as exc:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.presentation_projection_failed",
                    f"{story_id}.steps[{step_index}].expect.presentation",
                    str(exc),
                )
            )
            return interaction, None, diagnostics

        expected_mode = str(presentation_expect.get("mode") or "").strip()
        if expected_mode and presentation.get("mode") != expected_mode:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.presentation_mode_mismatch",
                    f"{story_id}.steps[{step_index}].expect.presentation.mode",
                    f"expected presentation mode {expected_mode}, got {presentation.get('mode')}",
                )
            )
        if "supported" in presentation_expect and bool(presentation.get("supported")) != bool(
            presentation_expect.get("supported")
        ):
            diagnostics.append(
                _diagnostic(
                    "conversational.story.presentation_supported_mismatch",
                    f"{story_id}.steps[{step_index}].expect.presentation.supported",
                    f"expected supported={presentation_expect.get('supported')}, got {presentation.get('supported')}",
                )
            )
        expected_reason = str(presentation_expect.get("reason_code") or "").strip()
        if expected_reason and presentation.get("reason_code") != expected_reason:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.presentation_reason_mismatch",
                    f"{story_id}.steps[{step_index}].expect.presentation.reason_code",
                    f"expected reason {expected_reason}, got {presentation.get('reason_code')}",
                )
            )
        expected_presentation_commands = _expected_list(presentation_expect.get("commands"))
        actual_presentation_commands = [
            str(item.get("command") or "") for item in presentation.get("actions") or []
        ]
        if expected_presentation_commands and actual_presentation_commands != expected_presentation_commands:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.presentation_commands_mismatch",
                    f"{story_id}.steps[{step_index}].expect.presentation.commands",
                    "presentation commands do not match expected command identities",
                    details={
                        "expected": expected_presentation_commands,
                        "actual": actual_presentation_commands,
                    },
                )
            )
        expected_equivalence = presentation_expect.get("semantic_equivalent")
        actual_equivalence = dict(presentation.get("plan") or {}).get("semantic_equivalent")
        if expected_equivalence is not None and bool(actual_equivalence) != bool(expected_equivalence):
            diagnostics.append(
                _diagnostic(
                    "conversational.story.presentation_equivalence_mismatch",
                    f"{story_id}.steps[{step_index}].expect.presentation.semantic_equivalent",
                    f"expected semantic_equivalent={expected_equivalence}, got {actual_equivalence}",
                )
            )

    return interaction, presentation, diagnostics


def _assert_story_repair(
    *,
    story_id: str,
    step_index: int,
    expect: Mapping[str, Any],
    output: Mapping[str, Any],
    command_id: str | None,
) -> list[dict[str, Any]]:
    repair_expect = dict(expect.get("repair") or {})
    if not repair_expect:
        return []
    diagnostics: list[dict[str, Any]] = []
    if command_id:
        diagnostics.append(
            _diagnostic(
                "conversational.story.repair_command_present",
                f"{story_id}.steps[{step_index}].expect.repair",
                "repair story step must not execute a workflow command",
            )
        )
    if output.get("kind") != "repair":
        diagnostics.append(
            _diagnostic(
                "conversational.story.repair_output_mismatch",
                f"{story_id}.steps[{step_index}].expect.repair.kind",
                f"expected repair output, got {output.get('kind')}",
            )
        )
    expected_next = str(repair_expect.get("next_expected_input") or "").strip()
    actual_next = str(dict(output.get("next_expected_input") or {}).get("kind") or "")
    if expected_next and actual_next != expected_next:
        diagnostics.append(
            _diagnostic(
                "conversational.story.repair_next_input_mismatch",
                f"{story_id}.steps[{step_index}].expect.repair.next_expected_input",
                f"expected next input {expected_next}, got {actual_next}",
            )
        )
    expected_reason = str(repair_expect.get("reason_code") or "").strip()
    actual_reason = str(dict(output.get("reason") or {}).get("code") or "")
    if expected_reason and actual_reason and actual_reason != expected_reason:
        diagnostics.append(
            _diagnostic(
                "conversational.story.repair_reason_mismatch",
                f"{story_id}.steps[{step_index}].expect.repair.reason_code",
                f"expected repair reason {expected_reason}, got {actual_reason}",
            )
        )
    return diagnostics


def _assert_story_value(
    diagnostics: list[dict[str, Any]],
    *,
    path: str,
    code: str,
    expected: Any,
    actual: Any,
) -> None:
    if expected is not None and expected != actual:
        diagnostics.append(_diagnostic(code, path, f"expected {expected!r}, got {actual!r}"))


def run_conversation_story(
    story: Mapping[str, Any],
    workflow: CompiledWorkflowDefinition | None = None,
    *,
    resolver: WorkflowResolver | None = None,
    output_source: Mapping[str, Any] | None = None,
    affordances_source: Mapping[str, Any] | None = None,
    package_id: str | None = None,
    package_digest: str | None = None,
) -> dict[str, Any]:
    """Run deterministic ABI records and compare assertions after execution."""

    resolver = resolver or WorkflowResolver()
    diagnostics: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    story_id = str(story.get("id") or "story")
    story_kind = str(story.get("story_kind") or "workflow")
    actor = dict(story.get("actor") or {})
    actor_id = str(actor.get("id") or "user:local")
    permissions = tuple(str(item) for item in list(actor.get("permissions") or []))
    roles = tuple(str(item) for item in list(actor.get("roles") or []))
    instance: dict[str, Any] | None = None
    if story_kind == "workflow":
        if workflow is None:
            return {
                "story_id": story_id,
                "valid": False,
                "steps": len(list(story.get("steps") or [])),
                "final_state": None,
                "diagnostics": [_diagnostic("conversational.story.workflow_missing", story_id, "workflow story has no workflow definition")],
                "timeline": [],
            }
        start = dict(story.get("start") or {})
        instance = new_instance(
            workflow,
            str(start.get("instance_id") or f"story:{story_id}"),
            context=dict(start.get("context") or {}),
            now="2026-01-01T00:00:00+00:00",
        )
        if str(start.get("state") or workflow.initial_state) in workflow.states:
            instance["state"] = str(start.get("state") or workflow.initial_state)
        instance["generation"] = int(start.get("generation") or 0)
    conversation_id = f"story:{story_id}"

    for index, raw_step in enumerate(list(story.get("steps") or [])):
        step = dict(raw_step or {})
        given = dict(step.get("given") or {})
        expect = dict(step.get("expect") or {})
        given_proposal = dict(given.get("proposal") or expect.get("proposal") or {})
        given_event = dict(given.get("event") or {})
        expected_proposal = dict(expect.get("proposal") or {})
        before_state = str(instance.get("state")) if instance is not None else None
        proposal: dict[str, Any] | None = None
        invocation: dict[str, Any] | None = None
        decision: dict[str, Any] | None = None
        activity_mock: dict[str, Any] | None = None
        workflow_event_id: str | None = None
        command_id = str(
            given_proposal.get("command") or given_event.get("command") or expect.get("command") or ""
        ).strip() or None
        timestamp = f"2026-01-01T00:{index:02d}:00+00:00"
        fixed_trace = {
            "trace_id": hashlib.sha256(f"{story_id}:{index}:trace".encode()).hexdigest()[:32],
            "span_id": hashlib.sha256(f"{story_id}:{index}:span".encode()).hexdigest()[:16],
            "parent_span_id": None,
            "traceparent": None,
        }
        input_context = {
            "channel": str(story.get("channel") or "test"),
            "modality": "event" if given_event else "text",
            "actor_ref": workflow_ref("principal", actor_id),
            "principal_ref": workflow_ref("principal", actor_id),
            "reply_route_ref": None,
            "context_ref": None,
        }
        provenance = {
            "source": "story",
            "package_ref": workflow_ref("artifact", f"conversational_package:{package_id}") if package_id else None,
            "package_digest": package_digest,
            "prompt_digest": None,
            "context_digest": _digest_sources({"given": given}),
        }
        proposal_kind = str(given_proposal.get("kind") or "")
        if proposal_kind == "workflow_command" and workflow is not None and instance is not None:
            policy = dict(given_proposal.get("action_policy") or action_policy_from_workflow_risk("read"))
            exact_ref = workflow_ref(
                "workflow",
                str(instance["instance_id"]),
                version=workflow.definition_version,
                generation=int(instance["generation"]),
                digest=str(instance.get("definition_digest") or "") or None,
            )
            proposal = build_workflow_intent_proposal(
                conversation_id=conversation_id,
                source_message_id=f"story:{story_id}:message:{index}",
                source_text=str(step.get("user") or command_id or "workflow command"),
                workflow_type=workflow.workflow_type,
                command_id=str(command_id or ""),
                instance_ref=exact_ref,
                input_value=dict(given_proposal.get("arguments") or {}),
                risk=str(policy.get("risk_class") or "read"),
                confirmation_required=policy.get("confirmation") != "none",
                confidence=float(given_proposal.get("confidence") or 0.0),
                locale=str(story.get("locale") or "en"),
                input_context=input_context,
                provenance=provenance,
                trace=fixed_trace,
                now=timestamp,
            )
            invocation = workflow_invocation_from_intent_proposal(
                proposal,
                actor_id=actor_id,
                idempotency_key=f"story:{story_id}:{index}:{command_id}",
                now=timestamp,
            )
            decision = resolver.apply(
                workflow,
                instance,
                str(command_id or ""),
                input_value=dict(invocation["command"]["input"]),
                actor=actor_id,
                permissions=permissions,
                roles=roles,
                expected_generation=int(invocation["command"]["expected_generation"]),
                idempotency_key=str(invocation["command"]["idempotency_key"]),
                now=timestamp,
            )
            instance = copy.deepcopy(decision["after"])
            event_records = [dict(item) for item in decision.get("event_records") or []]
            workflow_event_id = str(event_records[0].get("event_id") or "") if event_records else None
            if decision.get("accepted") and decision.get("activity"):
                activity_mock = {"mocked": True, "side_effect_isolated": True, "activity": copy.deepcopy(decision["activity"])}
        elif given_event and workflow is not None and instance is not None:
            decision = resolver.apply(
                workflow,
                instance,
                str(command_id or ""),
                input_value=dict(given_event.get("input") or {}),
                actor=actor_id,
                permissions=permissions,
                roles=roles,
                expected_generation=int(instance["generation"]),
                idempotency_key=f"story:event:{given_event.get('event_id')}",
                now=timestamp,
            )
            instance = copy.deepcopy(decision["after"])
            event_records = [dict(item) for item in decision.get("event_records") or []]
            workflow_event_id = str(event_records[0].get("event_id") or given_event.get("event_id") or "")
        elif proposal_kind == "skill_invocation":
            proposal = build_skill_intent_proposal(
                conversation_id=conversation_id,
                source_message_id=f"story:{story_id}:message:{index}",
                source_text=str(step.get("user") or "skill invocation"),
                skill_id=str(given_proposal.get("skill_id") or ""),
                operation_id=str(given_proposal.get("operation_id") or ""),
                arguments=dict(given_proposal.get("arguments") or {}),
                confidence=float(given_proposal.get("confidence") or 0.0),
                locale=str(story.get("locale") or "en"),
                action_policy=dict(given_proposal.get("action_policy") or action_policy_from_workflow_risk("read")),
                input_context=input_context,
                provenance=provenance,
                trace=fixed_trace,
                now=timestamp,
            )
            invocation = skill_invocation_from_intent_proposal(
                proposal,
                actor_id=actor_id,
                idempotency_key=f"story:{story_id}:{index}:skill",
                now=timestamp,
            )
            activity_mock = {
                "mocked": True,
                "side_effect_isolated": True,
                "activity": {
                    "skill_id": invocation["operation"]["skill_id"],
                    "operation_id": invocation["operation"]["operation_id"],
                    "result": copy.deepcopy(given.get("skill_result")),
                },
            }
        elif proposal_kind in {"question", "unrelated"}:
            proposal = build_noninvocation_intent_proposal(
                conversation_id=conversation_id,
                source_message_id=f"story:{story_id}:message:{index}",
                source_text=str(step.get("user") or proposal_kind),
                kind=proposal_kind,
                confidence=float(given_proposal.get("confidence") or 0.0),
                locale=str(story.get("locale") or "en"),
                input_context=input_context,
                provenance=provenance,
                trace=fixed_trace,
                now=timestamp,
            )

        if decision is not None:
            _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.reason_code", code="conversational.story.reason_mismatch", expected=expect.get("reason_code"), actual=decision.get("reason_code"))
            if expect.get("reason_code") is None and not decision.get("accepted"):
                diagnostics.append(_diagnostic("conversational.story.command_rejected", f"{story_id}.steps[{index}].given", f"command {command_id} was rejected: {decision.get('reason_code')}"))
            _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.transition_id", code="conversational.story.transition_mismatch", expected=expect.get("transition_id"), actual=decision.get("transition_id"))
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.state", code="conversational.story.state_mismatch", expected=expect.get("state"), actual=str(instance.get("state")) if instance is not None else None)
        actual_act = dict((proposal or {}).get("semantic_acts", [{}])[0]) if proposal else {}
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.proposal.kind", code="conversational.story.proposal_kind_mismatch", expected=expected_proposal.get("kind"), actual=actual_act.get("kind"))
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.proposal.command", code="conversational.story.proposal_command_mismatch", expected=expected_proposal.get("command"), actual=actual_act.get("command"))
        minimum_confidence = expected_proposal.get("confidence_at_least")
        if minimum_confidence is not None and float(actual_act.get("confidence") or 0.0) < float(minimum_confidence):
            diagnostics.append(_diagnostic("conversational.story.proposal_confidence_mismatch", f"{story_id}.steps[{index}].expect.proposal.confidence_at_least", f"expected confidence >= {minimum_confidence}, got {actual_act.get('confidence')}"))
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.command", code="conversational.story.command_mismatch", expected=expect.get("command"), actual=command_id)

        expected_output = dict(expect.get("output") or {})
        repair_expect = dict(expect.get("repair") or {})
        if (
            expected_output.get("kind") == "repair"
            and "reason_code" not in expected_output
            and repair_expect.get("reason_code") is not None
        ):
            expected_output["reason_code"] = repair_expect.get("reason_code")
        output_ref = str(given.get("output_ref") or "").strip()
        if output_ref:
            output = _catalog_output(
                output_ref=output_ref,
                output_source=output_source or {},
                affordances_source=affordances_source or {},
                story_id=story_id,
                step_index=index,
                conversation_id=conversation_id,
                proposal=proposal,
                instance=instance,
                command_id=command_id,
                workflow_event_id=workflow_event_id,
                package_id=package_id,
                package_digest=package_digest,
            )
        elif decision is not None and proposal is not None and invocation is not None:
            output = conversation_output_from_workflow_execution(
                {"accepted": decision.get("accepted"), "status": decision.get("status"), "reason_code": decision.get("reason_code"), "invocation": invocation, "decision": decision, "commit": None, "responses": []},
                now=f"2026-01-01T00:{index:02d}:30+00:00",
            )
        else:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.output_source_missing",
                    f"{story_id}.steps[{index}].given.output_ref",
                    "step requires a catalog output_ref or an executed workflow result",
                )
            )
            continue
        envelope = response_envelope_from_conversation_output(output, sequence=index + 1, now=f"2026-01-01T00:{index:02d}:45+00:00")
        actual_action_ids = [str(item.get("action_id")) for item in output.get("actions") or []]
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.output.kind", code="conversational.story.output_kind_mismatch", expected=expected_output.get("kind"), actual=output.get("kind"))
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.output.output_ref", code="conversational.story.output_ref_mismatch", expected=expected_output.get("output_ref"), actual=dict(output.get("metadata") or {}).get("source_output_ref"))
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.output.summary", code="conversational.story.output_summary_mismatch", expected=expected_output.get("summary"), actual=output.get("summary"))
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.output.actions", code="conversational.story.output_actions_mismatch", expected=expected_output.get("actions"), actual=actual_action_ids)
        _assert_story_value(diagnostics, path=f"{story_id}.steps[{index}].expect.output.next_expected_input", code="conversational.story.next_input_mismatch", expected=expected_output.get("next_expected_input"), actual=dict(output.get("next_expected_input") or {}).get("kind"))
        diagnostics.extend(
            _assert_story_repair(
                story_id=story_id,
                step_index=index,
                expect=expect,
                output=output,
                command_id=command_id,
            )
        )
        interaction = presentation = None
        if workflow is not None and instance is not None:
            interaction, presentation, projection_diagnostics = _story_interaction_projection(
                story_id=story_id,
                step_index=index,
                story_channel=str(story.get("channel") or "text"),
                workflow=workflow,
                resolver=resolver,
                instance=instance,
                actor_id=actor_id,
                permissions=permissions,
                roles=roles,
                expect=expect,
                conversation_id=conversation_id,
            )
            diagnostics.extend(projection_diagnostics)
        timeline.append(
            {
                "step": index,
                "input_kind": "event" if given_event else "user",
                "proposal": proposal,
                "invocation": invocation,
                "event": copy.deepcopy(given_event) if given_event else None,
                "command": command_id,
                "before_state": before_state,
                "after_state": str(instance.get("state")) if instance is not None else None,
                "accepted": None if decision is None else bool(decision.get("accepted")),
                "reason_code": None if decision is None else decision.get("reason_code"),
                "transition_id": None if decision is None else decision.get("transition_id"),
                "activity": activity_mock,
                "output": output,
                "response_envelope": envelope,
                "interaction": interaction,
                "presentation": presentation,
            }
        )

    return {
        "story_id": story_id,
        "valid": not diagnostics,
        "steps": len(list(story.get("steps") or [])),
        "final_state": str(instance.get("state")) if instance is not None else None,
        "diagnostics": diagnostics,
        "timeline": timeline,
    }


def validate_conversational_package(
    artifact_root: Path | str,
    *,
    manifest_name: str,
    run_stories: bool = True,
    workflow_artifact: WorkflowDefinitionArtifact | None = None,
    operation_catalog: Mapping[str, Sequence[str]] | None = None,
    require_operation_catalog: bool = True,
) -> ConversationalValidationResult:
    root = Path(artifact_root).expanduser().resolve()
    package_dir = root / CONVERSATIONAL_DIR
    diagnostics: list[dict[str, Any]] = []
    story_reports: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    input_source: dict[str, Any] = {}
    entities_source: dict[str, Any] = {}
    examples_source: dict[str, Any] = {}
    matchers_source: dict[str, Any] = {}
    affordances_source: dict[str, Any] = {}
    repair_source: dict[str, Any] = {}
    output_source: dict[str, Any] = {}
    stories: list[dict[str, Any]] = []
    story_paths: list[Path] = []
    locale_sources: list[dict[str, Any]] = []
    locale_paths: list[Path] = []
    package_digest: str | None = None
    component_manifest: dict[str, Any] = {}

    if workflow_artifact is None:
        try:
            workflow_artifact = load_manifest_bound_workflow(
                root,
                manifest_name=manifest_name,
                allow_legacy_inline=False,
            )
        except Exception as exc:
            diagnostics.append(
                _diagnostic(
                    "conversational.workflow.invalid",
                    "../workflow.json",
                    str(exc),
                )
            )
            workflow_artifact = None

    component_manifest_path = root / manifest_name
    if not component_manifest_path.is_file():
        diagnostics.append(
            _diagnostic(
                "conversational.component_manifest.missing",
                manifest_name,
                f"component manifest is missing: {manifest_name}",
            )
        )
    else:
        try:
            component_manifest = _read_yaml_mapping(component_manifest_path)
        except ConversationalArtifactError as exc:
            diagnostics.append(
                _diagnostic(
                    "conversational.component_manifest.invalid",
                    manifest_name,
                    str(exc),
                )
            )
        else:
            raw_reference = component_manifest.get("conversational")
            if not isinstance(raw_reference, Mapping):
                diagnostics.append(
                    _diagnostic(
                        "conversational.component_manifest.unbound",
                        f"{manifest_name}.conversational",
                        "component manifest must reference conversational/manifest.yaml",
                    )
                )
            elif str(raw_reference.get("manifest") or "").strip() != "conversational/manifest.yaml":
                diagnostics.append(
                    _diagnostic(
                        "conversational.component_manifest.bad_reference",
                        f"{manifest_name}.conversational.manifest",
                        "conversational.manifest must be exactly conversational/manifest.yaml",
                    )
                )

    manifest_path = package_dir / CONVERSATIONAL_MANIFEST
    if not manifest_path.is_file():
        diagnostics.append(
            _diagnostic(
                "conversational.manifest.missing",
                "conversational/manifest.yaml",
                "conversational package manifest is missing",
            )
        )
    else:
        try:
            manifest = _read_yaml_mapping(manifest_path)
        except ConversationalArtifactError as exc:
            diagnostics.append(_diagnostic("conversational.manifest.invalid", "conversational/manifest.yaml", str(exc)))
        else:
            diagnostics.extend(
                _schema_diagnostics(
                    PACKAGE_MANIFEST_SCHEMA,
                    manifest,
                    "conversational/manifest.yaml",
                )
            )

    files = dict(manifest.get("files") or {})
    source_specs = [
        ("input", str(files.get("input") or "input.yaml"), INPUT_SCHEMA),
        ("entities", str(files.get("entities") or "entities.yaml"), ENTITIES_SCHEMA),
        ("examples", str(files.get("examples") or "examples.yaml"), EXAMPLES_SCHEMA),
        ("affordances", str(files.get("affordances") or "affordances.yaml"), AFFORDANCES_SCHEMA),
        ("repair", str(files.get("repair") or "repair.yaml"), REPAIR_SCHEMA),
        ("output", str(files.get("output") or "output.yaml"), OUTPUT_SOURCE_SCHEMA),
    ]
    if files.get("matchers"):
        source_specs.insert(
            3,
            ("matchers", str(files["matchers"]), MATCHERS_SCHEMA),
        )
    loaded: dict[str, dict[str, Any]] = {}
    for key, rel, schema_name in source_specs:
        value = _load_source(package_dir, rel, schema_name, diagnostics)
        if value is not None:
            loaded[key] = value
    input_source = loaded.get("input", {})
    entities_source = loaded.get("entities", {})
    examples_source = loaded.get("examples", {})
    matchers_source = loaded.get("matchers", {})
    affordances_source = loaded.get("affordances", {})
    repair_source = loaded.get("repair", {})
    output_source = loaded.get("output", {})

    for rel in list(files.get("locales") or []):
        try:
            path = _safe_rel(package_dir, str(rel))
        except ConversationalArtifactError as exc:
            diagnostics.append(_diagnostic("conversational.locale.path_invalid", str(rel), str(exc)))
            continue
        if not path.is_file():
            diagnostics.append(
                _diagnostic(
                    "conversational.locale.missing",
                    str(rel),
                    f"referenced locale source is missing: {rel}",
                )
            )
            continue
        try:
            source = _read_yaml_mapping(path)
        except ConversationalArtifactError as exc:
            diagnostics.append(_diagnostic("conversational.locale.invalid_yaml", str(rel), str(exc)))
            continue
        diagnostics.extend(_schema_diagnostics(LOCALE_SCHEMA, source, str(rel)))
        locale_sources.append(source)
        locale_paths.append(path)

    for rel in list(files.get("stories") or []):
        try:
            path = _safe_rel(package_dir, str(rel))
        except ConversationalArtifactError as exc:
            diagnostics.append(_diagnostic("conversational.story.path_invalid", str(rel), str(exc)))
            continue
        if not path.is_file():
            diagnostics.append(
                _diagnostic(
                    "conversational.story.missing",
                    str(rel),
                    f"referenced story is missing: {rel}",
                )
            )
            continue
        try:
            story = _read_yaml_mapping(path)
        except ConversationalArtifactError as exc:
            diagnostics.append(_diagnostic("conversational.story.invalid_yaml", str(rel), str(exc)))
            continue
        diagnostics.extend(_schema_diagnostics(STORY_SCHEMA, story, str(rel)))
        stories.append(story)
        story_paths.append(path)

    if package_dir.is_dir() and manifest:
        listed = {
            CONVERSATIONAL_MANIFEST,
            str(files.get("input") or "input.yaml"),
            str(files.get("entities") or "entities.yaml"),
            str(files.get("examples") or "examples.yaml"),
            *(str(files["matchers"]) for _ in (0,) if files.get("matchers")),
            str(files.get("affordances") or "affordances.yaml"),
            str(files.get("repair") or "repair.yaml"),
            str(files.get("output") or "output.yaml"),
            *(str(item) for item in list(files.get("stories") or [])),
            *(str(item) for item in list(files.get("locales") or [])),
        }
        for path in sorted(package_dir.rglob("*.yaml")) + sorted(package_dir.rglob("*.yml")):
            rel = path.relative_to(package_dir).as_posix()
            if rel not in listed:
                diagnostics.append(
                    _diagnostic(
                        "conversational.file.unreferenced",
                        rel,
                        f"conversational source is not listed in manifest.yaml: {rel}",
                    )
                )

    admitted_operation_catalog: dict[str, Sequence[str]] = dict(operation_catalog or {})
    if manifest_name == "skill.yaml":
        skill_id = str(component_manifest.get("name") or root.name).strip()
        admitted_operation_catalog[skill_id] = tuple(
            str(item.get("name") or "").strip()
            for item in list(component_manifest.get("tools") or [])
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        )

    diagnostics.extend(
        _cross_check_package(
            manifest=manifest,
            input_source=input_source,
            entities_source=entities_source,
            examples_source=examples_source,
            matchers_source=matchers_source,
            affordances_source=affordances_source,
            repair_source=repair_source,
            output_source=output_source,
            stories=stories,
            story_paths=story_paths,
            locale_sources=locale_sources,
            workflow_artifact=workflow_artifact,
            operation_catalog=admitted_operation_catalog,
            require_operation_catalog=require_operation_catalog,
        )
    )

    if manifest:
        package_digest = _digest_sources(
            {
                "manifest": manifest,
                "input": input_source,
                "entities": entities_source,
                "examples": examples_source,
                "matchers": matchers_source,
                "affordances": affordances_source,
                "repair": repair_source,
                "output": output_source,
                "stories": stories,
                "locales": locale_sources,
            }
        )

    if run_stories:
        for story in stories:
            try:
                story_reports.append(
                    run_conversation_story(
                        story,
                        workflow_artifact.compiled if workflow_artifact is not None else None,
                        output_source=output_source,
                        affordances_source=affordances_source,
                        package_id=str(manifest.get("package_id") or "") or None,
                        package_digest=package_digest,
                    )
                )
            except Exception as exc:
                story_reports.append(
                    {
                        "story_id": str(story.get("id") or "story"),
                        "valid": False,
                        "steps": len(list(story.get("steps") or [])),
                        "final_state": None,
                        "diagnostics": [_diagnostic("conversational.story.execution_failed", str(story.get("id") or "story"), str(exc))],
                        "timeline": [],
                    }
                )
            diagnostics.extend(story_reports[-1]["diagnostics"])
    metrics = {
        "intents": len(list(input_source.get("intents") or [])),
        "entities": len(list(entities_source.get("entities") or [])),
        "examples": len(list(examples_source.get("examples") or [])),
        "matchers": len(list(matchers_source.get("matchers") or [])),
        "affordances": len(list(affordances_source.get("affordances") or [])),
        "repair_policies": len(list(repair_source.get("policies") or [])),
        "outputs": len(list(output_source.get("outputs") or [])),
        "stories": len(stories),
        "locales": len(locale_sources),
        "workflow_commands_referenced": len(
            {
                str(dict(item.get("workflow") or {}).get("command_id"))
                for item in list(affordances_source.get("affordances") or [])
                if isinstance(item, Mapping)
                and isinstance(item.get("workflow"), Mapping)
                and str(dict(item.get("workflow") or {}).get("command_id") or "").strip()
            }
        ),
        "workflow_transitions_referenced": len(
            {
                str(dict(item.get("workflow") or {}).get("transition_id"))
                for item in list(affordances_source.get("affordances") or [])
                if isinstance(item, Mapping)
                and isinstance(item.get("workflow"), Mapping)
                and str(dict(item.get("workflow") or {}).get("transition_id") or "").strip()
            }
        ),
    }
    report = {
        "schema": "adaos.conversational.validation_report.v1",
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "package_id": str(manifest.get("package_id") or "") or None,
        "package_digest": package_digest,
        "diagnostics": diagnostics[:1000],
        "metrics": metrics,
        "story_reports": story_reports,
    }
    validate_workflow_record("adaos.conversational.validation_report.v1", report)

    package: ConversationalPackage | None = None
    if report["valid"]:
        package = ConversationalPackage(
            artifact_root=root,
            package_dir=package_dir,
            manifest_path=manifest_path,
            manifest=copy.deepcopy(manifest),
            input_source=copy.deepcopy(input_source),
            entities_source=copy.deepcopy(entities_source),
            examples_source=copy.deepcopy(examples_source),
            matchers_source=copy.deepcopy(matchers_source),
            affordances_source=copy.deepcopy(affordances_source),
            repair_source=copy.deepcopy(repair_source),
            output_source=copy.deepcopy(output_source),
            stories=tuple(copy.deepcopy(item) for item in stories),
            story_paths=tuple(story_paths),
            locale_sources=tuple(copy.deepcopy(item) for item in locale_sources),
            locale_paths=tuple(locale_paths),
            workflow_artifact=workflow_artifact,
            package_digest=str(package_digest),
        )
    return ConversationalValidationResult(report=report, package=package)


__all__ = [
    "AFFORDANCES_SCHEMA",
    "CONVERSATION_OUTPUT_SCHEMA",
    "CONVERSATIONAL_DIR",
    "CONVERSATIONAL_MANIFEST",
    "ConversationalArtifactError",
    "ConversationalPackage",
    "ConversationalValidationResult",
    "INPUT_SCHEMA",
    "OUTPUT_SOURCE_SCHEMA",
    "PACKAGE_MANIFEST_SCHEMA",
    "REPAIR_SCHEMA",
    "STORY_SCHEMA",
    "VALIDATION_REPORT_SCHEMA",
    "run_conversation_story",
    "validate_conversational_package",
]
