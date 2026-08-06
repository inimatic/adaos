from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import re
import time
from typing import Any, Literal, Mapping


CONVERSATION_CONTRACT_VERSION = "adaos.conversation.contract.v1"

ActorKind = Literal["core", "skill", "agent", "user", "node", "endpoint", "transport"]
ConversationState = Literal["active", "archived", "deleted", "redacted"]
DialogRepairState = Literal[
    "none",
    "no_match",
    "no_input",
    "disambiguation",
    "correction",
    "interruption",
    "cancel",
    "resume",
    "parameter_change",
]
MemoryScope = Literal["global_user", "skill_user", "agent_user", "conversation"]
MemoryConsent = Literal["unknown", "denied", "session", "skill_scoped", "global", "granted"]
MemoryVisibility = Literal["owner_only", "conversation", "user_visible", "cross_owner"]
ResponseTarget = Literal[
    "text_tail",
    "speech_text",
    "card",
    "pending_action",
    "notification",
    "builder_evidence",
    "transport_native",
]

_SIMPLE_ACTOR_RE = re.compile(r"^(core|skill|user|node|endpoint):[A-Za-z0-9_.:@/-]+$")
_AGENT_ACTOR_RE = re.compile(r"^agent:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$")
_TRANSPORT_ACTOR_RE = re.compile(r"^transport:[A-Za-z0-9_.-]+:[A-Za-z0-9_.:@/-]+$")


class ConversationContractError(ValueError):
    """Raised when a canonical conversation contract object is malformed."""


def now_ts() -> float:
    return time.time()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _require(value: Any, field_name: str) -> str:
    token = _clean(value)
    if not token:
        raise ConversationContractError(f"{field_name} is required")
    return token


def validate_actor_id(actor_id: str) -> str:
    token = _require(actor_id, "actor_id")
    if _SIMPLE_ACTOR_RE.match(token) or _AGENT_ACTOR_RE.match(token) or _TRANSPORT_ACTOR_RE.match(token):
        return token
    raise ConversationContractError(f"invalid actor_id: {token!r}")


def actor_kind(actor_id: str) -> ActorKind:
    token = validate_actor_id(actor_id)
    return token.split(":", 1)[0]  # type: ignore[return-value]


def _tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _as_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _as_jsonable(item) for key, item in asdict(value).items() if item is not None}
    if isinstance(value, Mapping):
        return {str(key): _as_jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, tuple):
        return [_as_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_as_jsonable(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ActorRef:
    actor_id: str
    kind: ActorKind | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        actor_id = validate_actor_id(self.actor_id)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "kind", self.kind or actor_kind(actor_id))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class Initiator:
    actor_id: str
    reason: str
    source: str | None = None
    external_ref: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor_id", validate_actor_id(self.actor_id))
        object.__setattr__(self, "reason", _require(self.reason, "initiator.reason"))
        object.__setattr__(self, "source", _clean(self.source) or None)
        object.__setattr__(self, "external_ref", _dict(self.external_ref))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ContentPart:
    type: str
    text: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _require(self.type, "content.type"))
        object.__setattr__(self, "text", _clean(self.text) or None)
        object.__setattr__(self, "data", _dict(self.data))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class SourceRef:
    message_id: str | None = None
    conversation_id: str | None = None
    seq: int | None = None
    kind: str | None = None
    ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    mode: str = "node_store"
    searchable: bool = True
    cross_skill_use: str = "deny_by_default"
    raw_window_messages: int = 500
    segment_size_messages: int = 40
    summarization: str = "async"

    def __post_init__(self) -> None:
        if self.cross_skill_use != "deny_by_default":
            raise ConversationContractError("history.cross_skill_use must default to deny_by_default")
        if self.raw_window_messages < 0 or self.segment_size_messages <= 0:
            raise ConversationContractError("history windows must be non-negative and segment size must be positive")

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    recent_turns: int = 20
    fts_top_k: int = 8
    semantic_top_k: int = 0
    memory_top_k: int = 12
    max_context_tokens: int = 12_000
    timeout_ms: int = 750

    def __post_init__(self) -> None:
        for name in ("recent_turns", "fts_top_k", "semantic_top_k", "memory_top_k", "max_context_tokens", "timeout_ms"):
            if int(getattr(self, name)) < 0:
                raise ConversationContractError(f"retrieval.{name} must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class PersonalizationPolicy:
    global_user_profile: str = "read_with_consent"
    skill_user_profile: str = "read_write"
    agent_user_profile: str = "read_write"
    conversation_profile: str = "read_write"

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class RepairPolicy:
    no_match: str = "clarify"
    no_input: str = "prompt_once"
    disambiguation: str = "ask"
    correction: str = "apply_if_low_risk"
    interruption: str = "pause_frame"
    cancel: str = "confirm_if_destructive"
    resume: str = "restore_frame"
    parameter_change: str = "revalidate_frame"

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ResponsePolicy:
    targets: tuple[ResponseTarget, ...] = ("text_tail", "speech_text")
    card_policy: str = "explicit"
    pending_action_policy: str = "required_for_mutations"
    notification_policy: str = "non_blocking"

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", tuple(self.targets))
        if not self.targets:
            raise ConversationContractError("response.targets must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    retention_class: str = "normal"
    max_raw_messages: int = 500
    redaction: str = "policy_controlled"
    exportable: bool = True
    delete_mode: str = "redact_then_tombstone"
    expires_at: float | None = None

    def __post_init__(self) -> None:
        if self.max_raw_messages < 0:
            raise ConversationContractError("retention.max_raw_messages must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        data = _as_jsonable(self)
        data["class"] = data.pop("retention_class")
        return data


@dataclass(frozen=True, slots=True)
class ConversationRoutingPolicy:
    default_transports: tuple[str, ...] = ("web",)
    allow_voice: bool = True
    allow_proactive: bool = False
    fallback_surface: str = "general_assistant"

    def __post_init__(self) -> None:
        object.__setattr__(self, "default_transports", tuple(_require(item, "routing.default_transports[]") for item in self.default_transports))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    node_id: str
    kind: str
    owner: str
    surface: str
    webspace_id: str
    created_by: Initiator
    logical_owner: str | None = None
    title: str | None = None
    state: ConversationState = "active"
    participants: tuple[ActorRef, ...] = ()
    active_agent_id: str | None = None
    history_policy: HistoryPolicy = field(default_factory=HistoryPolicy)
    retrieval_policy: RetrievalPolicy = field(default_factory=RetrievalPolicy)
    personalization_policy: PersonalizationPolicy = field(default_factory=PersonalizationPolicy)
    repair_policy: RepairPolicy = field(default_factory=RepairPolicy)
    response_policy: ResponsePolicy = field(default_factory=ResponsePolicy)
    routing_policy: ConversationRoutingPolicy = field(default_factory=ConversationRoutingPolicy)
    retention_policy: RetentionPolicy = field(default_factory=RetentionPolicy)
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require(self.id, "conversation.id"))
        object.__setattr__(self, "node_id", _require(self.node_id, "conversation.node_id"))
        object.__setattr__(self, "kind", _require(self.kind, "conversation.kind"))
        object.__setattr__(self, "owner", validate_actor_id(self.owner))
        object.__setattr__(self, "logical_owner", validate_actor_id(self.logical_owner or self.owner))
        object.__setattr__(self, "surface", _require(self.surface, "conversation.surface"))
        object.__setattr__(self, "webspace_id", _require(self.webspace_id, "conversation.webspace_id"))
        object.__setattr__(self, "participants", tuple(self.participants))
        if self.active_agent_id:
            object.__setattr__(self, "active_agent_id", validate_actor_id(self.active_agent_id))
        object.__setattr__(self, "meta", _dict(self.meta))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ConversationThread:
    id: str
    conversation_id: str
    title: str | None = None
    state: ConversationState = "active"
    created_by: Initiator | None = None
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require(self.id, "thread.id"))
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "thread.conversation_id"))
        object.__setattr__(self, "meta", _dict(self.meta))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: str
    node_id: str
    conversation_id: str
    seq: int
    role: str
    from_actor: ActorRef
    content: tuple[ContentPart, ...]
    created_at: float = field(default_factory=now_ts)
    thread_id: str | None = None
    agent_id: str | None = None
    initiator: Initiator | None = None
    transport: str | None = None
    external_ref: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    index_state: dict[str, str] = field(default_factory=lambda: {"fts": "pending", "summary": "pending", "embedding": "not_configured"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require(self.id, "message.id"))
        object.__setattr__(self, "node_id", _require(self.node_id, "message.node_id"))
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "message.conversation_id"))
        if self.seq <= 0:
            raise ConversationContractError("message.seq must be positive")
        object.__setattr__(self, "role", _require(self.role, "message.role"))
        object.__setattr__(self, "content", tuple(self.content))
        if not self.content:
            raise ConversationContractError("message.content must not be empty")
        if self.agent_id:
            object.__setattr__(self, "agent_id", validate_actor_id(self.agent_id))
        object.__setattr__(self, "external_ref", _dict(self.external_ref))
        object.__setattr__(self, "meta", _dict(self.meta))
        object.__setattr__(self, "index_state", {str(key): str(value) for key, value in _dict(self.index_state).items()})

    @property
    def text(self) -> str:
        return "\n".join(part.text or "" for part in self.content if part.type == "text").strip()

    def to_dict(self) -> dict[str, Any]:
        data = _as_jsonable(self)
        data["from"] = data.pop("from_actor")
        return data


@dataclass(frozen=True, slots=True)
class DialogChannel:
    webspace_id: str
    channel_id: str
    conversation_id: str
    owner: str
    surface: str
    title: str | None = None
    active_agent_id: str | None = None
    status: str = "active"
    updated_at: float = field(default_factory=now_ts)
    policy: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "webspace_id", _require(self.webspace_id, "dialog_channel.webspace_id"))
        object.__setattr__(self, "channel_id", _require(self.channel_id, "dialog_channel.channel_id"))
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "dialog_channel.conversation_id"))
        object.__setattr__(self, "owner", validate_actor_id(self.owner))
        object.__setattr__(self, "surface", _require(self.surface, "dialog_channel.surface"))
        if self.active_agent_id:
            object.__setattr__(self, "active_agent_id", validate_actor_id(self.active_agent_id))
        object.__setattr__(self, "policy", _dict(self.policy))
        object.__setattr__(self, "meta", _dict(self.meta))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    node_id: str
    owner: str
    scope: MemoryScope
    text: str
    consent: MemoryConsent
    visibility: MemoryVisibility
    user_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    kind: str = "fact"
    source_refs: tuple[SourceRef, ...] = ()
    confidence: float | None = None
    value: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    expires_at: float | None = None
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require(self.id, "memory.id"))
        object.__setattr__(self, "node_id", _require(self.node_id, "memory.node_id"))
        object.__setattr__(self, "owner", validate_actor_id(self.owner))
        object.__setattr__(self, "text", _require(self.text, "memory.text"))
        if self.agent_id:
            object.__setattr__(self, "agent_id", validate_actor_id(self.agent_id))
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ConversationContractError("memory.confidence must be between 0 and 1")
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        object.__setattr__(self, "value", _dict(self.value))
        object.__setattr__(self, "policy", _dict(self.policy))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ConversationSegment:
    id: str
    conversation_id: str
    seq_from: int
    seq_to: int
    summary: str
    token_estimate: int = 0
    index_state: dict[str, str] = field(default_factory=lambda: {"fts": "pending", "embedding": "not_configured"})
    updated_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require(self.id, "segment.id"))
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "segment.conversation_id"))
        if self.seq_from <= 0 or self.seq_to < self.seq_from:
            raise ConversationContractError("segment seq range must be positive and ordered")
        object.__setattr__(self, "summary", _require(self.summary, "segment.summary"))
        object.__setattr__(self, "index_state", {str(key): str(value) for key, value in _dict(self.index_state).items()})

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class DialogPolicyState:
    conversation_id: str
    owner: str
    surface: str
    channel_id: str | None = None
    active_agent_id: str | None = None
    repair_state: DialogRepairState = "none"
    frame_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "dialog_policy.conversation_id"))
        object.__setattr__(self, "owner", validate_actor_id(self.owner))
        object.__setattr__(self, "surface", _require(self.surface, "dialog_policy.surface"))
        if self.active_agent_id:
            object.__setattr__(self, "active_agent_id", validate_actor_id(self.active_agent_id))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class DialogFrame:
    id: str
    kind: str
    state: str = "collecting"
    slots: dict[str, Any] = field(default_factory=dict)
    required_slots: tuple[str, ...] = ()
    validation: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require(self.id, "frame.id"))
        object.__setattr__(self, "kind", _require(self.kind, "frame.kind"))
        object.__setattr__(self, "slots", _dict(self.slots))
        object.__setattr__(self, "required_slots", tuple(str(item) for item in _tuple(self.required_slots) if _clean(item)))
        object.__setattr__(self, "validation", _dict(self.validation))
        object.__setattr__(self, "policy", _dict(self.policy))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class DialogAct:
    type: str
    actor_id: str
    target: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    evidence_refs: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "type", _require(self.type, "dialog_act.type"))
        object.__setattr__(self, "actor_id", validate_actor_id(self.actor_id))
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ConversationContractError("dialog_act.confidence must be between 0 and 1")
        object.__setattr__(self, "payload", _dict(self.payload))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class DialogTurn:
    id: str
    conversation_id: str
    turn_trace_id: str
    policy_state: DialogPolicyState
    user_message_id: str | None = None
    state: str = "started"
    active_frame_id: str | None = None
    repair_state: DialogRepairState = "none"
    started_at: float = field(default_factory=now_ts)
    completed_at: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require(self.id, "turn.id"))
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "turn.conversation_id"))
        object.__setattr__(self, "turn_trace_id", _require(self.turn_trace_id, "turn.turn_trace_id"))
        object.__setattr__(self, "meta", _dict(self.meta))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    conversation_id: str
    content: tuple[ContentPart, ...]
    request_id: str | None = None
    dialog_acts: tuple[DialogAct, ...] = ()
    render_targets: tuple[ResponseTarget, ...] = ("text_tail",)
    speech_text: str | None = None
    card: dict[str, Any] | None = None
    pending_action_ref: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "response.conversation_id"))
        object.__setattr__(self, "content", tuple(self.content))
        if not self.content and not self.dialog_acts:
            raise ConversationContractError("response must contain content or dialog acts")
        object.__setattr__(self, "dialog_acts", tuple(self.dialog_acts))
        object.__setattr__(self, "render_targets", tuple(self.render_targets))
        object.__setattr__(self, "card", _dict(self.card))
        object.__setattr__(self, "meta", _dict(self.meta))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


@dataclass(frozen=True, slots=True)
class TurnTrace:
    turn_trace_id: str
    conversation_id: str
    webspace_id: str
    channel_id: str | None = None
    message_id: str | None = None
    agent_id: str | None = None
    selected_tool: str | None = None
    policy_decision: dict[str, Any] = field(default_factory=dict)
    renderer: dict[str, Any] = field(default_factory=dict)
    status: str = "started"
    created_at: float = field(default_factory=now_ts)
    completed_at: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "turn_trace_id", _require(self.turn_trace_id, "turn_trace_id"))
        object.__setattr__(self, "conversation_id", _require(self.conversation_id, "trace.conversation_id"))
        object.__setattr__(self, "webspace_id", _require(self.webspace_id, "trace.webspace_id"))
        if self.agent_id:
            object.__setattr__(self, "agent_id", validate_actor_id(self.agent_id))
        object.__setattr__(self, "policy_decision", _dict(self.policy_decision))
        object.__setattr__(self, "renderer", _dict(self.renderer))

    def to_dict(self) -> dict[str, Any]:
        return _as_jsonable(self)


DIALOG_PROJECTION_RULES: dict[str, Any] = {
    "schema": "adaos.dialog.projection.v1",
    "canonical_store": "node_conversation_store",
    "yjs_path": "data.dialog",
    "webio_stream_receivers": ["dialog.visible_tail", "voice_chat.messages"],
    "max_visible_tail_messages": 50,
    "allowed_top_level_fields": [
        "active_channel_id",
        "channels",
        "active_agent",
        "visible_tail",
        "memory",
        "last_turn_trace",
        "updated_at",
    ],
    "forbidden": [
        "unbounded_transcript",
        "raw_cross_owner_memory",
        "transport_secret",
        "external_chat_id_as_conversation_id",
    ],
}


def conversation_contract_snapshot() -> dict[str, Any]:
    return {
        "schema_version": CONVERSATION_CONTRACT_VERSION,
        "records": [
            "Conversation",
            "ConversationMessage",
            "ConversationThread",
            "DialogChannel",
            "MemoryItem",
            "ConversationSegment",
            "ConversationRoutingPolicy",
            "DialogTurn",
            "DialogAct",
            "DialogFrame",
            "DialogPolicyState",
            "ResponseEnvelope",
            "TurnTrace",
            "ConversationInteraction",
            "InteractionResponse",
            "ChannelCapabilityProfile",
            "InteractionPresentation",
        ],
        "actor_id_patterns": {
            "core": "core:<surface>",
            "skill": "skill:<skill_id>",
            "agent": "agent:<skill_id>:<agent_id>",
            "user": "user:<user_id>",
            "node": "node:<node_id>",
            "endpoint": "endpoint:<endpoint_id>",
            "transport": "transport:<transport>:<external_ref>",
        },
        "default_policies": {
            "history": HistoryPolicy().to_dict(),
            "retrieval": RetrievalPolicy().to_dict(),
            "personalization": PersonalizationPolicy().to_dict(),
            "repair": RepairPolicy().to_dict(),
            "response": ResponsePolicy().to_dict(),
            "routing": ConversationRoutingPolicy().to_dict(),
            "retention": RetentionPolicy().to_dict(),
        },
        "projection_rules": dict(DIALOG_PROJECTION_RULES),
    }


__all__ = [
    "ActorRef",
    "ContentPart",
    "Conversation",
    "ConversationContractError",
    "ConversationMessage",
    "ConversationRoutingPolicy",
    "ConversationSegment",
    "ConversationThread",
    "DIALOG_PROJECTION_RULES",
    "DialogAct",
    "DialogChannel",
    "DialogFrame",
    "DialogPolicyState",
    "DialogTurn",
    "HistoryPolicy",
    "Initiator",
    "MemoryItem",
    "PersonalizationPolicy",
    "RepairPolicy",
    "ResponseEnvelope",
    "ResponsePolicy",
    "RetentionPolicy",
    "RetrievalPolicy",
    "SourceRef",
    "TurnTrace",
    "actor_kind",
    "conversation_contract_snapshot",
    "validate_actor_id",
]
