from __future__ import annotations

from adaos.sdk import conversation


def test_list_published_agents_is_authorized_bounded_and_inert(monkeypatch) -> None:
    required: list[str] = []
    monkeypatch.setattr(
        "adaos.sdk.access.require",
        lambda capability: required.append(str(capability)),
    )
    monkeypatch.setattr(
        "adaos.services.router.dialog_registry.published_agent_records",
        lambda: [
            {
                "id": "agent:builder_skill:builder",
                "label": "Builder",
                "owner": "skill:builder_skill",
                "channel_id": "builder",
                "skill": "builder_skill",
                "kind": "skill_agent",
                "icon": "construct-outline",
                "source": "skill:builder_skill.skill_yaml",
                "policy": {"secret": "not projected"},
                "talk_tool": "chat",
            },
            {
                "id": "agent:research_orchestrator_skill:researcher",
                "label": "Researcher",
                "owner": "skill:research_orchestrator_skill",
                "channel_id": "research_orchestrator",
                "skill": "research_orchestrator_skill",
                "kind": "skill_agent",
                "icon": "flask-outline",
                "source": "skill:research_orchestrator_skill.skill_yaml",
            },
        ],
    )

    result = conversation.list_published_agents(limit=1)

    assert required == ["workspace.read"]
    assert result["schema"] == "adaos.sdk.conversation.published_agents.v1"
    assert result["count"] == 1
    assert result["total"] == 2
    assert result["truncated"] is True
    assert result["invalidation_tags"] == ["conversation.agents"]
    assert result["items"] == [
        {
            "id": "agent:builder_skill:builder",
            "label": "Builder",
            "owner": "skill:builder_skill",
            "channel_id": "builder",
            "skill": "builder_skill",
            "kind": "skill_agent",
            "icon": "construct-outline",
            "source": "skill:builder_skill.skill_yaml",
        }
    ]


def test_list_published_agents_filters_channel(monkeypatch) -> None:
    monkeypatch.setattr("adaos.sdk.access.require", lambda _capability: None)
    monkeypatch.setattr(
        "adaos.services.router.dialog_registry.published_agent_records",
        lambda: [
            {"id": "agent:general", "label": "General", "channel_id": "general"},
            {"id": "agent:builder", "label": "Builder", "channel_id": "builder"},
        ],
    )

    result = conversation.list_published_agents(channel_id="builder")

    assert [item["id"] for item in result["items"]] == ["agent:builder"]
    assert result["total"] == 1
