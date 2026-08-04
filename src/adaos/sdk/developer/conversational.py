"""Public design-time SDK for conversational package validation and stories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from adaos.services.conversational_pipeline import compile_conversational_package

ArtifactKind = Literal["skill", "scenario"]


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


__all__ = ["ArtifactKind", "compile_package", "export_package"]
