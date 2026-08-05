"""Stable Builder SDK ports for package-bound conversational development."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def emit_intent_proposal(
    artifact_root: Path | str,
    *,
    manifest_name: str,
    intent_id: str,
    source_text: str,
    source_message_id: str,
    conversation_id: str,
    locale: str,
    channel: str,
    modality: str = "text",
    slots: Mapping[str, Any] | None = None,
    workflow_instance_ref: Mapping[str, Any] | None = None,
    allowed_command_snapshot: Sequence[Mapping[str, Any]] = (),
    context_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    from adaos.services.conversational_ingress import mediate_package_intent

    return mediate_package_intent(
        artifact_root,
        manifest_name=manifest_name,
        intent_id=intent_id,
        source_text=source_text,
        source_message_id=source_message_id,
        conversation_id=conversation_id,
        locale=locale,
        channel=channel,
        modality=modality,
        slots=slots,
        workflow_instance_ref=workflow_instance_ref,
        allowed_command_snapshot=allowed_command_snapshot,
        context_ref=context_ref,
        reply_route_ref=reply_route_ref,
        confidence=confidence,
    )


def semantic_output(
    artifact_root: Path | str,
    *,
    manifest_name: str,
    output_ref: str,
    conversation_id: str,
    locale: str,
    correlation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from adaos.services.conversational_ingress import semantic_output_from_package

    return semantic_output_from_package(
        artifact_root,
        manifest_name=manifest_name,
        output_ref=output_ref,
        conversation_id=conversation_id,
        locale=locale,
        correlation=correlation,
    )


def promote_teacher_candidate(
    candidate_id: str,
    *,
    privacy_scope: str | None = None,
    actor: str = "builder",
) -> dict[str, Any]:
    from adaos.services.nlu.teacher_overlay_store import promote_candidate_to_builder_change

    return promote_candidate_to_builder_change(
        candidate_id,
        privacy_scope=privacy_scope,
        actor=actor,
    )


__all__ = ["emit_intent_proposal", "promote_teacher_candidate", "semantic_output"]
