from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from jsonschema import Draft7Validator

from adaos.sdk.scenarios.runtime import ActionRegistry, ScenarioModel, ScenarioRuntime, default_registry


_SCENARIO_MANIFESTS = ("scenario.json", "scenario.yaml", "scenario.yml")
_SKILL_MANIFESTS = ("skill.yaml", "skill.yml")


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
        return candidate
    for name in _SCENARIO_MANIFESTS:
        manifest = candidate / name
        if manifest.is_file():
            return manifest
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


def _registry_with_dependencies(
    dependency_ids: Iterable[str],
    dependency_roots: Iterable[Path],
    issues: list[ScenarioValidationIssue],
) -> ActionRegistry:
    registry = default_registry()
    roots = list(dependency_roots)
    for dependency_id in dependency_ids:
        skill_root = next((root / dependency_id for root in roots if (root / dependency_id).is_dir()), None)
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
        if not tools:
            issues.append(
                ScenarioValidationIssue(
                    "error",
                    "scenario.dependency.tools_missing",
                    f"declared skill dependency '{dependency_id}' exports no tools",
                    "depends",
                )
            )
            continue
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

    scenario_id = str(payload.get("id") or manifest.parent.name).strip() or manifest.parent.name
    model = ScenarioModel.from_payload(payload, fallback_id=scenario_id)
    roots = _dependency_roots(manifest, dependency_roots)
    registry = _registry_with_dependencies(_dependency_ids(payload), roots, issues)
    for message in ScenarioRuntime(registry=registry).validate(model):
        code = "scenario.route.unknown" if message.startswith("unknown route") else "scenario.steps.invalid"
        issues.append(ScenarioValidationIssue("error", code, message, "steps"))

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
