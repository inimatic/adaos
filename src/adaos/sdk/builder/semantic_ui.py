"""Stable SDK facade for bounded Builder semantic UI operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.semantic_ui import BuilderSemanticUIService

    return BuilderSemanticUIService.from_context()


def apply(operation: Mapping[str, Any], *, execution_metrics: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if operation.get("schema") == "adaos.builder.semantic_graph_change.v1":
        from adaos.services.builder.semantic_graph import SemanticGraphService
        return dict(SemanticGraphService.from_context().apply(operation, execution_metrics=execution_metrics))
    if execution_metrics is not None:
        raise ValueError('Proposal measurements require a managed semantic graph operation')
    return dict(_service().apply(operation))


def replay(document: Mapping[str, Any], ops, *, write_refs) -> dict[str, Any]:
    """Pure replay of retained semantic operations; no model or Application effects."""
    from adaos.services.builder.semantic_graph import reduce_graph
    return reduce_graph(document, ops, write_refs=write_refs)


def verify_source(document: Mapping[str, Any], webui: Mapping[str, Any], *, project_ref: str) -> dict[str, Any]:
    from adaos.services.builder.semantic_graph import verify_baseline
    return verify_baseline(document, webui, project_ref=project_ref)


def recognizes_label_request(instruction: str) -> bool:
    from adaos.services.builder.semantic_label_requests import parse_request
    return parse_request(instruction) is not None


def prepare_label_request(*, project_ref: str, instruction: str, source_message_id: str,
                          expected_revision: str | None = None) -> dict[str, Any]:
    from adaos.services.builder.semantic_graph import SemanticGraphService
    return SemanticGraphService.from_context().prepare_label_request(
        project_ref=project_ref, instruction=instruction, source_message_id=source_message_id,
        expected_revision=expected_revision)


def recognizes_request(instruction: str) -> bool:
    from adaos.services.builder.semantic_edit_requests import parse_request
    return parse_request(instruction) is not None


def inspect(*, project_ref: str) -> dict[str, Any]:
    from adaos.services.builder.semantic_graph import SemanticGraphService
    return SemanticGraphService.from_context().inspect(project_ref=project_ref)


def record_turn_timing(*, meta: Mapping[str, Any], stage: str, timings: Mapping[str, Any]) -> dict[str, Any]:
    from adaos.services.turn_timing import record_turn_timing as record
    return record(meta=meta, stage=stage, timings=timings)


def admit(*, project_ref: str, instruction: str, source_message_id: str, write_refs=None,
          operation_kinds=('label.set', 'view.fields.reorder'), max_ops=1) -> dict[str, Any]:
    from adaos.services.builder.semantic_admission import SemanticAdmissionService
    return SemanticAdmissionService.from_context().begin(project_ref=project_ref, instruction=instruction,
        source_message_id=source_message_id, write_refs=write_refs, operation_kinds=operation_kinds, max_ops=max_ops)


def prepare_proposal(*, project_ref: str, admission_id: str, proposal: Mapping[str, Any]) -> dict[str, Any]:
    from adaos.services.builder.semantic_admission import SemanticAdmissionService
    return SemanticAdmissionService.from_context().prepare(project_ref=project_ref, admission_id=admission_id, proposal=proposal)


def propose(*, project_ref: str, admission_id: str, model: str, reasoning_effort='high',
            max_tokens=32768, timeout_s=300, output_mode='bounded_ops') -> dict[str, Any]:
    from adaos.services.builder.semantic_admission import SemanticAdmissionService
    from adaos.services.builder.semantic_proposer import SemanticProposer
    return SemanticProposer(SemanticAdmissionService.from_context()).propose(project_ref=project_ref,
        admission_id=admission_id, model=model, reasoning_effort=reasoning_effort, max_tokens=max_tokens,
        timeout_s=timeout_s, output_mode=output_mode)


def prepare_request(*, project_ref: str, instruction: str, source_message_id: str,
                    expected_revision: str | None = None) -> dict[str, Any]:
    from adaos.services.builder.semantic_graph import SemanticGraphService
    return SemanticGraphService.from_context().prepare_request(
        project_ref=project_ref, instruction=instruction, source_message_id=source_message_id,
        expected_revision=expected_revision)


__all__ = ["apply", "replay", "verify_source", "recognizes_label_request", "prepare_label_request",
           "recognizes_request", "prepare_request", "inspect", "record_turn_timing", "admit", "prepare_proposal", "propose"]
