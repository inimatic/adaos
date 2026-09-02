from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from jsonschema import Draft7Validator

from adaos.sdk.scenarios.runtime import ActionRegistry, ScenarioModel, ScenarioRuntime, default_registry
from adaos.services.conversational_pipeline import compile_conversational_package
from adaos.services.workflow_artifacts import WorkflowArtifactError, load_manifest_bound_workflow


_SCENARIO_MANIFESTS = ("scenario.yaml",)
_SKILL_MANIFESTS = ("skill.yaml",)
_log = logging.getLogger("adaos.scenario.validation")


@dataclass(frozen=True, slots=True)
class ScenarioValidationIssue:
    level: str
    code: str
    message: str
    where: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioValidationReport:
    ok: bool
    scenario_id: str | None
    issues: tuple[ScenarioValidationIssue, ...]

    @property
    def errors(self) -> list[str]:
        return [issue.message for issue in self.issues if issue.level == "error"]


def _load_mapping(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw) or {}
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path.name} must contain an object")
    return dict(payload)


def _manifest_path(path: Path | str) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file():
        if candidate.name != "scenario.yaml":
            _log.error(
                "scenario rejected: unsupported declaration file path=%s required=scenario.yaml",
                str(candidate),
            )
            raise FileNotFoundError(f"scenario declaration must be scenario.yaml, got {candidate.name}")
        return candidate
    for name in _SCENARIO_MANIFESTS:
        manifest = candidate / name
        if manifest.is_file():
            return manifest
    _log.error("scenario rejected: required declaration is missing path=%s required=scenario.yaml", str(candidate))
    raise FileNotFoundError(f"scenario manifest not found at {candidate}")


def _dependency_ids(payload: Mapping[str, Any]) -> list[str]:
    values: list[Any] = list(payload.get("depends") or [])
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), Mapping) else {}
    skills = runtime.get("skills") if isinstance(runtime.get("skills"), Mapping) else {}
    values.extend(skills.get("required") or [])
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _dependency_roots(manifest: Path, roots: Iterable[Path | str]) -> list[Path]:
    candidates = [Path(root).expanduser().resolve() for root in roots]
    scenario_root = manifest.parent
    if scenario_root.parent.name == "scenarios":
        candidates.insert(0, scenario_root.parent.parent / "skills")
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _skill_tools(skill_root: Path) -> set[str]:
    manifest = next((skill_root / name for name in _SKILL_MANIFESTS if (skill_root / name).is_file()), None)
    if manifest is None:
        return set()
    payload = _load_mapping(manifest)
    tools: set[str] = set()
    for item in payload.get("tools") or []:
        if isinstance(item, Mapping) and str(item.get("name") or "").strip():
            tools.add(str(item["name"]).strip())
    exports = payload.get("exports") if isinstance(payload.get("exports"), Mapping) else {}
    for item in exports.get("tools") or []:
        name = str(item.get("name") if isinstance(item, Mapping) else item).strip()
        if name:
            tools.add(name)
    return tools


def _dependency_operation_catalog(
    dependency_ids: Iterable[str],
    roots: Iterable[Path],
) -> dict[str, tuple[str, ...]]:
    catalog: dict[str, tuple[str, ...]] = {}
    for dependency_id in dependency_ids:
        skill_root, _complete = _resolve_skill_root(dependency_id, roots)
        if skill_root is not None:
            catalog[dependency_id] = tuple(sorted(_skill_tools(skill_root)))
    return catalog


def _resolve_skill_root(dependency_id: str, roots: Iterable[Path]) -> tuple[Path | None, bool]:
    candidates = [root / dependency_id for root in roots if (root / dependency_id).is_dir()]
    for candidate in candidates:
        if _skill_tools(candidate):
            return candidate, True
    return (candidates[0], False) if candidates else (None, False)


def _skill_contract(skill_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest = next((skill_root / name for name in _SKILL_MANIFESTS if (skill_root / name).is_file()), None)
    if manifest is None:
        return {}, {}
    payload = _load_mapping(manifest)
    tools = {
        str(item.get("name") or "").strip(): dict(item)
        for item in payload.get("tools") or []
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    }
    routed_tools = {
        str(item.get("tool") or "").strip(): dict(item)
        for item in payload.get("data_routes") or []
        if isinstance(item, Mapping) and str(item.get("tool") or "").strip()
    }
    return tools, routed_tools


def _walk_objects(value: Any, path: str = "$") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield path, value
        for key, nested in value.items():
            yield from _walk_objects(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _walk_objects(nested, f"{path}[{index}]")


def _scenario_webui_contract_issues(
    manifest: Path,
    dependency_ids: Iterable[str],
    roots: Iterable[Path],
) -> list[ScenarioValidationIssue]:
    webui = manifest.parent / "webui.json"
    if not webui.is_file():
        return []
    try:
        payload = _load_mapping(webui)
    except Exception as exc:
        return [ScenarioValidationIssue("error", "scenario.webui.invalid", str(exc), "webui.json")]

    scenario_payload = _load_mapping(manifest)
    runtime_data_policy = (
        scenario_payload.get("runtime_data_policy")
        if isinstance(scenario_payload.get("runtime_data_policy"), Mapping)
        else {}
    )
    policy_level = "error" if str(runtime_data_policy.get("enforcement") or "").strip().lower() == "strict" else "warning"

    dependencies = set(dependency_ids)
    contracts: dict[str, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]] = {}
    for dependency_id in dependencies:
        skill_root, has_tools = _resolve_skill_root(dependency_id, roots)
        if skill_root is not None and has_tools:
            contracts[dependency_id] = _skill_contract(skill_root)

    issues: list[ScenarioValidationIssue] = []
    for path, item in _walk_objects(payload):
        data_source = item.get("dataSource")
        if isinstance(data_source, Mapping) and data_source.get("kind") == "skill":
            target = str(data_source.get("name") or "").strip()
            skill_id, separator, tool_name = target.partition(".")
            where = f"webui.json:{path}.dataSource"
            if not separator or not skill_id or not tool_name:
                issues.append(ScenarioValidationIssue("error", "scenario.webui.skill_target_invalid", f"invalid skill dataSource target: {target}", where))
                continue
            if skill_id not in dependencies:
                issues.append(ScenarioValidationIssue("error", "scenario.webui.skill_dependency_missing", f"skill dataSource '{target}' is not declared in scenario dependencies", where))
                continue
            tools, routed_tools = contracts.get(skill_id, ({}, {}))
            if tool_name not in tools:
                issues.append(ScenarioValidationIssue("error", "scenario.webui.skill_tool_unknown", f"skill dataSource references unknown tool '{target}'", where))
                continue
            side_effects = str(tools[tool_name].get("side_effects") or "none").strip().lower()
            if side_effects not in {"", "none", "read_only"}:
                issues.append(ScenarioValidationIssue("error", "scenario.webui.mutation_data_source", f"mutating tool '{target}' cannot be a dataSource", where))
            if tool_name not in routed_tools:
                issues.append(ScenarioValidationIssue(policy_level, "scenario.webui.data_route_missing", f"skill dataSource '{target}' has no exact tool data_route", where))
            if data_source.get("cacheTtlMs") is None:
                issues.append(ScenarioValidationIssue("warning", "scenario.webui.cache_policy_implicit", f"stable skill dataSource '{target}' relies on the client default cache policy", where))
            tags = data_source.get("invalidationTags")
            if not isinstance(tags, list) or not any(str(tag or "").strip() for tag in tags):
                issues.append(ScenarioValidationIssue("warning", "scenario.webui.invalidation_tags_missing", f"skill dataSource '{target}' has no addressable invalidation tags", where))
            route = routed_tools.get(tool_name) or {}
            read_policy = route.get("read_policy") if isinstance(route.get("read_policy"), Mapping) else {}
            if route:
                declared_tags = {
                    str(tag or "").strip()
                    for tag in read_policy.get("invalidation_tags") or []
                    if str(tag or "").strip()
                }
                rendered_tags = {
                    str(tag or "").strip()
                    for tag in tags or []
                    if str(tag or "").strip()
                } if isinstance(tags, list) else set()
                if declared_tags != rendered_tags:
                    issues.append(ScenarioValidationIssue(
                        policy_level,
                        "scenario.webui.invalidation_tags_mismatch",
                        f"skill dataSource '{target}' invalidationTags must exactly match data_route read_policy",
                        where,
                    ))
                declared_preserve = read_policy.get("preserve_last_value")
                rendered_preserve = data_source.get("preserveLastValue")
                if rendered_preserve is None:
                    issues.append(ScenarioValidationIssue(policy_level, "scenario.webui.preserve_last_value_implicit", f"skill dataSource '{target}' does not execute the declared preserve_last_value policy", where))
                elif bool(rendered_preserve) != bool(declared_preserve):
                    issues.append(ScenarioValidationIssue(policy_level, "scenario.webui.preserve_last_value_mismatch", f"skill dataSource '{target}' preserveLastValue differs from data_route read_policy", where))
                declared_hz = read_policy.get("max_request_hz")
                rendered_hz = data_source.get("maxRequestHz")
                if rendered_hz is None:
                    issues.append(ScenarioValidationIssue(policy_level, "scenario.webui.max_request_hz_implicit", f"skill dataSource '{target}' does not execute the declared max_request_hz policy", where))
                elif declared_hz is not None and float(rendered_hz) != float(declared_hz):
                    issues.append(ScenarioValidationIssue(policy_level, "scenario.webui.max_request_hz_mismatch", f"skill dataSource '{target}' maxRequestHz differs from data_route read_policy", where))

        if item.get("type") == "callSkill":
            target = str(item.get("target") or "").strip()
            skill_id, separator, tool_name = target.partition(".")
            where = f"webui.json:{path}"
            if not separator or skill_id not in dependencies:
                continue
            tools, _routed_tools = contracts.get(skill_id, ({}, {}))
            tool = tools.get(tool_name)
            if tool is None:
                issues.append(ScenarioValidationIssue("error", "scenario.webui.action_tool_unknown", f"callSkill references unknown tool '{target}'", where))
                continue
            side_effects = str(tool.get("side_effects") or "none").strip().lower()
            invalidates = item.get("invalidates")
            if side_effects not in {"", "none", "read_only"} and (
                not isinstance(invalidates, list) or not any(str(tag or "").strip() for tag in invalidates)
            ):
                issues.append(ScenarioValidationIssue("warning", "scenario.webui.mutation_invalidation_missing", f"mutating action '{target}' has no addressable invalidates tags", where))
    return issues


def _registry_with_dependencies(
    dependency_ids: Iterable[str],
    dependency_roots: Iterable[Path],
    issues: list[ScenarioValidationIssue],
) -> ActionRegistry:
    registry = default_registry()
    roots = list(dependency_roots)
    for dependency_id in dependency_ids:
        skill_root, _has_tools = _resolve_skill_root(dependency_id, roots)
        if skill_root is None:
            issues.append(
                ScenarioValidationIssue(
                    "error",
                    "scenario.dependency.missing",
                    f"declared skill dependency '{dependency_id}' was not found",
                    "depends",
                )
            )
            continue
        tools = _skill_tools(skill_root)
        # A scenario dependency may own UI, events, or projected data without
        # exporting a callable tool. Exact route and Web UI references are
        # validated below, so a missing tool still fails at its use site.
        for tool in tools:
            registry.register(f"{dependency_id}.{tool}", lambda _args: None)
            registry.register(f"{dependency_id}:{tool}", lambda _args: None)
    return registry


def validate_scenario_path(
    path: Path | str,
    *,
    dependency_roots: Iterable[Path | str] = (),
) -> ScenarioValidationReport:
    issues: list[ScenarioValidationIssue] = []
    try:
        manifest = _manifest_path(path)
        payload = _load_mapping(manifest)
    except Exception as exc:
        return ScenarioValidationReport(
            False,
            None,
            (ScenarioValidationIssue("error", "scenario.manifest.invalid", str(exc), str(path)),),
        )

    schema_path = Path(__file__).resolve().parents[2] / "abi" / "scenario.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        for error in sorted(Draft7Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path)):
            where = ".".join(str(part) for part in error.absolute_path) or None
            issues.append(
                ScenarioValidationIssue(
                    "error",
                    "scenario.schema.invalid",
                    error.message,
                    where,
                )
            )
    except Exception as exc:
        issues.append(
            ScenarioValidationIssue(
                "error",
                "scenario.schema.unavailable",
                f"scenario schema validation failed: {type(exc).__name__}: {exc}",
                str(schema_path),
            )
        )

    try:
        load_manifest_bound_workflow(
            manifest.parent,
            manifest_name="scenario.yaml",
            allow_legacy_inline=True,
        )
    except WorkflowArtifactError as exc:
        issues.append(
            ScenarioValidationIssue(
                "error",
                "scenario.workflow.invalid",
                str(exc),
                "workflow.json",
            )
        )

    roots = _dependency_roots(manifest, dependency_roots)
    dependency_ids = _dependency_ids(payload)
    if isinstance(payload.get("conversational"), Mapping):
        conversational = compile_conversational_package(
            manifest.parent,
            manifest_name="scenario.yaml",
            operation_catalog=_dependency_operation_catalog(dependency_ids, roots),
        )
        issues.extend(
            ScenarioValidationIssue(
                str(item.get("severity") or "error"),
                str(item.get("code") or "conversational.invalid"),
                str(item.get("message") or "conversational package validation failed"),
                str(item.get("path") or "conversational"),
            )
            for item in conversational.validation.report.get("diagnostics") or []
        )

    scenario_id = str(payload.get("id") or manifest.parent.name).strip() or manifest.parent.name
    model = ScenarioModel.from_payload(payload, fallback_id=scenario_id)
    registry = _registry_with_dependencies(dependency_ids, roots, issues)
    for message in ScenarioRuntime(registry=registry).validate(model):
        code = "scenario.route.unknown" if message.startswith("unknown route") else "scenario.steps.invalid"
        issues.append(ScenarioValidationIssue("error", code, message, "steps"))
    issues.extend(_scenario_webui_contract_issues(manifest, dependency_ids, roots))

    return ScenarioValidationReport(
        not any(issue.level == "error" for issue in issues),
        scenario_id,
        tuple(issues),
    )


__all__ = [
    "ScenarioValidationIssue",
    "ScenarioValidationReport",
    "validate_scenario_path",
]
