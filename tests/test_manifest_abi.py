from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, Draft7Validator, ValidationError


def _load_schema(name: str) -> dict:
    path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _load_service_skill_schema() -> dict:
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "services"
        / "skill"
        / "skill_schema.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_skill_schema_accepts_runtime_activation_policy() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "infrascope_skill",
        "version": "0.9.0",
        "runtime": {
            "python": "3.11",
            "activation": {
                "mode": "lazy",
                "startup_allowed": False,
                "background_refresh": False,
                "when": {
                    "scenarios_active": ["infrascope"],
                    "client_presence": True,
                    "webspace_scope": "active",
                    "webspaces": ["default"],
                },
            },
        },
    }

    Draft7Validator(schema).validate(payload)


def test_skill_schema_accepts_browser_data_route_plan() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "infrastate_skill",
        "version": "0.76.0",
        "data_routes": [
            {
                "surface": "widget:runtime_status",
                "route": "yjs",
                "projection_slot": "infrastate.state",
                "first_paint": "compact cached badge",
                "recovery": "Yjs room replay restores latest compact status",
                "update_source": ["runtime.ready", "yjs.guard.changed"],
                "budget": {
                    "max_payload_bytes": 4096,
                    "max_publish_hz": 0.5,
                    "coalesce_ms": 1000,
                    "snapshot_policy": "on_subscribe",
                },
                "guard_visibility": {
                    "degraded_state": "status badge shows Yjs pressure",
                    "log": "quarantine.jsonl",
                    "quarantine": True,
                    "metric": "yjs_owner_guard.suppressed",
                },
            },
            {
                "surface": "modal:operations",
                "route": "stream",
                "receiver": "infrastate.operations.active",
                "first_paint": "empty active operations list",
                "recovery": "snapshot requested on stream subscribe",
                "update_source": "operation.changed",
            },
        ],
    }

    Draft7Validator(schema).validate(payload)


def test_skill_schema_accepts_builder_authoring_hints() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "weather_skill",
        "version": "0.3.0",
        "llm_hints": {
            "description": "Weather capability for current conditions.",
            "aliases": ["weather", "forecast"],
            "primary_actions": [
                {
                    "id": "weather.current",
                    "intent": "weather.current",
                    "tool": "get_snapshot",
                    "examples": ["show weather in Paris"],
                    "side_effect_class": "read_only",
                }
            ],
            "slot_schemas": {"city": {"type": "string"}},
            "entities": [{"kind": "city", "aliases": ["Paris"]}],
            "owner_hints": ["skill:weather_skill"],
        },
        "nlu": {
            "nlu_hints": {
                "examples": [{"text": "погода в Москве", "locale": "ru", "intent": "weather.current"}],
                "side_effect_class": "read_only",
            }
        },
    }

    Draft7Validator(schema).validate(payload)


def test_skill_schema_rejects_unknown_browser_data_route() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "bad_skill",
        "version": "1.0.0",
        "data_routes": [
            {
                "surface": "widget:status",
                "route": "magic_runtime_autoroute",
            }
        ],
    }

    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(payload)


def test_skill_schema_rejects_status_plane_as_data_route() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "bad_skill",
        "version": "1.0.0",
        "data_routes": [
            {
                "surface": "widget:status",
                "route": "status",
            }
        ],
    }

    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(payload)


def test_runtime_skill_validator_schema_accepts_data_routes() -> None:
    schema = _load_service_skill_schema()
    payload = {
        "name": "route_aware_skill",
        "version": "0.1.0",
        "data_routes": [
            {
                "surface": "widget:status",
                "route": "stream",
                "receiver": "route_aware.status",
                "budget": {
                    "max_payload_bytes": 2048,
                    "max_publish_hz": 1,
                    "snapshot_policy": "on_subscribe_if_stale",
                },
                "guard_visibility": "show degraded status and log suppression count",
            }
        ],
    }

    Draft202012Validator(schema).validate(payload)


def test_runtime_skill_validator_schema_accepts_builder_authoring_hints() -> None:
    schema = _load_service_skill_schema()
    payload = {
        "name": "route_aware_skill",
        "version": "0.1.0",
        "llm_hints": {
            "aliases": ["runtime status"],
            "actions": [{"id": "runtime.status", "event": "runtime.status.request"}],
            "side_effect_class": "read_only",
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_runtime_skill_validator_schema_rejects_status_plane_data_route() -> None:
    schema = _load_service_skill_schema()
    payload = {
        "name": "bad_skill",
        "version": "1.0.0",
        "data_routes": [
            {
                "surface": "widget:status",
                "route": "status",
            }
        ],
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_skill_schema_rejects_unknown_activation_mode() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "bad_skill",
        "version": "1.0.0",
        "runtime": {
            "activation": {
                "mode": "hot",
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(payload)


def test_scenario_schema_accepts_runtime_skill_bindings() -> None:
    schema = _load_schema("scenario.schema.json")
    payload = {
        "id": "infrascope",
        "version": "0.6.0",
        "depends": ["legacy_skill"],
        "runtime": {
            "skills": {
                "required": ["infrascope_skill"],
                "optional": ["telemetry_skill"],
            }
        },
    }

    Draft7Validator(schema).validate(payload)


def test_scenario_schema_accepts_builder_authoring_hints() -> None:
    schema = _load_schema("scenario.schema.json")
    payload = {
        "id": "web_desktop",
        "version": "0.8.0",
        "llm_hints": {
            "aliases": ["desktop", "capabilities"],
            "primary_actions": [
                {
                    "id": "desktop.open_modal",
                    "intent": "desktop.open_modal",
                    "examples": ["open apps catalog"],
                    "side_effect_class": "runtime_write",
                }
            ],
        },
        "nlu": {
            "nlu_hints": {
                "slot_schemas": {"modal_id": {"type": "string"}},
                "owner_hints": ["scenario:web_desktop"],
            }
        },
    }

    Draft7Validator(schema).validate(payload)


def test_scenario_schema_rejects_unknown_runtime_skill_field() -> None:
    schema = _load_schema("scenario.schema.json")
    payload = {
        "id": "infrascope",
        "version": "0.6.0",
        "runtime": {
            "skills": {
                "required": ["infrascope_skill"],
                "preferred": ["telemetry_skill"],
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(payload)


def test_webui_schema_accepts_builder_nlu_hints() -> None:
    schema = _load_schema("webui.v1.schema.json")
    payload = {
        "catalog": {
            "apps": [
                {
                    "id": "weather",
                    "title": "Weather",
                    "launchModal": "weather_modal",
                }
            ]
        },
        "nlu": {
            "llm_hints": {
                "aliases": ["weather app"],
                "actions": [{"id": "weather.open", "intent": "desktop.open_modal"}],
            },
            "nlu_hints": {
                "examples": ["open weather"],
                "side_effect_class": "runtime_write",
            },
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_builder_task_schema_accepts_teacher_handoff_packet() -> None:
    schema = _load_schema("builder.task.v1.schema.json")
    payload = {
        "task_id": "btask.123",
        "kind": "development_task",
        "status": "proposed",
        "source": {
            "type": "nlu_teacher",
            "text": "build a weather dashboard",
            "request_id": "req.1",
            "candidate_id": "cand.123",
        },
        "requested_behavior": "Create a weather dashboard skill and scenario.",
        "target": {"type": "skill", "id": "weather_dashboard_skill"},
        "context_snapshot": {
            "webspace_id": "desktop",
            "current_scenario": "web_desktop",
            "intent": "weather.current",
        },
        "artifact_hints": {
            "preferred_kind": "skill",
            "preferred_id": "weather_dashboard_skill",
            "create_new": True,
            "template_id": "skill_default",
        },
        "side_effect_class": "runtime_write",
        "privacy": {
            "utterance_retention": "bounded_text",
            "contains_personal_data": False,
        },
        "acceptance": {
            "checks": ["schema_valid", "tests_pass"],
            "replay_phrase": "build a weather dashboard",
            "expected_result": "Skill draft validates and exposes a dashboard.",
        },
        "links": {
            "candidate_id": "cand.123",
            "request_id": "req.1",
            "origin_scenario_id": "web_desktop",
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_builder_draft_schema_accepts_draft_workspace_metadata() -> None:
    schema = _load_schema("builder.draft.v1.schema.json")
    payload = {
        "draft_id": "draft.weather.1",
        "task_id": "btask.123",
        "status": "draft",
        "source": {
            "type": "human_idea",
            "text": "build a weather dashboard",
            "request_id": "req.1",
        },
        "artifact": {
            "kind": "skill",
            "id": "weather_dashboard_skill",
            "template_id": "skill_default",
            "draft_root": "drafts/weather_dashboard_skill",
            "files": [
                {"path": "skill.yaml", "role": "manifest", "required": True},
                {"path": "handlers/main.py", "role": "handler", "required": True},
                {"path": "tests/test_module_integrity.py", "role": "test", "required": True},
            ],
        },
        "metadata": {
            "source_idea": "Create a weather dashboard skill and scenario.",
            "assumptions": ["Weather source is read-only."],
            "risk_notes": ["External weather API keys require human review."],
            "expected_tests": ["schema validation", "handler smoke test"],
            "route_plan_required": True,
            "human_review_required": True,
        },
        "quality_gates": {
            "schemas": ["skill.schema.json"],
            "tests": ["pytest tests/test_module_integrity.py"],
            "previews": ["route plan review"],
            "requires_human_approval": ["external IO", "new secrets"],
        },
        "links": {
            "builder_task_id": "btask.123",
            "request_id": "req.1",
        },
    }

    Draft202012Validator(schema).validate(payload)


def test_builder_draft_schema_accepts_default_template_metadata() -> None:
    schema = _load_schema("builder.draft.v1.schema.json")
    root = Path(__file__).resolve().parents[1] / "src" / "adaos"
    template_paths = [
        root / "skills_templates" / "skill_default" / "builder.draft.json",
        root / "scenario_templates" / "scenario_default" / "builder.draft.json",
    ]

    for path in template_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(payload)


def test_scenario_schema_accepts_default_builder_template_manifest() -> None:
    schema = _load_schema("scenario.schema.json")
    root = Path(__file__).resolve().parents[1] / "src" / "adaos"
    payload = json.loads(
        (root / "scenario_templates" / "scenario_default" / "scenario.json").read_text(encoding="utf-8")
    )

    Draft7Validator(schema).validate(payload)


def test_nlu_teacher_schema_accepts_contract_bundle() -> None:
    schema = _load_schema("nlu.teacher.v1.schema.json")
    payload = {
        "request_thread": {
            "request_id": "req.1",
            "thread_id": "thread.1",
            "previous_request_id": "req.0",
            "correction_target_id": "cand.0",
            "text": "open weather",
            "source_channel": "voice",
            "scope": {
                "channel": "voice",
                "route_id": "voice_chat",
                "webspace_id": "desktop",
                "scenario_id": "web_desktop",
                "locale": "en",
                "privacy_boundary": "workspace",
            },
            "idempotency": {
                "request_capture": "idem.request.1",
                "llm_proposal": "idem.llm.1",
                "preview": "idem.preview.1",
            },
        },
        "action_candidate": {
            "id": "act.1",
            "candidate_id": "cand.1",
            "request_id": "req.1",
            "class": "interface_action",
            "intent": "desktop.open_modal",
            "slots": {"modal_id": "weather_modal"},
            "owner": {"type": "scenario", "id": "web_desktop"},
            "side_effect_class": "ui_navigation",
            "status": "action_previewed",
            "phrase_preview": {"ok": True},
            "action_preview": {"ok": True, "action_id": "desktop.modal.open"},
            "promotion": {
                "state": "local_learned",
                "portability": "scenario-local",
                "public_export_allowed": False,
                "privacy_gate": "operator_approval_required",
            },
            "provenance": {
                "source": "nlu_teacher",
                "webspace_id": "desktop",
                "request_id": "req.1",
                "candidate_id": "cand.1",
                "operator_action": "apply",
                "mcp_bearer_embedded": False,
            },
            "privacy": {
                "retention_policy": "nlu.teacher.retention.v1",
                "promotion_policy": "nlu.teacher.promotion.v1",
                "raw_utterance_scope": "local_state",
                "public_promotion_requires_review": True,
            },
        },
        "template_candidate": {
            "id": "tplcand.1",
            "candidate_id": "cand.1",
            "request_id": "req.1",
            "class": "template_candidate",
            "engine": "regex",
            "intent": "desktop.open_modal",
            "owner": {"type": "scenario", "id": "web_desktop"},
            "operation": "add_regex_rule",
            "patch": {"pattern": "open weather"},
            "linked_action_candidate_id": "act.1",
            "status": "phrase_previewed",
            "promotion": {
                "state": "local_learned",
                "portability": "scenario-local",
                "public_export_allowed": False,
            },
            "provenance": {
                "source": "nlu_teacher",
                "request_id": "req.1",
                "candidate_id": "cand.1",
                "mcp_bearer_embedded": False,
            },
        },
        "clarification_session": {
            "id": "clarify.1",
            "request_id": "req.1",
            "thread_id": "thread.1",
            "status": "rejected",
            "uncertainty_kind": "candidate_confirmation",
            "question": "Open Weather?",
            "allowed_answers": [{"id": "yes"}, {"id": "no"}],
            "rejected_candidates": ["cand.1"],
            "negative_feedback": {
                "reason": "voice_confirmation_rejected",
                "answer": "no",
            },
            "attempt": 1,
        },
        "response_policy": {
            "decision": "confirm",
            "requires_confirmation": True,
            "allowed_side_effects": ["read_only", "ui_navigation"],
            "reason": "voice confirmation required",
        },
        "mcp_capability_profile": {
            "profile_id": "NLUTeacherDryRun",
            "mode": "dry_run_preview",
            "tools": ["nlu_authoring.get_context", "nlu_authoring.check_phrase", "desktop.preview_action"],
            "dispatch_allowed": False,
            "training_mutation_allowed": False,
            "requires_operator_approval": True,
        },
    }

    Draft202012Validator(schema).validate(payload)
