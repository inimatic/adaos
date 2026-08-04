from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from jsonschema import Draft202012Validator

from adaos.services.conversational_runtime import build_conversation_output
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
AFFORDANCES_SCHEMA = "conversational.affordances.v1.schema.json"
REPAIR_SCHEMA = "conversational.repair.v1.schema.json"
OUTPUT_SOURCE_SCHEMA = "conversational.output.v1.schema.json"
STORY_SCHEMA = "conversational.story.v1.schema.json"
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
    affordances_source: dict[str, Any]
    repair_source: dict[str, Any]
    output_source: dict[str, Any]
    stories: tuple[dict[str, Any], ...]
    story_paths: tuple[Path, ...]
    workflow_artifact: WorkflowDefinitionArtifact
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
    affordances_source: Mapping[str, Any],
    repair_source: Mapping[str, Any],
    output_source: Mapping[str, Any],
    stories: Sequence[Mapping[str, Any]],
    story_paths: Sequence[Path],
    workflow_artifact: WorkflowDefinitionArtifact | None,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    package_id = str(manifest.get("package_id") or "").strip()
    for name, source in (
        ("input.yaml", input_source),
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

    if workflow_artifact is None:
        diagnostics.append(
            _diagnostic(
                "conversational.workflow.missing",
                "$.workflow_refs",
                "conversational package requires a manifest-bound governed workflow.json",
            )
        )
    else:
        workflow_refs = list(manifest.get("workflow_refs") or [])
        if not any(
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
        for index, ref in enumerate(workflow_refs):
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
            workflow_side_effect = str(transition.descriptor["risk"]["side_effect"])
            affordance_class = str(affordance.get("side_effect_class") or "")
            if not _side_effect_matches(affordance_class, workflow_side_effect):
                diagnostics.append(
                    _diagnostic(
                        "conversational.affordance.side_effect_mismatch",
                        f"affordances.yaml.affordances[{index}].side_effect_class",
                        f"affordance {affordance_id} side_effect_class {affordance_class!r} does not match workflow side_effect {workflow_side_effect!r}",
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

    for story_index, story in enumerate(stories):
        path_label = (
            str(story_paths[story_index].relative_to(story_paths[story_index].parents[2]))
            if story_index < len(story_paths)
            else f"story[{story_index}]"
        )
        if compiled is not None and story.get("workflow_type") != compiled.workflow_type:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.workflow_type_mismatch",
                    path_label,
                    f"story workflow_type {story.get('workflow_type')!r} does not match {compiled.workflow_type}",
                )
            )
        start = dict(story.get("start") or {})
        if compiled is not None and str(start.get("state") or "") not in compiled.states:
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
            expect = dict(step.get("expect") or {})
            proposal = dict(expect.get("proposal") or {})
            command_id = str(expect.get("command") or proposal.get("command") or "").strip()
            if command_id and compiled is not None and command_id not in commands:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.command_unknown",
                        f"{path_label}.steps[{step_index}].expect.command",
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
            output_ref = str(output.get("output_ref") or "").strip()
            if output_ref and output_ref not in outputs:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.output_ref_unknown",
                        f"{path_label}.steps[{step_index}].expect.output.output_ref",
                        f"story references unknown output {output_ref}",
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


def _conversation_output_from_story(
    *,
    story_id: str,
    step_index: int,
    conversation_id: str,
    output_spec: Mapping[str, Any],
    instance: Mapping[str, Any],
    workflow_type: str,
    definition_version: str,
    command_id: str | None,
    workflow_event_id: str | None,
) -> dict[str, Any]:
    kind = str(output_spec.get("kind") or "result")
    summary = str(output_spec.get("summary") or kind.replace("_", " "))
    actions = [
        {
            "action_id": str(action_id),
            "label": str(action_id).replace("_", " "),
            "command": None,
            "risk_level": "none",
            "target_refs": [],
            "requires_confirmation": False,
            "presentation_hint": "secondary",
        }
        for action_id in list(output_spec.get("actions") or [])
    ]
    return build_conversation_output(
        output_id=f"story:{story_id}:step:{step_index}",
        conversation_id=conversation_id,
        kind=kind,
        summary=summary,
        risk_level="none",
        actions=actions,
        correlation={
            "turn_trace_id": f"story:{story_id}:turn:{step_index}",
            "intent_proposal_id": None,
            "interaction_id": None,
            "workflow_ref": workflow_ref(
                "workflow",
                str(instance["instance_id"]),
                version=definition_version,
                generation=int(instance["generation"]),
            ),
            "workflow_event_id": workflow_event_id,
            "command_id": command_id,
            "run_ref": None,
            "reply_route_ref": None,
        },
        next_expected_input={
            "kind": str(output_spec.get("next_expected_input") or "none"),
            "interaction_id": None,
            "fields": [],
        },
        channel_constraints={
            "preferred": None,
            "fallbacks": [],
            "requires_rich_view": False,
        },
        response_envelope_ref=None,
        metadata={"workflow_type": workflow_type, "story_id": story_id},
        now="2026-01-01T00:00:00+00:00",
    )


def run_conversation_story(
    story: Mapping[str, Any],
    workflow: CompiledWorkflowDefinition,
    *,
    resolver: WorkflowResolver | None = None,
) -> dict[str, Any]:
    """Run a deterministic conversation story without LLM calls or live effects."""

    resolver = resolver or WorkflowResolver()
    diagnostics: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    story_id = str(story.get("id") or "story")
    actor = dict(story.get("actor") or {})
    actor_id = str(actor.get("id") or "user:local")
    permissions = tuple(str(item) for item in list(actor.get("permissions") or []))
    roles = tuple(str(item) for item in list(actor.get("roles") or []))
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
        expect = dict(step.get("expect") or {})
        proposal = dict(expect.get("proposal") or {})
        command_id = str(expect.get("command") or proposal.get("command") or "").strip()
        input_value = dict(proposal.get("arguments") or {})
        if "confirmed" not in input_value and command_id:
            input_value["confirmed"] = True
        before_state = str(instance.get("state"))
        decision: dict[str, Any] | None = None
        workflow_event_id: str | None = None
        activity_mock: dict[str, Any] | None = None
        if command_id:
            decision = resolver.apply(
                workflow,
                instance,
                command_id,
                input_value=input_value,
                actor=actor_id,
                permissions=permissions,
                roles=roles,
                expected_generation=int(instance["generation"]),
                idempotency_key=f"story:{story_id}:{index}:{command_id}",
                now=f"2026-01-01T00:{index:02d}:00+00:00",
            )
            instance = copy.deepcopy(decision["after"])
            event_records = list(decision.get("event_records") or [])
            if event_records:
                workflow_event_id = str(event_records[0].get("event_id") or "")
            if decision.get("accepted") and decision.get("activity"):
                activity_mock = {
                    "mocked": True,
                    "side_effect_isolated": True,
                    "activity": copy.deepcopy(decision["activity"]),
                }
            expected_reason = expect.get("reason_code")
            if expected_reason is not None and decision.get("reason_code") != expected_reason:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.reason_mismatch",
                        f"{story_id}.steps[{index}].expect.reason_code",
                        f"expected reason {expected_reason!r}, got {decision.get('reason_code')!r}",
                    )
                )
            elif expected_reason is None and not decision.get("accepted"):
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.command_rejected",
                        f"{story_id}.steps[{index}].expect.command",
                        f"command {command_id} was rejected: {decision.get('reason_code')}",
                    )
                )
            expected_transition = str(expect.get("transition_id") or "").strip()
            if expected_transition and decision.get("transition_id") != expected_transition:
                diagnostics.append(
                    _diagnostic(
                        "conversational.story.transition_mismatch",
                        f"{story_id}.steps[{index}].expect.transition_id",
                        f"expected transition {expected_transition}, got {decision.get('transition_id')}",
                    )
                )

        expected_state = str(expect.get("state") or "").strip()
        if expected_state and str(instance.get("state")) != expected_state:
            diagnostics.append(
                _diagnostic(
                    "conversational.story.state_mismatch",
                    f"{story_id}.steps[{index}].expect.state",
                    f"expected state {expected_state}, got {instance.get('state')}",
                )
            )
        output_spec = dict(expect.get("output") or {})
        output = _conversation_output_from_story(
            story_id=story_id,
            step_index=index,
            conversation_id=conversation_id,
            output_spec=output_spec,
            instance=instance,
            workflow_type=workflow.workflow_type,
            definition_version=workflow.definition_version,
            command_id=command_id or None,
            workflow_event_id=workflow_event_id,
        )
        output_errors = _schema_diagnostics(
            CONVERSATION_OUTPUT_SCHEMA,
            output,
            f"{story_id}.steps[{index}].output",
        )
        diagnostics.extend(output_errors)
        timeline.append(
            {
                "step": index,
                "user": step.get("user"),
                "event": copy.deepcopy(step.get("event")),
                "command": command_id or None,
                "before_state": before_state,
                "after_state": str(instance.get("state")),
                "accepted": None if decision is None else bool(decision.get("accepted")),
                "reason_code": None if decision is None else decision.get("reason_code"),
                "transition_id": None if decision is None else decision.get("transition_id"),
                "activity": activity_mock,
                "output": output,
            }
        )

    report = {
        "story_id": story_id,
        "valid": not diagnostics,
        "steps": len(list(story.get("steps") or [])),
        "final_state": str(instance.get("state")),
        "diagnostics": diagnostics,
        "timeline": timeline,
    }
    return report


def validate_conversational_package(
    artifact_root: Path | str,
    *,
    manifest_name: str,
    run_stories: bool = True,
    workflow_artifact: WorkflowDefinitionArtifact | None = None,
) -> ConversationalValidationResult:
    root = Path(artifact_root).expanduser().resolve()
    package_dir = root / CONVERSATIONAL_DIR
    diagnostics: list[dict[str, Any]] = []
    story_reports: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    input_source: dict[str, Any] = {}
    affordances_source: dict[str, Any] = {}
    repair_source: dict[str, Any] = {}
    output_source: dict[str, Any] = {}
    stories: list[dict[str, Any]] = []
    story_paths: list[Path] = []
    package_digest: str | None = None

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
    source_specs = (
        ("input", str(files.get("input") or "input.yaml"), INPUT_SCHEMA),
        ("affordances", str(files.get("affordances") or "affordances.yaml"), AFFORDANCES_SCHEMA),
        ("repair", str(files.get("repair") or "repair.yaml"), REPAIR_SCHEMA),
        ("output", str(files.get("output") or "output.yaml"), OUTPUT_SOURCE_SCHEMA),
    )
    loaded: dict[str, dict[str, Any]] = {}
    for key, rel, schema_name in source_specs:
        value = _load_source(package_dir, rel, schema_name, diagnostics)
        if value is not None:
            loaded[key] = value
    input_source = loaded.get("input", {})
    affordances_source = loaded.get("affordances", {})
    repair_source = loaded.get("repair", {})
    output_source = loaded.get("output", {})

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

    diagnostics.extend(
        _cross_check_package(
            manifest=manifest,
            input_source=input_source,
            affordances_source=affordances_source,
            repair_source=repair_source,
            output_source=output_source,
            stories=stories,
            story_paths=story_paths,
            workflow_artifact=workflow_artifact,
        )
    )

    if run_stories and workflow_artifact is not None:
        for story in stories:
            story_reports.append(run_conversation_story(story, workflow_artifact.compiled))
            diagnostics.extend(story_reports[-1]["diagnostics"])

    if manifest:
        package_digest = _digest_sources(
            {
                "manifest": manifest,
                "input": input_source,
                "affordances": affordances_source,
                "repair": repair_source,
                "output": output_source,
                "stories": stories,
            }
        )
    metrics = {
        "intents": len(list(input_source.get("intents") or [])),
        "affordances": len(list(affordances_source.get("affordances") or [])),
        "repair_policies": len(list(repair_source.get("policies") or [])),
        "outputs": len(list(output_source.get("outputs") or [])),
        "stories": len(stories),
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
    if report["valid"] and workflow_artifact is not None:
        package = ConversationalPackage(
            artifact_root=root,
            package_dir=package_dir,
            manifest_path=manifest_path,
            manifest=copy.deepcopy(manifest),
            input_source=copy.deepcopy(input_source),
            affordances_source=copy.deepcopy(affordances_source),
            repair_source=copy.deepcopy(repair_source),
            output_source=copy.deepcopy(output_source),
            stories=tuple(copy.deepcopy(item) for item in stories),
            story_paths=tuple(story_paths),
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
