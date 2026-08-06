"""One package-bound intent and semantic-output rail for every channel."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Sequence

from adaos.services.conversational_compiler import resolve_message
from adaos.services.conversational_pipeline import compile_conversational_package
from adaos.services.conversational_runtime import (
    action_policy_from_workflow_risk,
    build_conversation_output,
    build_noninvocation_intent_proposal,
    build_skill_intent_proposal,
    build_workflow_intent_proposal,
)
from adaos.services.governed_workflow import workflow_ref


class ConversationalIngressError(ValueError):
    pass


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _find(values: Any, id_: str) -> dict[str, Any]:
    return next(
        (
            copy.deepcopy(dict(item))
            for item in values or []
            if isinstance(item, Mapping) and str(item.get("id") or "") == id_
        ),
        {},
    )


def mediate_package_intent(
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
    """Compile, resolve and emit an IntentProposal without channel-specific policy."""

    result = compile_conversational_package(artifact_root, manifest_name=manifest_name)
    if not result.valid or result.package is None or result.runtime_bundle is None:
        raise ConversationalIngressError("conversational package is not admitted")
    intent = _find(result.package.input_source.get("intents"), str(intent_id))
    if not intent:
        raise ConversationalIngressError(f"intent is not declared by package: {intent_id}")
    affordance = _find(
        result.package.affordances_source.get("affordances"),
        str(intent.get("affordance_id") or ""),
    )
    provenance = {
        "source": "nlu",
        "package_ref": workflow_ref(
            "artifact",
            f"conversational_package:{result.runtime_bundle['package_ref']['id']}",
            version=result.runtime_bundle["package_ref"].get("version"),
            digest=result.runtime_bundle["source_digest"],
        ),
        "package_digest": result.runtime_bundle["source_digest"],
        "prompt_digest": None,
        "context_digest": None,
    }
    binding = _mapping(intent.get("workflow") or affordance.get("workflow"))
    if binding:
        if not isinstance(workflow_instance_ref, Mapping):
            raise ConversationalIngressError("workflow intent requires an exact workflow_instance_ref")
        risk = _mapping(affordance.get("action_policy"))
        return build_workflow_intent_proposal(
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            source_text=source_text,
            workflow_type=str(binding.get("workflow_type") or ""),
            command_id=str(binding.get("command_id") or ""),
            instance_ref=workflow_instance_ref,
            input_value=slots,
            context_ref=context_ref,
            reply_route_ref=reply_route_ref,
            risk=str(risk.get("risk_class") or "read"),
            confirmation_required=str(risk.get("confirmation") or "none") != "none",
            confidence=confidence,
            locale=locale,
            allowed_command_snapshot=allowed_command_snapshot,
            provenance=provenance,
            channel=channel,
            modality=modality,
        )
    skill = _mapping(intent.get("skill_invocation") or affordance.get("skill_invocation"))
    if skill:
        return build_skill_intent_proposal(
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            source_text=source_text,
            skill_id=str(skill.get("skill_id") or ""),
            operation_id=str(skill.get("operation_id") or ""),
            arguments=slots,
            confidence=confidence,
            locale=locale,
            provenance=provenance,
            action_policy=_mapping(affordance.get("action_policy")),
            channel=channel,
            modality=modality,
        )
    kind = "question" if str(intent.get("kind") or "") in {"query", "view"} else "unrelated"
    return build_noninvocation_intent_proposal(
        conversation_id=conversation_id,
        source_message_id=source_message_id,
        source_text=source_text,
        kind=kind,
        confidence=confidence,
        locale=locale,
        provenance=provenance,
    )


def semantic_output_from_package(
    artifact_root: Path | str,
    *,
    manifest_name: str,
    output_ref: str,
    conversation_id: str,
    locale: str,
    correlation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize localized semantic output before channel limits are applied."""

    result = compile_conversational_package(artifact_root, manifest_name=manifest_name)
    if not result.valid or result.package is None or result.runtime_bundle is None:
        raise ConversationalIngressError("conversational package is not admitted")
    template = _find(result.package.output_source.get("outputs"), str(output_ref))
    if not template:
        raise ConversationalIngressError(f"output is not declared by package: {output_ref}")
    bundle = result.runtime_bundle
    summary = resolve_message(
        bundle,
        str(template.get("summary_ref") or output_ref),
        locale=locale,
        fallback=str(template.get("summary") or ""),
    )
    actions: list[dict[str, Any]] = []
    for item in template.get("actions") or []:
        if not isinstance(item, Mapping):
            continue
        affordance = _find(result.package.affordances_source.get("affordances"), str(item.get("affordance_id") or ""))
        label = resolve_message(
            bundle,
            str(item.get("label_ref") or f"action.{item.get('action_id')}.label"),
            locale=locale,
            fallback=str(item.get("label") or item.get("action_id") or "Action"),
        )
        binding = _mapping(affordance.get("workflow"))
        skill_binding = _mapping(affordance.get("skill_invocation"))
        policy = _mapping(affordance.get("action_policy")) or action_policy_from_workflow_risk("read")
        actions.append(
            {
                "action_id": str(item.get("action_id") or ""),
                "label": label["text"],
                "command": str(binding.get("command_id") or "") or None,
                "risk_level": str(template.get("risk_level") or "none"),
                "target_refs": [],
                "requires_confirmation": str(policy.get("confirmation") or "none") != "none",
                "presentation_hint": "danger" if policy.get("risk_class") == "destructive" else "secondary",
                "binding": {
                    "kind": str(affordance.get("kind") or "none")
                    if str(affordance.get("kind") or "none") in {"workflow_command", "skill_invocation", "query"}
                    else "none",
                    "affordance_id": str(affordance.get("id") or "") or None,
                    "workflow_command": str(binding.get("command_id") or "") or None,
                    "skill_operation": str(skill_binding.get("operation_id") or "") or None,
                },
                "action_policy": policy,
            }
        )
    return build_conversation_output(
        output_id=f"package:{bundle['package_ref']['id']}:{output_ref}",
        conversation_id=conversation_id,
        kind=str(template.get("kind") or "result"),
        audience=str(template.get("audience") or "user"),
        risk_level=str(template.get("risk_level") or "none"),
        summary=summary["text"],
        content_parts=[dict(item) for item in template.get("content_parts") or [] if isinstance(item, Mapping)],
        details=[
            {"label": str(item.get("label") or "detail"), "value": str(item.get("value") or ""), "sensitivity": "internal"}
            for item in template.get("details") or []
            if isinstance(item, Mapping)
        ],
        actions=actions,
        correlation=correlation,
        next_expected_input={"kind": str(template.get("next_expected_input") or "none"), "interaction_id": None, "fields": []},
        handoff_target=_mapping(template.get("handoff_target")) or None,
        provenance={
            "source": "conversation",
            "package_ref": workflow_ref(
                "artifact",
                f"conversational_package:{bundle['package_ref']['id']}",
                version=bundle["package_ref"].get("version"),
                digest=bundle["source_digest"],
            ),
            "package_digest": bundle["source_digest"],
            "source_ref": workflow_ref("artifact", f"conversational_output:{output_ref}"),
            "source_digest": bundle["source_digest"],
        },
        metadata={
            "locale": summary["locale"],
            "requested_locale": locale,
            "summary_ref": summary["message_ref"],
            "catalog_digest": summary["catalog_digest"],
            "locale_fallback": summary["fallback"],
        },
    )


__all__ = [
    "ConversationalIngressError",
    "mediate_package_intent",
    "semantic_output_from_package",
]
