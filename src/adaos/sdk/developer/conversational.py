"""Public design-time SDK for conversational package validation and stories."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import yaml

from adaos.services.conversational_pipeline import compile_conversational_package
from adaos.services.governed_workflow import validate_workflow_record

ArtifactKind = Literal["skill", "scenario"]

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_LOCALE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,39}$")
_VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$"
)


def _yaml_text(value: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(value), allow_unicode=True, sort_keys=False)


def _project_root(kind: ArtifactKind, project_id: str) -> Path:
    """Resolve one bounded DEV project through the developer workspace SDK."""

    from adaos.sdk.developer import projects

    return projects._root(kind, project_id)  # noqa: SLF001


def _project_operation_catalog(
    kind: ArtifactKind,
    project_id: str,
    supplied: Mapping[str, Sequence[str]] | None,
) -> dict[str, tuple[str, ...]]:
    """Build the admitted operation catalog for a DEV scenario's dependencies."""

    from adaos.sdk.developer import projects

    catalog = {
        str(skill_id): tuple(str(operation) for operation in operations)
        for skill_id, operations in dict(supplied or {}).items()
    }
    if kind != "scenario":
        return catalog

    root = _project_root(kind, project_id)
    component = yaml.safe_load((root / "scenario.yaml").read_text(encoding="utf-8-sig")) or {}
    if not isinstance(component, Mapping):
        return catalog

    def values(value: Any) -> list[Any]:
        return [value] if isinstance(value, str) else list(value or [])

    dependencies = values(component.get("depends"))
    runtime = component.get("runtime") if isinstance(component.get("runtime"), Mapping) else {}
    skills = runtime.get("skills") if isinstance(runtime.get("skills"), Mapping) else {}
    dependencies.extend(values(skills.get("required")))
    for dependency in sorted({str(item).strip() for item in dependencies if str(item).strip()}):
        try:
            skill_root = projects._root("skill", dependency)  # noqa: SLF001
            manifest = yaml.safe_load(
                (skill_root / "skill.yaml").read_text(encoding="utf-8-sig")
            ) or {}
        except (
            projects.DeveloperProjectError,
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            yaml.YAMLError,
        ):
            continue
        if not isinstance(manifest, Mapping):
            continue
        operations = {
            str(item.get("name") or "").strip()
            for item in list(manifest.get("tools") or [])
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        }
        exports = manifest.get("exports") if isinstance(manifest.get("exports"), Mapping) else {}
        operations.update(
            str(item.get("name") if isinstance(item, Mapping) else item).strip()
            for item in list(exports.get("tools") or [])
            if str(item.get("name") if isinstance(item, Mapping) else item).strip()
        )
        catalog.setdefault(dependency, tuple(sorted(operations)))
    return catalog


def scaffold_package(
    path: Path | str,
    *,
    kind: ArtifactKind,
    package_id: str | None = None,
    version: str | None = None,
    locales: Sequence[str] = ("en",),
    include_matchers: bool = True,
) -> dict[str, Any]:
    """Create a non-destructive conversational source skeleton and validate it."""

    if kind not in {"skill", "scenario"}:
        raise ValueError("kind must be 'skill' or 'scenario'")
    root = Path(path).expanduser().resolve()
    manifest_name = "skill.yaml" if kind == "skill" else "scenario.yaml"
    component_path = root / manifest_name
    if not component_path.is_file():
        raise FileNotFoundError(f"component manifest is missing: {component_path}")

    component_raw = component_path.read_bytes()
    component = yaml.safe_load(component_raw.decode("utf-8-sig")) or {}
    if not isinstance(component, Mapping):
        raise ValueError(f"{manifest_name} must contain a YAML object")
    component = dict(component)
    if "conversational" in component:
        raise FileExistsError(f"{manifest_name} already declares a conversational package")
    package_dir = root / "conversational"
    if package_dir.exists():
        raise FileExistsError(f"conversational package path already exists: {package_dir}")

    resolved_package_id = str(package_id or component.get("name") or component.get("id") or "").strip()
    resolved_version = str(version or component.get("version") or "0.1.0").strip()
    resolved_locales = tuple(str(item).strip() for item in locales)
    if not _ID_PATTERN.fullmatch(resolved_package_id):
        raise ValueError("package_id must satisfy the conversational ABI identifier format")
    if not _VERSION_PATTERN.fullmatch(resolved_version):
        raise ValueError("version must be semantic version text")
    if not resolved_locales or len(set(resolved_locales)) != len(resolved_locales):
        raise ValueError("locales must be a non-empty sequence without duplicates")
    invalid_locale = next(
        (locale for locale in resolved_locales if not _LOCALE_PATTERN.fullmatch(locale)),
        None,
    )
    if invalid_locale is not None:
        raise ValueError(f"invalid locale identifier: {invalid_locale!r}")

    workflow_refs: list[dict[str, Any]] = []
    workflow_binding = component.get("workflow")
    if isinstance(workflow_binding, Mapping) and workflow_binding.get("manifest") == "workflow.json":
        workflow_path = root / "workflow.json"
        if not workflow_path.is_file():
            raise FileNotFoundError(f"component references missing workflow: {workflow_path}")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
        if not isinstance(workflow, Mapping):
            raise ValueError("workflow.json must contain a JSON object")
        workflow_refs.append(
            {
                "workflow_type": str(workflow.get("workflow_type") or ""),
                "definition_ref": "../workflow.json",
                "definition_version": workflow.get("definition_version"),
                "definition_digest": None,
            }
        )

    files: dict[str, Any] = {
        "input": "input.yaml",
        "entities": "entities.yaml",
        "examples": "examples.yaml",
        "affordances": "affordances.yaml",
        "repair": "repair.yaml",
        "output": "output.yaml",
        "stories": [],
        "locales": [f"locale.{locale}.yaml" for locale in resolved_locales],
    }
    if include_matchers:
        files["matchers"] = "matchers.yaml"
    package_manifest = {
        "schema": "adaos.conversational.package_manifest.v1",
        "package_id": resolved_package_id,
        "package_kind": kind,
        "owner_ref": {"kind": kind, "id": resolved_package_id},
        "version": resolved_version,
        "workflow_refs": workflow_refs,
        "files": files,
        "locales": list(resolved_locales),
        "default_locale": resolved_locales[0],
        "privacy_defaults": {
            "source_scope": kind,
            "runtime_overlay_scope": "user",
            "public_promotion": "requires_review",
        },
        "compiled_outputs": [],
        "compatibility_aliases": [],
        "metadata": {"generated_by": "adaos.sdk.developer.conversational"},
    }
    sources: dict[str, Mapping[str, Any]] = {
        "manifest.yaml": package_manifest,
        "input.yaml": {
            "schema": "adaos.conversational.input.v1",
            "package_id": resolved_package_id,
            "intents": [],
            "policy": {
                "default_confidence": 0.8,
                "abstain_below": 0.5,
                "protected_action_confirmation": True,
            },
        },
        "entities.yaml": {
            "schema": "adaos.conversational.entities.v1",
            "package_id": resolved_package_id,
            "entities": [],
        },
        "examples.yaml": {
            "schema": "adaos.conversational.examples.v1",
            "package_id": resolved_package_id,
            "examples": [],
            "hard_negatives": [],
        },
        "affordances.yaml": {
            "schema": "adaos.conversational.affordances.v1",
            "package_id": resolved_package_id,
            "affordances": [],
        },
        "repair.yaml": {
            "schema": "adaos.conversational.repair.v1",
            "package_id": resolved_package_id,
            "policies": [],
        },
        "output.yaml": {
            "schema": "adaos.conversational.output.v1",
            "package_id": resolved_package_id,
            "outputs": [],
        },
    }
    if include_matchers:
        sources["matchers.yaml"] = {
            "schema": "adaos.conversational.matchers.v1",
            "package_id": resolved_package_id,
            "matchers": [],
        }
    for locale in resolved_locales:
        sources[f"locale.{locale}.yaml"] = {
            "schema": "adaos.conversational.locale.v1",
            "package_id": resolved_package_id,
            "locale": locale,
            "messages": {},
        }

    temporary_dir = root / f".conversational.tmp-{uuid.uuid4().hex}"
    temporary_manifest = component_path.with_suffix(f"{component_path.suffix}.tmp-{uuid.uuid4().hex}")
    generated_files: list[str] = []
    package_installed = False
    try:
        temporary_dir.mkdir()
        (temporary_dir / "tests" / "stories").mkdir(parents=True)
        for relative, payload in sources.items():
            destination = temporary_dir / relative
            destination.write_text(_yaml_text(payload), encoding="utf-8")
            generated_files.append(f"conversational/{relative}")
        temporary_dir.replace(package_dir)
        package_installed = True

        if component_path.read_bytes() != component_raw:
            raise RuntimeError(f"{manifest_name} changed while the package was being generated")
        component["conversational"] = {"manifest": "conversational/manifest.yaml"}
        temporary_manifest.write_text(_yaml_text(component), encoding="utf-8")
        temporary_manifest.replace(component_path)
    except Exception:
        temporary_manifest.unlink(missing_ok=True)
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        if package_installed and package_dir.exists():
            shutil.rmtree(package_dir)
        raise

    result = compile_package(root, kind=kind)
    return {
        **result,
        "generated_files": [manifest_name, *generated_files],
        "package_root": str(package_dir),
    }


def scaffold_project(
    kind: ArtifactKind,
    project_id: str,
    *,
    package_id: str | None = None,
    version: str | None = None,
    locales: Sequence[str] = ("en",),
    include_matchers: bool = True,
) -> dict[str, Any]:
    """Scaffold a package for a bounded DEV project selected by identity."""

    return scaffold_package(
        _project_root(kind, project_id),
        kind=kind,
        package_id=package_id,
        version=version,
        locales=locales,
        include_matchers=include_matchers,
    )


def compile_package(
    path: Path | str,
    *,
    kind: ArtifactKind,
    operation_catalog: Mapping[str, Sequence[str]] | None = None,
    run_stories: bool = True,
    build_static_report: bool = True,
) -> dict[str, Any]:
    """Validate sources, execute deterministic stories, and project static evidence."""

    if kind not in {"skill", "scenario"}:
        raise ValueError("kind must be 'skill' or 'scenario'")
    result = compile_conversational_package(
        path,
        manifest_name="skill.yaml" if kind == "skill" else "scenario.yaml",
        operation_catalog=operation_catalog,
        run_stories=run_stories,
        build_static_report=build_static_report,
    )
    return result.as_dict()


def compile_project(
    kind: ArtifactKind,
    project_id: str,
    *,
    operation_catalog: Mapping[str, Sequence[str]] | None = None,
    run_stories: bool = True,
    build_static_report: bool = True,
) -> dict[str, Any]:
    """Compile a bounded DEV project's conversational package by identity."""

    return compile_package(
        _project_root(kind, project_id),
        kind=kind,
        operation_catalog=_project_operation_catalog(kind, project_id, operation_catalog),
        run_stories=run_stories,
        build_static_report=build_static_report,
    )


def export_package(
    path: Path | str,
    *,
    kind: ArtifactKind,
    output_dir: Path | str,
    operation_catalog: Mapping[str, Sequence[str]] | None = None,
    run_stories: bool = True,
) -> dict[str, Any]:
    """Compile a package and materialize static review evidence."""

    result = compile_package(
        path,
        kind=kind,
        operation_catalog=operation_catalog,
        run_stories=run_stories,
        build_static_report=True,
    )
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}

    def write(name: str, content: str) -> None:
        destination = target / name
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(destination)
        artifacts[name] = str(destination)

    write(
        "conversational-validation.json",
        json.dumps(result["validation_report"], ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    if result["static_report"] is None:
        for name in ("workflow-static-report.json", "workflow-static-report.md"):
            (target / name).unlink(missing_ok=True)
    if result["static_report"] is not None:
        write(
            "workflow-static-report.json",
            json.dumps(result["static_report"], ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
    if result["static_markdown"] is not None:
        write("workflow-static-report.md", result["static_markdown"])
    return {**result, "artifacts": artifacts}


def export_learning_stories(
    path: Path | str,
    *,
    kind: ArtifactKind,
    output_dir: Path | str,
    privacy_review_ref: str,
    locales: Sequence[str] | None = None,
    operation_catalog: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Export reviewed story semantics without runtime/user transcript text.

    Learning export is intentionally distinct from validation evidence.  It is
    admitted only after the canonical package and every selected story pass,
    and requires an explicit privacy-review reference.  User utterances,
    actor ids, arguments, skill results, and runtime trace payloads are never
    copied into the export.
    """

    review_ref = str(privacy_review_ref or "").strip()
    if not review_ref:
        raise ValueError("learning story export requires privacy_review_ref")
    if kind not in {"skill", "scenario"}:
        raise ValueError("kind must be 'skill' or 'scenario'")
    pipeline = compile_conversational_package(
        path,
        manifest_name="skill.yaml" if kind == "skill" else "scenario.yaml",
        operation_catalog=operation_catalog,
        run_stories=True,
        build_static_report=True,
    )
    if not pipeline.valid or pipeline.package is None:
        raise ValueError("learning story export requires a valid conversational package")
    package = pipeline.package
    supported = tuple(str(item) for item in package.manifest.get("locales") or [])
    selected = tuple(dict.fromkeys(str(item).strip() for item in (locales or supported) if str(item).strip()))
    if not selected:
        raise ValueError("learning story export requires at least one locale")
    unknown = sorted(set(selected) - set(supported))
    if unknown:
        raise ValueError(f"learning story export locale is not admitted: {', '.join(unknown)}")
    reports = {
        str(item.get("story_id") or ""): item
        for item in pipeline.validation.report.get("story_reports") or []
        if isinstance(item, Mapping)
    }
    stories: list[dict[str, Any]] = []
    for source in package.stories:
        story_id = str(source.get("id") or "").strip()
        if str(source.get("locale") or "") not in selected:
            continue
        report = reports.get(story_id)
        if not report or report.get("valid") is not True:
            raise ValueError(f"learning story has not passed deterministic validation: {story_id}")
        projected_steps: list[dict[str, Any]] = []
        for index, step in enumerate(source.get("steps") or []):
            given = dict(step.get("given") or {}) if isinstance(step, Mapping) else {}
            expect = dict(step.get("expect") or {}) if isinstance(step, Mapping) else {}
            proposal = dict(given.get("proposal") or {}) if isinstance(given.get("proposal"), Mapping) else {}
            output = dict(expect.get("output") or {}) if isinstance(expect.get("output"), Mapping) else {}
            projected_steps.append(
                {
                    "sequence": index + 1,
                    "intent_id": str(proposal.get("intent_id") or "").strip() or None,
                    "command": str(expect.get("command") or proposal.get("command") or "").strip() or None,
                    "output_ref": str(output.get("output_ref") or given.get("output_ref") or "").strip() or None,
                    "expected_state": str(expect.get("state") or "").strip() or None,
                }
            )
        stories.append(
            {
                "story_id": story_id,
                "title": str(source.get("title") or story_id).strip(),
                "locale": str(source.get("locale") or ""),
                "channel": str(source.get("channel") or ""),
                "story_kind": str(source.get("story_kind") or ""),
                "steps": projected_steps,
            }
        )
    export = validate_workflow_record(
        "adaos.conversational.learning_export.v1",
        {
            "schema": "adaos.conversational.learning_export.v1",
            "package_id": str(package.manifest.get("package_id") or ""),
            "package_digest": package.package_digest,
            "privacy_review_ref": review_ref,
            "locales": list(selected),
            "content_policy": "authored_semantics_without_runtime_transcripts",
            "stories": stories,
        },
    )
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "conversational-learning.json"
    markdown_path = target / "conversational-learning.md"
    json_tmp = json_path.with_suffix(".json.tmp")
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    json_tmp.write_text(json.dumps(export, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    lines = [f"# {export['package_id']} conversational learning", "", f"Privacy review: `{review_ref}`", ""]
    for story in stories:
        lines.extend([f"## {story['title']}", "", f"Locale: `{story['locale']}` · Channel: `{story['channel']}`", ""])
        for step in story["steps"]:
            semantics = [
                value
                for value in (
                    f"intent `{step['intent_id']}`" if step["intent_id"] else None,
                    f"command `{step['command']}`" if step["command"] else None,
                    f"output `{step['output_ref']}`" if step["output_ref"] else None,
                    f"state `{step['expected_state']}`" if step["expected_state"] else None,
                )
                if value
            ]
            lines.append(f"{step['sequence']}. " + (" → ".join(semantics) or "semantic response"))
        lines.append("")
    markdown_tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    json_tmp.replace(json_path)
    markdown_tmp.replace(markdown_path)
    return {
        "export": export,
        "artifacts": {
            "conversational-learning.json": str(json_path),
            "conversational-learning.md": str(markdown_path),
        },
    }


__all__ = [
    "ArtifactKind",
    "compile_package",
    "compile_project",
    "export_learning_stories",
    "export_package",
    "scaffold_package",
    "scaffold_project",
]
