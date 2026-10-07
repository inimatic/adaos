"""Typed public records for the governed human-decision SDK surface."""

from __future__ import annotations

from typing import Any, Literal, Required, TypedDict


class EntityRef(TypedDict, total=False):
    kind: Required[str]
    id: Required[str]
    version: str
    generation: int
    digest: str


class SemanticParameter(TypedDict, total=False):
    type: Required[Literal["text", "number", "integer", "boolean", "timestamp", "date", "duration", "amount", "unit", "identifier"]]
    value: Required[str | int | float | bool]
    timezone: str
    unit: str
    currency: str
    precision: int


class MessageVariant(TypedDict, total=False):
    visual: Required[str]
    spoken: Required[str]
    plural_param: str
    visual_plural: dict[str, str]
    spoken_plural: dict[str, str]


class MessageCatalogRef(TypedDict):
    package_id: str
    package_version: str
    catalog_digest: str


class SemanticMessage(TypedDict, total=False):
    schema: Literal["adaos.conversation.semantic_message.v1"]
    key: Required[str]
    version: int
    params: dict[str, SemanticParameter]
    fallback: MessageVariant
    translations: dict[str, MessageVariant]
    catalog_ref: MessageCatalogRef | None
    source_locale: str
    critical: bool
    fallback_policy: Literal["allow", "require_locale"]


class LocaleContext(TypedDict, total=False):
    locale: str
    source_locale: str
    request_locale: str
    user_locale: str
    channel_locale: str
    timezone: str
    user_timezone: str


class InteractionChoice(TypedDict, total=False):
    value: Required[str]
    label: Required[str]
    description: str | None


class InteractionInputSpec(TypedDict, total=False):
    kind: Required[Literal["choice", "multi_choice", "confirmation", "text", "form"]]
    required_fields: list[str]
    choices: list[InteractionChoice]
    sensitive: bool


class InteractionActionSemantics(TypedDict, total=False):
    preset: str
    effect_class: str
    operation: str
    executor: str
    mutates_domain: bool
    records_consent: bool
    terminal: bool
    effect_ref: EntityRef | None
    schedule: dict[str, str] | None
    assertion_required: bool


class InteractionAssurance(TypedDict):
    mode: Literal["voice_permitted", "trusted_interface_required", "step_up_required"]
    voice_permitted: bool
    trusted_interface_required: bool
    step_up_required: bool


class InteractionAction(TypedDict, total=False):
    action_id: Required[str]
    label: Required[str]
    command: Required[str]
    value: Required[str | int | float | bool]
    target_ref: EntityRef
    expected_generation: int
    risk: Literal["read", "write", "external", "destructive", "irreversible", "privileged", "publication", "release"]
    confirmation_required: bool
    assurance: InteractionAssurance
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
    original_text: Required[str | None]
    action_token: Required[str | None]
    intent_proposal: Required[dict[str, Any] | None]
    status: Required[str]
    validation: Required[dict[str, Any]]
    supersedes_response_id: Required[str | None]
    presentation_id: Required[str | None]
    target_ref: Required[EntityRef | None]
    source_message_ref: Required[EntityRef | None]
    consumed_command: Required[InteractionAction | None]
    assurance_receipt: dict[str, Any] | None
    rejection_reason: Required[str | None]
    idempotency_key: Required[str]
    created_at: Required[str]


class HumanDecisionResponseResult(TypedDict, total=False):
    interaction: Required[ConversationInteraction]
    response: Required[InteractionResponse]
    dispatch: Required[dict[str, Any] | None]
    duplicate: Required[bool]


class WorkflowDefinitionSpec(TypedDict, total=False):
    schema: Required[str]
    workflow_type: Required[str]
    definition_version: Required[str]
    aggregate_type: Required[str]
    initial_state: Required[str]
    states: Required[list[dict[str, Any]]]
    commands: Required[list[dict[str, Any]]]
    transitions: Required[list[dict[str, Any]]]
    subworkflows: list[dict[str, Any]]
    metadata: dict[str, Any]


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
    accepted: Required[bool]
    status: Required[str]
    reason_code: Required[str | None]
    invocation: Required[dict[str, Any]]
    decision: Required[dict[str, Any] | None]
    commit: Required[dict[str, Any] | None]
    responses: Required[list[dict[str, Any]]]
    dispatch: Required[dict[str, Any] | None]
    reconciled: bool


class WorkflowInteractionOutcome(TypedDict):
    schema: Literal["adaos.workflow.interaction_outcome.v1"]
    interaction_id: str
    response_id: str
    dispatch_id: str
    status: str
    terminal: bool
    reason_code: str | None
    effect_assertion: dict[str, Any] | None
    outcome: dict[str, Any]


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
    "WorkflowInteractionOutcome",
]
