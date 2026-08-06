from __future__ import annotations

import pytest

from adaos.services import conversation_interactions
from adaos.services.conversation_rich_views import (
    ConversationRichViewError,
    ConversationRichViewRegistry,
)


def _definition() -> dict[str, object]:
    return {
        "schema": "adaos.conversation.rich_view.v1",
        "view_id": "builder.process",
        "version": 1,
        "title_ref": "builder.view.process.title",
        "data_contract_ref": "adaos.builder.process_projection.v1",
        "presentations": [
            {
                "kind": "panel",
                "required_capabilities": ["rich_view"],
                "renderer_ref": "webui:builder:process-panel",
                "semantic_equivalence": "full",
            },
            {
                "kind": "compact_message",
                "required_capabilities": ["text"],
                "renderer_ref": "conversation-output:builder.process.compact",
                "semantic_equivalence": "bounded",
            },
            {
                "kind": "deep_link",
                "required_capabilities": ["links"],
                "renderer_ref": "navigation:builder.process",
                "semantic_equivalence": "handoff",
            },
        ],
    }


def test_rich_view_registry_negotiates_web_and_limited_presentations() -> None:
    registry = ConversationRichViewRegistry([_definition()])
    web = conversation_interactions.standard_capability_profile("web", persist=False)
    telegram = conversation_interactions.standard_capability_profile("telegram", persist=False)

    web_plan = registry.resolve("builder.process", web)
    telegram_plan = registry.resolve("builder.process", telegram)

    assert web_plan["kind"] == "panel"
    assert web_plan["semantic_equivalence"] == "full"
    assert telegram_plan["kind"] in {"compact_message", "deep_link"}
    assert telegram_plan["kind"] != "panel"
    assert web_plan["definition_digest"] == telegram_plan["definition_digest"]


def test_rich_view_registry_is_immutable_and_fails_closed_without_fallback() -> None:
    registry = ConversationRichViewRegistry([_definition()])
    changed = _definition()
    changed["title_ref"] = "changed"
    with pytest.raises(ConversationRichViewError, match="mutable"):
        registry.register(changed)

    no_capabilities = {
        "profile_id": "none",
        "version": 1,
        "surface": "chat",
        "capabilities": {},
    }
    plan = registry.resolve("builder.process", no_capabilities)
    assert plan["supported"] is False
    assert plan["reason_code"] == "required_view_capabilities_unavailable"
