from __future__ import annotations

import json
from pathlib import Path

import yaml


def test_nlu_teacher_read_model_lists_templates_and_targets():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.teacher_read_model import describe_scenario_nlu, describe_skill_nlu, list_nlu_templates, list_training_targets

    ctx = get_ctx()
    skill_id = "test_teacher_inventory_skill"
    scenario_id = "test_teacher_inventory_scenario"

    skill_root = Path(ctx.paths.skills_dir()) / skill_id
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": skill_id,
                "version": "0.0.1",
                "events": {"subscribe": ["inventory.weather.fetch"]},
                "llm_policy": {"autoapply_nlu_teacher": True},
                "nlu": {
                    "intents": {
                        "inventory.weather": {
                            "examples": ["inventory weather in Berlin"],
                            "actions": [{"type": "callSkill", "target": "inventory.weather.fetch", "params": {"city": "$slot.city"}}],
                        }
                    },
                    "regex_rules": [
                        {
                            "id": "rx.inventory.weather",
                            "intent": "inventory.weather",
                            "pattern": r"\binventory weather\b",
                            "enabled": True,
                            "source": "test",
                        }
                    ],
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    scenario_root = Path(ctx.paths.scenarios_dir()) / scenario_id
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.0.1",
                "nlu": {
                    "intents": {
                        "inventory.open_panel": {
                            "examples": ["open inventory panel"],
                            "actions": [{"type": "callHost", "target": "desktop.modal.open", "params": {"modal_id": "inventory_panel"}}],
                        }
                    },
                    "regex_rules": [
                        {
                            "id": "rx.inventory.panel",
                            "intent": "inventory.open_panel",
                            "pattern": r"\binventory panel\b",
                            "enabled": True,
                            "source": "test",
                        }
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    skill_descriptor = describe_skill_nlu(skill_id)
    scenario_descriptor = describe_scenario_nlu(scenario_id)
    skill_templates = list_nlu_templates(owner_type="skill", owner_id=skill_id, include_system_actions=False)
    scenario_templates = list_nlu_templates(owner_type="scenario", owner_id=scenario_id, include_system_actions=False)
    targets = list_training_targets(webspace_id="desktop", include_system_actions=True)

    assert skill_descriptor["ok"] is True
    assert skill_descriptor["skill_surface"]["llm_policy"]["autoapply_nlu_teacher"] is True
    assert scenario_descriptor["ok"] is True
    assert "inventory.open_panel" in scenario_descriptor["nlu"]["intents"]

    skill_kinds = {item["kind"] for item in skill_templates["templates"]}
    scenario_kinds = {item["kind"] for item in scenario_templates["templates"]}
    assert {"example", "intent_route", "regex_rule"}.issubset(skill_kinds)
    assert {"example", "intent_route", "regex_rule"}.issubset(scenario_kinds)
    assert all(item["fingerprint"] for item in skill_templates["templates"])
    assert all(item["id"].startswith("tpl.skill.") for item in skill_templates["templates"])

    target_by_key = {(item.get("type"), item.get("id")): item for item in targets["targets"]}
    assert ("skill", skill_id) in target_by_key
    assert ("scenario", scenario_id) in target_by_key
    assert any(item.get("type") == "system_action" and item.get("intent") == "desktop.open_modal" for item in targets["targets"])

