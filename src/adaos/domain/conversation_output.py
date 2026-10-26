"""Shared, introspectable contracts for Core conversation output ports."""
from typing import Literal, TypedDict


class PublicationReceipt(TypedDict):
    schema: Literal["adaos.conversation.publication_receipt.v1"]
    publication_id: str
    message_id: str
    intent_ids: list[str]
    status: Literal["committed"]
    committed_at: float


class OutputRoute(TypedDict):
    route_id: str
    transport: str
    status: Literal["active", "paused", "expired", "revoked"]
    expires_at: str | None
    selected: bool


class OutputRoutes(TypedDict):
    items: list[OutputRoute]
    automatic_external_outputs_default: Literal["off"]
    conversation_id: str
    policy_revision: int
    truncated: bool


class OutputSelectionReceipt(TypedDict):
    conversation_id: str
    route_ids: list[str]
    policy_revision: int


class CommunicationContext(TypedDict):
    status: Literal["ready", "unavailable"]
    reason: str | None
    conversation_id: str | None
    thread_id: str | None
    owner: str | None
    request_id: str | None
    verified_user_ref: str | None
    origin_transport: str | None
