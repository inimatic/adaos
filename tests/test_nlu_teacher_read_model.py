from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
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


@pytest.mark.anyio
async def test_nlu_teacher_contextual_action_surface_exposes_m2_context():
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu.teacher_read_model import get_contextual_action_surface_async
    from adaos.services.yjs.doc import async_get_ydoc
    from adaos.services.yjs.store import reset_ystore_for_webspace

    ctx = get_ctx()
    webspace_id = f"ws-test-m2-surface-{uuid4().hex}"
    skill_id = "test_teacher_surface_skill"
    skill_root = Path(ctx.paths.skills_dir()) / skill_id
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": skill_id,
                "version": "0.0.1",
                "events": {"subscribe": ["surface.demo.run"]},
                "nlu": {
                    "intents": {
                        "surface.demo": {
                            "examples": ["run surface demo"],
                            "actions": [{"type": "callSkill", "target": "surface.demo.run"}],
                        }
                    },
                    "llm_hints": {
                        "primary_actions": [
                            {
                                "utterances": ["run surface demo"],
                                "intent": "surface.demo",
                                "confirmation": "Run surface demo?",
                            }
                        ]
                    },
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (skill_root / "webui.json").write_text(
        json.dumps(
            {
                "voice_capabilities": [
                    {
                        "id": "surface.demo.query",
                        "kind": "queryable_ui_section",
                        "title": "Surface demo section",
                        "labels": {"en": ["surface demo section"], "ru": ["раздел демо"]},
                        "result_modes": ["open_ui", "voice_summary"],
                        "default_result_mode": "open_ui",
                        "side_effect_class": "read_only",
                        "activation": [
                            {"type": "desktop.open_modal", "params": {"modal_id": "surface_modal"}},
                            {"type": "ui.affordance.activate", "params": {"affordance_id": "surface.demo.section"}},
                        ],
                    }
                ],
                "voice_affordances": [
                    {
                        "id": "surface.demo.section",
                        "kind": "ui_section",
                        "parent": "surface_modal",
                        "title": "Surface Demo",
                        "labels": {"en": ["surface demo"], "ru": ["демо поверхность"]},
                        "side_effect_class": "ui_navigation",
                        "activation": [
                            {"type": "desktop.open_modal", "params": {"modal_id": "surface_modal"}},
                            {"type": "ui.focus_widget", "params": {"widget_id": "surface-demo-widget"}},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        async with async_get_ydoc(webspace_id) as ydoc:
            with ydoc.begin_transaction() as txn:
                ydoc.get_map("ui").set(txn, "current_scenario", "surface_scenario")
                ydoc.get_map("ui").set(
                    txn,
                    "application",
                    {"modals": {"surface_modal": {"title": "Surface Modal"}}},
                )
                ydoc.get_map("data").set(
                    txn,
                    "catalog",
                    {"apps": [{"id": "surface_app", "title": "Surface App", "launchModal": "surface_modal"}]},
                )
                ydoc.get_map("data").set(txn, "installed", {"apps": ["surface_app"], "widgets": []})
                ydoc.get_map("data").set(txn, "nodes", {"node-a": {"label": "Node A", "status": "online"}})
                ydoc.get_map("data").set(
                    txn,
                    "jobs",
                    {"job-1": {"title": "Index media", "status": "running", "owner": "media_indexer_skill"}},
                )
                ydoc.get_map("data").set(
                    txn,
                    "nlu_teacher",
                    {
                        "pending_confirmations": [
                            {"id": "confirm-1", "status": "awaiting_user", "question": "Open Surface?", "ts": 10.0}
                        ],
                        "clarification_sessions": [
                            {
                                "id": "clarify-1",
                                "status": "awaiting_user",
                                "kind": "llm_clarification",
                                "question": "Which surface?",
                                "allowed_answers": [{"id": "first", "label": "Surface"}],
                                "ts": 11.0,
                            }
                        ],
                        "candidates": [{"id": "cand-1", "status": "pending"}],
                        "budget": {"counters": {"request": 2, "response": 1}, "policy": {"fallback_behavior": "store_miss_for_later_batch_enrichment"}},
                        "policies": {"retention": {"version": "nlu.teacher.retention.v1"}},
                        "deferred_enrichment_queue": [
                            {
                                "id": "deferred.req-2",
                                "status": "pending",
                                "request_id": "req-2",
                                "reason": "root_llm_unavailable",
                                "ts": 13.0,
                            }
                        ],
                        "events": [
                            {
                                "ts": 12.0,
                                "kind": "candidate.quarantined",
                                "request_id": "req-1",
                                "title": "Quarantined",
                            }
                        ],
                        "workbench_signals": [{"id": "teacher.queue", "status": "pending"}],
                    },
                )

        surface = await get_contextual_action_surface_async(webspace_id=webspace_id, include_live=True)

        assert surface["ok"] is True
        assert surface["surface_id"] == "adaos.nlu.contextual_action_surface.v1"
        assert surface["runtime_state"]["current_scenario"] == "surface_scenario"
        assert "surface_modal" in surface["runtime_state"]["available_modal_ids"]
        assert surface["runtime_state"]["installed"]["apps"] == ["surface_app"]
        assert surface["runtime_state"]["active_teacher_sessions"]["pending_confirmations"][0]["id"] == "confirm-1"
        assert surface["runtime_state"]["active_teacher_sessions"]["clarification_sessions"][0]["id"] == "clarify-1"
        assert surface["runtime_state"]["recent_errors"][0]["kind"] == "candidate.quarantined"
        assert surface["runtime_state"]["teacher_budget"]["counters"]["request"] == 2
        assert surface["runtime_state"]["teacher_policies"]["retention"]["version"] == "nlu.teacher.retention.v1"
        assert surface["runtime_state"]["deferred_enrichment_queue"][0]["request_id"] == "req-2"
        assert surface["process_state"]["teacher_queue"]["pending_candidates"] == 1
        assert surface["process_state"]["teacher_queue"]["deferred_enrichment"] == 1
        assert surface["process_state"]["teacher_budget"]["counters"]["response"] == 1
        assert surface["process_state"]["process_rows"][0]["id"] == "job-1"
        assert any(item.get("id") == "host.desktop.modal.open" for item in surface["available_actions"])
        assert any(item.get("owner") == {"type": "skill", "id": skill_id} for item in surface["available_actions"])
        voice_capability = next(item for item in surface["voice_capabilities"] if item.get("id") == "surface.demo.query")
        voice_affordance = next(item for item in surface["voice_affordances"] if item.get("id") == "surface.demo.section")
        assert voice_capability["owner"] == {"type": "skill", "id": skill_id}
        assert voice_capability["result_modes"] == ["open_ui", "voice_summary"]
        assert voice_capability["availability"]["status"] == "reachable"
        assert voice_affordance["parent"] == "surface_modal"
        assert voice_affordance["availability"]["status"] == "reachable"
        assert surface["voice_surface"]["voice_affordances_count"] >= 1
        hint_rows = [item for item in surface["developer_hints"] if item.get("owner") == {"type": "skill", "id": skill_id}]
        assert hint_rows
        assert hint_rows[0]["hints"]["primary_actions"][0]["intent"] == "surface.demo"
        assert surface["authoring_boundaries"]["dispatch"] is False
    finally:
        reset_ystore_for_webspace(webspace_id)


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
