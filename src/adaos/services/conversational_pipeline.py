"""Shared design-time pipeline for conversational packages."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from adaos.services.conversational_artifacts import (
    ConversationalPackage,
    ConversationalValidationResult,
    validate_conversational_package,
)
from adaos.services.workflow_static_reports import (
    conversational_package_static_report,
    workflow_static_report_markdown,
)


@dataclass(frozen=True, slots=True)
class ConversationalPipelineResult:
    """Validation, deterministic stories, and optional workflow projection."""

    validation: ConversationalValidationResult
    static_report: dict[str, Any] | None
    static_markdown: str | None
    runtime_bundle: dict[str, Any] | None

    @property
    def valid(self) -> bool:
        return bool(self.validation.report.get("valid"))

    @property
    def package(self) -> ConversationalPackage | None:
        return self.validation.package

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "validation_report": copy.deepcopy(self.validation.report),
            "static_report": copy.deepcopy(self.static_report),
            "static_markdown": self.static_markdown,
            "runtime_bundle": copy.deepcopy(self.runtime_bundle),
        }


def compile_conversational_package(
    artifact_root: Path | str,
    *,
    manifest_name: str,
    operation_catalog: Mapping[str, Sequence[str]] | None = None,
    run_stories: bool = True,
    build_static_report: bool = True,
    require_operation_catalog: bool = True,
) -> ConversationalPipelineResult:
    """Run the canonical Builder/admission pipeline without provider calls."""

    validation = validate_conversational_package(
        artifact_root,
        manifest_name=manifest_name,
        operation_catalog=operation_catalog,
        run_stories=run_stories,
        require_operation_catalog=require_operation_catalog,
    )
    package = validation.package
    static_report = None
    static_markdown = None
    runtime_bundle = None
    if build_static_report and package is not None and package.workflow_artifact is not None:
        static_report = conversational_package_static_report(
            package,
            validation_result=validation,
        )
        static_markdown = workflow_static_report_markdown(static_report)
    if package is not None:
        from adaos.services.conversational_compiler import compile_runtime_bundle

        runtime_bundle = compile_runtime_bundle(package)
    return ConversationalPipelineResult(
        validation=validation,
        static_report=static_report,
        static_markdown=static_markdown,
        runtime_bundle=runtime_bundle,
    )


__all__ = [
    "ConversationalPipelineResult",
    "compile_conversational_package",
]
