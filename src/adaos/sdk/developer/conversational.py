"""Public design-time SDK for conversational package validation and stories."""

from __future__ import annotations

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


__all__ = ["ArtifactKind", "compile_package"]
