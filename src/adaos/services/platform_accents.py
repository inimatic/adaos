"""Platform distinctions, not a general programming tutorial or new authority."""
from __future__ import annotations

ACCENTS = {
    "adaos.skill.webui_tool_contract.v2": "UI actions execute through the public manifest graph; declared effects participate in authorization.",
    "adaos.ui.declarative_state.v1": "Presentation and localized labels do not define resource identity or routing; view modes belong to declarative state.",
    "adaos.ui.interaction_resources.v1": "Delivery, focus and degraded states are first-class platform interaction dimensions, not decorative UI.",
    "adaos.builder.workflow_definition.v1": "The manifest-referenced workflow definition is the transition authority; UI and runtime project that definition.",
    "adaos.skill.human_decision.v1": "Pending Actions project governed workflow interactions. A callback acknowledgement is not an observed durable outcome.",
    "adaos.skill.data_route_receiver.v1": "A declared data route and its receiver have distinct ownership and delivery contracts.",
    "adaos.skill.receiver_snapshot.v1": "Snapshots and streams have explicit freshness and pressure semantics; presentation does not own the stream.",
    "adaos.skill.causal_read.v1": "Tool-backed reads carry causal delivery semantics; a command result is not automatically a fresh read model.",
    "adaos.skill.resource_storage.v1": "Domain resources belong to the skill; mutable user data lives in its owner-scoped runtime boundary, never in package files.",
    "adaos.skill.runtime_lifecycle.v1": "Installation, configuration, runtime and update preservation are separate platform lifecycle dimensions.",
    "adaos.skill.python_lifecycle.v1": "Import-time code and runtime resources have different lifetimes in an isolated skill slot.",
    "adaos.skill.event_loop_isolation.v1": "The shared runtime loop is a platform resource; blocking work needs the admitted isolation boundary.",
    "adaos.skill.conversation_i18n.v1": "Conversation history, semantic messages, localization and reusable memory are platform-owned boundaries with owner and consent semantics.",
    "adaos.skill.async_llm_job.v1": "Long-running model work belongs to Root-accounted jobs and delivery, not an untracked handler-local request.",
    "adaos.skill.member_subnet.v1": "Member identity and subnet data come from admitted typed providers; unavailability must not turn into invented data.",
    "adaos.resource.access_privacy.v1": "Caller authority, application roles and data practices are separate from request payloads and UI visibility.",
    "adaos.builder.consumer_contract.v1": "Accepted consumer contracts and their content-addressed delivery are exact authority; capability semantics and delivery guarantees are distinct.",
}


def platform_accent(rule_id: str) -> str | None:
    return ACCENTS.get(rule_id)
