"""Typed public records for the governed human-decision SDK surface."""

from __future__ import annotations

from typing import Any, Literal, Required, TypedDict


class EntityRef(TypedDict, total=False):
    kind: Required[str]
    id: Required[str]
    version: str
    generation: int
    digest: str


class LocalizedText(TypedDict, total=False):
    key: Required[str]
    params: dict[str, str | int | float | bool | None]
    fallback: str


class SemanticMessage(TypedDict, total=False):
    schema: Required[Literal["adaos.conversation.semantic_message.v1"]]
    message_key: Required[str]
    params: dict[str, str | int | float | bool | None]
    visual: LocalizedText
    spoken: LocalizedText
    critical: bool


class LocaleContext(TypedDict, total=False):
    request_locale: str
    user_locale: str
    channel_locale: str
    timezone: str


class InteractionChoice(TypedDict, total=False):
    value: Required[str | int | float | bool]
    label: Required[str]
    description: str | None


class InteractionInputSpec(TypedDict, total=False):
    kind: Required[Literal["choice", "multi_choice", "confirmation", "text", "form"]]
    required_fields: list[str]
    choices: list[InteractionChoice]
    sensitive: bool


class InteractionActionSemantics(TypedDict, total=False):
    preset: str
    effect: Required[str]
    outcome: Required[str]
    preview_is_read_only: Required[bool]
    refusal_is_terminal: Required[bool]


class InteractionAction(TypedDict, total=False):
    action_id: Required[str]
    label: Required[str]
    command: Required[str]
    value: Required[str | int | float | bool]
    target_ref: EntityRef
    expected_generation: int
    risk: Literal["read", "write", "external", "destructive", "irreversible", "privileged", "publication", "release"]
    confirmation_required: bool
    assurance: Literal["voice_permitted", "trusted_interface_required", "step_up_required"]
    semantics: InteractionActionSemantics


class InteractionSpecification(TypedDict, total=False):
    interaction_id: str
    prompt: Required[str]
    prompt_ref: str
    prompt_message: SemanticMessage
    locale_context: LocaleContext
    input_spec: InteractionInputSpec
    actions: list[InteractionAction]
    required_capabilities: list[str]
    optional_capabilities: list[str]
    fallbacks: list[str]
    task_ref: EntityRef
    workflow_ref: EntityRef
    reply_route_ref: EntityRef
    expires_at: str
    content_retention_until_epoch: float
    audit_retention_until_epoch: float
    metadata: dict[str, Any]


class ChannelCapabilityProfile(TypedDict, total=False):
    profile_id: Required[str]
    profile_version: Required[int]
    transport: Required[str]
    client: Required[str]
    surface: Required[str]
    capabilities: Required[dict[str, bool]]
    limits: dict[str, int]


class ConversationInteraction(TypedDict, total=False):
    schema: Required[Literal["adaos.conversation.interaction.v1"]]
    interaction_id: Required[str]
    conversation_id: Required[str]
    generation: Required[int]
    owner: Required[str]
    prompt: Required[str]
    semantic_digest: Required[str]
    status: Required[str]
    input_spec: Required[InteractionInputSpec]
    actions: Required[list[InteractionAction]]
    expires_at: str | None
    workflow_ref: EntityRef | None


class InteractionPresentation(TypedDict, total=False):
    presentation_id: Required[str]
    interaction_id: Required[str]
    interaction_generation: Required[int]
    profile_id: Required[str]
    supported: Required[bool]
    mode: Required[str]
    prompt: Required[str]
    actions: Required[list[dict[str, Any]]]


class HumanDecisionRequestResult(TypedDict):
    ok: bool
    handle: dict[str, Any]
    interaction: ConversationInteraction
    presentation: InteractionPresentation
    materialization: dict[str, Any]


class InteractionResponseValues(TypedDict, total=False):
    action_id: str
    command: str
    value: str | int | float | bool
    choice: str | int | float | bool
    choices: list[str | int | float | bool]
    confirmed: bool
    text: str


class IntentProposalReference(TypedDict, total=False):
    schema: Required[Literal["adaos.intent.proposal.v1"]]
    proposal_id: Required[str]
    act_id: Required[str]
    modality: Required[Literal["text", "voice"]]
    dialog_binding: dict[str, Any] | None


class InteractionResponse(TypedDict, total=False):
    schema: Required[Literal["adaos.conversation.interaction_response.v1"]]
    response_id: Required[str]
    interaction_id: Required[str]
    interaction_generation: Required[int]
    actor_id: Required[str]
    source: Required[str]
    values: Required[InteractionResponseValues]
    status: Required[str]
    consumed_command: InteractionAction | None
    assurance_receipt: dict[str, Any] | None


class HumanDecisionResponseResult(TypedDict, total=False):
    interaction: Required[ConversationInteraction]
    response: Required[InteractionResponse]
    dispatch: dict[str, Any] | None
    duplicate: Required[bool]


class WorkflowDefinitionSpec(TypedDict, total=False):
    schema: Required[str]
    workflow_type: Required[str]
    definition_version: Required[str]
    initial_state: Required[str]
    states: Required[list[dict[str, Any]]]
    commands: Required[list[dict[str, Any]]]


class HumanDecisionContext(TypedDict, total=False):
    trace_id: str
    turn_trace_id: str
    webspace_id: str
    locale: str
    timezone: str


class HumanDecisionMetadata(TypedDict, total=False):
    source_message_ref: EntityRef
    turn_trace_id: str
    trace: dict[str, str | None]
    evidence_refs: list[EntityRef]


class WorkflowExecutionResult(TypedDict, total=False):
    invocation: Required[dict[str, Any]]
    instance: Required[dict[str, Any]]
    dispatch: Required[dict[str, Any]]
    effect_assertion: dict[str, Any] | None
    outcome_receipt: Required[dict[str, Any]]
    duplicate: bool


__all__ = [
    "ChannelCapabilityProfile",
    "ConversationInteraction",
    "EntityRef",
    "HumanDecisionContext",
    "HumanDecisionMetadata",
    "HumanDecisionRequestResult",
    "HumanDecisionResponseResult",
    "InteractionAction",
    "InteractionActionSemantics",
    "InteractionInputSpec",
    "InteractionResponse",
    "InteractionResponseValues",
    "InteractionSpecification",
    "IntentProposalReference",
    "LocaleContext",
    "SemanticMessage",
    "WorkflowDefinitionSpec",
    "WorkflowExecutionResult",
]
