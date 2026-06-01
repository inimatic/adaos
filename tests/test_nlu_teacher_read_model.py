from __future__ import annotations

import json
from pathlib import Path

import yaml


def test_nlu_teacher_read_model_lists_templates_and_targets():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.teacher_read_model import (
        describe_scenario_nlu,
        describe_skill_nlu,
        list_nlu_templates,
        list_training_targets,
        preview_interface_action,
        preview_template_patch,
    )

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

    preview = preview_template_patch(
        webspace_id="desktop",
        operation="add_regex_rule",
        target={"type": "skill", "id": skill_id},
        intent="inventory.weather",
        text="show inventory temperature in Berlin",
        pattern=r"\binventory temperature\b(?:\s+in\s+(?P<city>[^?.!,;:]+))?",
    )
    duplicate = preview_template_patch(
        webspace_id="desktop",
        operation="add_regex_rule",
        target={"type": "skill", "id": skill_id},
        intent="inventory.weather",
        text="inventory weather",
        pattern=r"\binventory weather\b",
    )
    stale = preview_template_patch(
        webspace_id="desktop",
        operation="save_example",
        target={"type": "scenario", "id": scenario_id},
        intent="inventory.open_panel",
        text="show inventory workspace",
        base_fingerprint="stale-fingerprint",
    )
    action_preview = preview_interface_action(
        webspace_id="desktop",
        action_id="host.desktop.modal.open",
        params={"modal_id": "nlu_teacher_modal"},
    )
    missing_action_slot = preview_interface_action(
        webspace_id="desktop",
        action_id="host.desktop.modal.open",
        params={},
    )

    assert preview["ok"] is True
    assert preview["regex_preview"]["slots"]["city"] == "Berlin"
    assert duplicate["ok"] is False
    assert any(item["name"] == "duplicate_regex" and item["status"] == "duplicate" for item in duplicate["checks"])
    assert stale["ok"] is False
    assert any(item["name"] == "base_fingerprint" and item["status"] == "stale" for item in stale["checks"])
    assert action_preview["ok"] is True
    assert action_preview["would_dispatch"]["target"] == "desktop.modal.open"
    assert action_preview["would_dispatch"]["params"]["webspace_id"] == "desktop"
    assert missing_action_slot["ok"] is False
    assert any(item["name"] == "required_slots" and item["missing"] == ["modal_id"] for item in missing_action_slot["checks"])


def test_nlu_teacher_events_build_workbench_signals():
    from adaos.services.nlu.teacher_events import rebuild_events_by_candidate

    teacher = {
        "items": [
            {"id": "teach-1", "status": "pending"},
            {"id": "teach-2", "status": "skipped"},
        ],
        "candidates": [
            {
                "id": "cand-1",
                "request_id": "req-1",
                "status": "pending",
                "kind": "regex_rule",
                "candidate": {"name": "Weather regex", "description": "test"},
            },
            {
                "id": "cand-2",
                "request_id": "req-2",
                "status": "quarantined",
                "kind": "regex_rule",
                "candidate": {"name": "Bad regex", "description": "test"},
            },
        ],
        "events": [
            {
                "ts": 1.0,
                "kind": "candidate.proposed",
                "request_id": "req-1",
                "request_text": "weather pls",
                "title": "Candidate proposed",
                "subtitle": "regex_rule",
                "raw": {"candidate": {"name": "Weather regex"}},
            },
            {
                "ts": 2.0,
                "kind": "understanding.acquired",
                "request_id": "req-1",
                "request_text": "weather pls",
                "title": "Understanding acquired",
                "subtitle": "desktop.open_weather",
                "raw": {"candidate_id": "cand-1"},
            },
        ],
        "llm_logs": [{"id": "log-1", "status": "error"}],
    }

    rebuild_events_by_candidate(teacher)

    assert teacher["workbench_summary"]["pending_candidate_count"] == 1
    assert teacher["workbench_summary"]["quarantined_candidate_count"] == 1
    signal_ids = {item["id"] for item in teacher["workbench_signals"]}
    assert {"teacher.queue", "teacher.acquired", "teacher.quarantine", "teacher.llm_errors", "teacher.latest_event"}.issubset(signal_ids)
    assert teacher["threads_by_request"]
