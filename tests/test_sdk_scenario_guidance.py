from __future__ import annotations

from pathlib import Path

import pytest

from adaos.sdk.scenarios import guidance


def _manifest() -> dict:
    return {
        "guidance": {
            "schema": "adaos.scenario.guidance.v1",
            "readme": "README.md",
            "overview": {"en": "Overview", "ru": "Описание"},
            "presentation": {
                "channels": ["web", "text", "voice"],
                "modal_id": "help",
            },
            "workflow": {
                "state_source": {
                    "kind": "skill",
                    "name": "manager.describe",
                    "params": {"object_id": "$state.objectId"},
                },
                "state_path": "workflow.state",
                "actions_path": "next_actions",
            },
            "conversational": {
                "help_intent": "demo.help",
                "next_steps_intent": "demo.next_steps",
            },
        }
    }


def test_read_guidance_is_channel_neutral(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    monkeypatch.setattr(guidance.scenarios_loader, "read_manifest", lambda *_args, **_kwargs: _manifest())
    monkeypatch.setattr(guidance.scenarios_loader, "scenario_root_for_space", lambda *_args, **_kwargs: tmp_path)

    result = guidance.read_guidance("demo", locale="ru", channel="voice")

    assert result["overview"] == "Описание"
    assert result["readme"] == "# Demo\n"
    assert result["modal_id"] == "help"


def test_describe_guidance_resolves_state_and_invokes_declared_provider(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Help", encoding="utf-8")
    monkeypatch.setattr(guidance.scenarios_loader, "read_manifest", lambda *_args, **_kwargs: _manifest())
    monkeypatch.setattr(guidance.scenarios_loader, "scenario_root_for_space", lambda *_args, **_kwargs: tmp_path)
    captured = {}

    def _invoke(skill_id, operation_id, arguments):
        captured.update(skill_id=skill_id, operation_id=operation_id, arguments=arguments)
        return {"workflow": {"state": "draft"}, "next_actions": [{"id": "review"}]}

    monkeypatch.setattr(guidance, "invoke_skill", _invoke)

    result = guidance.describe_guidance(
        "demo",
        state={"objectId": "experiment.1"},
        locale="ru",
        channel="text",
        section="next_steps",
    )

    assert captured == {
        "skill_id": "manager",
        "operation_id": "describe",
        "arguments": {
            "object_id": "experiment.1",
            "locale": "ru",
            "channel": "text",
            "section": "next_steps",
        },
    }
    assert result["workflow"]["workflow"]["state"] == "draft"


def test_read_guidance_rejects_an_undeclared_channel(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(guidance.scenarios_loader, "read_manifest", lambda *_args, **_kwargs: _manifest())
    monkeypatch.setattr(guidance.scenarios_loader, "scenario_root_for_space", lambda *_args, **_kwargs: tmp_path)

    with pytest.raises(guidance.ScenarioGuidanceError, match="does not support channel"):
        guidance.read_guidance("demo", channel="hologram")
