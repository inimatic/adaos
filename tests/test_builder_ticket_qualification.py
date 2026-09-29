from __future__ import annotations

import json
from pathlib import Path

from adaos.services.builder.repair import BuilderRepairService
from adaos.services.builder.ticket_qualification import (
    LANGUAGE_QUALIFICATION_PROPOSAL_SCHEMA,
    prepare_repair_qualification,
    resolve_language_qualification_proposal,
)
from adaos.services.development_tickets import (
    BUILDER_CLARIFICATION_PENDING_ACTION_KIND,
    DevelopmentTicketService,
)


def _source_tree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "handlers").mkdir()
    (root / "tests").mkdir()
    (root / "webui.json").write_text(
        json.dumps(
            {
                "semantic": {
                    "views": [
                        {"id": "subscription_summary", "title": "Current subscription usage"},
                    ]
                },
                "actions": [{"id": "refresh", "title": "Refresh usage"}],
            }
        ),
        encoding="utf-8",
    )
    (root / "handlers" / "main.py").write_text(
        "def load_subscription_usage():\n    return {'status': 'ready'}\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_subscription_status_skill.py").write_text(
        "def test_refresh_usage():\n    assert True\n",
        encoding="utf-8",
    )
    (root / "prompt_state.json").write_text(
        json.dumps({"transcript": "subscription refresh usage" * 500}),
        encoding="utf-8",
    )
    return root


def _manifest_source_tree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "skill.yaml").write_text(
        """name: demo_skill
data_routes:
- surface: tool:dataset_export
  route: skill-local
  first_paint: explicit tool response only
  recovery: rerun export
  guard_visibility:
    degraded_state: export reports failure
""",
        encoding="utf-8",
    )
    return root


def _scenario_source_tree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "ui_revisions").mkdir()
    (root / "llm_jobs").mkdir()
    current_ui = {
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "layout": {"areas": [{"id": "planned", "label": "Запланировано"}]},
                        "widgets": [
                            {
                                "id": "planned",
                                "title": "Запланировано",
                                "dataSource": {
                                    "kind": "static",
                                    "value": [
                                        {"id": "one", "status": "Запланировано"},
                                        {"id": "two", "status": "Запланировано"},
                                    ],
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    (root / "webui.json").write_text(
        json.dumps(current_ui, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "scenario.yaml").write_text(
        "id: kanban\nversion: 0.1.0\nname: kanban\n",
        encoding="utf-8",
    )
    for path in (
        root / "scenario.json",
        root / "builder.draft.json",
        root / "ui_revisions" / "002.json",
        root / "llm_jobs" / "job.json",
    ):
        path.write_text(
            json.dumps(current_ui, ensure_ascii=False),
            encoding="utf-8",
        )
    return root


def _ticket(summary: str) -> dict:
    return {
        "ticket_id": "dticket.test",
        "summary": summary,
        "component_ref": "skill:subscription_status_skill",
        "target_scope": {
            "type": "skill",
            "id": "subscription_status_skill",
            "surface": "modal",
        },
        "evidence_refs": [],
    }


def _prompt_facts(**overrides) -> dict:
    facts = {
        "schema": "adaos.builder.prompt_facts.v1",
        "concepts": ["ui", "data"],
        "surface_kinds": ["modal"],
        "operation_kinds": ["read", "command"],
        "data_planes": ["tool_details"],
        "effects": ["read_only"],
        "requires_i18n": False,
        "requires_access": False,
        "requires_conversation": False,
        "requires_lifecycle": False,
    }
    facts.update(overrides)
    return facts


def test_local_qualification_maps_plain_language_to_bounded_source(tmp_path: Path) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")

    result = prepare_repair_qualification(
        _ticket(
            "Данные в виджете Subscription загружаются не сразу, а Refresh должен обновлять расходы."
        ),
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="subscription_status_skill",
    )

    assert result["ready"] is True
    assert result["confidence"] == "high"
    assert result["model_call_expected"] is False
    assert result["estimated_model_tokens"] == 0
    repair = result["builder_repair"]
    assert repair["profile"] == "surgical_ui"
    assert set(repair["target_files"]) == {
        "skills/subscription_status_skill/handlers/main.py",
        "skills/subscription_status_skill/webui.json",
        "skills/subscription_status_skill/tests/test_subscription_status_skill.py",
    }
    assert not any("prompt_state" in path for path in repair["target_files"])
    assert len(repair["source_preconditions"]) == 3
    assert all(item["sha256"].startswith("sha256:") for item in repair["source_preconditions"])


def test_typed_ui_text_rename_uses_canonical_webui_without_model(tmp_path: Path) -> None:
    source = _scenario_source_tree(tmp_path / "kanban")
    ticket = {
        "ticket_id": "dticket.rename",
        "summary": "Переименуй колонку «Запланировано» в «Очередь». Больше ничего не меняй.",
        "component_ref": "scenario:kanban",
        "target_scope": {"type": "scenario", "id": "kanban"},
        "metadata": {
            "operation_kinds": ["rename_ui_text"],
            "surface_kind": "scenario",
            "requires_i18n": True,
            "requires_lifecycle": True,
        },
        "evidence_refs": [],
    }

    result = prepare_repair_qualification(
        ticket,
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="scenario",
        object_id="kanban",
    )

    assert result["ready"] is True
    assert result["confidence"] == "high"
    assert result["model_call_expected"] is False
    assert result["estimated_model_tokens"] == 0
    assert result["source_index"]["file_count"] == 2
    repair = result["builder_repair"]
    assert repair["profile"] == "surgical_ui"
    assert repair["target_files"] == ["scenarios/kanban/webui.json"]
    assert repair["prompt_facts"]["operation_kinds"] == ["update"]
    assert repair["prompt_facts"]["requires_i18n"] is True
    assert repair["prompt_facts"]["requires_lifecycle"] is True
    operation = repair["structured_edits"]["operations"][0]
    assert operation == {
        "id": "rename-current-ui-text",
        "op": "replace_text",
        "path": "scenarios/kanban/webui.json",
        "old": "Запланировано",
        "new": "Очередь",
        "expected_count": 4,
    }


def test_local_qualification_closes_public_tool_graph_before_model_use(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")
    (source / "skill.yaml").write_text(
        "name: subscription_status_skill\ntools: []\nexports:\n  tools: []\n",
        encoding="utf-8",
    )

    result = prepare_repair_qualification(
        _ticket("The Refresh button must update subscription usage data."),
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="subscription_status_skill",
    )

    assert result["ready"] is True
    repair = result["builder_repair"]
    assert repair["contract_closure"]["kind"] == "skill_public_tool_graph"
    assert repair["contract_closure"]["required_paths"] == [
        "skills/subscription_status_skill/skill.yaml",
        "skills/subscription_status_skill/handlers/main.py",
        "skills/subscription_status_skill/webui.json",
    ]
    assert set(repair["contract_closure"]["required_paths"]).issubset(
        repair["target_files"]
    )
    assert {
        item["path"] for item in repair["source_preconditions"]
    }.issuperset(repair["contract_closure"]["required_paths"])
    assert "contract:skill_public_tool_graph" in repair["target_refs"]


def test_local_qualification_stops_when_language_does_not_resolve_source(tmp_path: Path) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")

    result = prepare_repair_qualification(
        _ticket("Сделайте это лучше."),
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="subscription_status_skill",
    )

    assert result["ready"] is False
    assert result["status"] == "needs_clarification"
    assert result["recommended_next"] == "bounded_language_qualification_or_user_clarification"
    assert result["model_call_expected"] is False


def test_language_proposal_is_resolved_only_through_authoritative_source_index(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")

    result = resolve_language_qualification_proposal(
        _ticket("Make the visible subscription refresh behavior clearer."),
        {
            "schema": LANGUAGE_QUALIFICATION_PROPOSAL_SCHEMA,
            "concepts": ["ui", "data"],
            "prompt_facts": _prompt_facts(),
            "candidate_paths": [
                "skills/subscription_status_skill/webui.json",
                "skills/subscription_status_skill/handlers/main.py",
            ],
            "confidence": 0.94,
            "clarification_question": None,
            "rationale": "The named refresh UI and its handler own the behavior.",
        },
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="subscription_status_skill",
    )

    assert result["ready"] is True
    assert result["concepts"] == ["data", "ui"]
    assert result["builder_repair"]["concepts"] == ["data", "ui"]
    assert set(result["builder_repair"]["target_files"]) == {
        "skills/subscription_status_skill/handlers/main.py",
        "skills/subscription_status_skill/tests/test_subscription_status_skill.py",
        "skills/subscription_status_skill/webui.json",
    }
    assert len(result["builder_repair"]["source_preconditions"]) == 3


def test_language_proposal_rejects_invented_source_path(tmp_path: Path) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")

    result = resolve_language_qualification_proposal(
        _ticket("Make this behavior clearer."),
        {
            "schema": LANGUAGE_QUALIFICATION_PROPOSAL_SCHEMA,
            "concepts": ["ui"],
            "prompt_facts": _prompt_facts(concepts=["ui"]),
            "candidate_paths": [
                "skills/subscription_status_skill/handlers/invented.py"
            ],
            "confidence": 0.99,
            "clarification_question": None,
            "rationale": "Proposed handler.",
        },
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="subscription_status_skill",
    )

    assert result["ready"] is False
    assert result["status"] == "needs_clarification"
    assert result["recommended_next"] == "user_clarification"
    assert result["invalid_candidate_paths"] == [
        "skills/subscription_status_skill/handlers/invented.py"
    ]


def test_local_qualification_routes_public_sdk_usage_to_subnet_data(tmp_path: Path) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")
    ticket = _ticket(
        "В окне добавь расход Codex: сколько токенов использовано и осталось. "
        "Данные обновляются через публичный SDK AdaOS."
    )
    ticket["component_ref"] = "modal:subscription_status_modal"

    result = prepare_repair_qualification(
        ticket,
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="subscription_status_skill",
    )

    assert result["ready"] is True
    assert set(result["concepts"]) >= {"data", "subnet", "ui"}
    assert result["builder_repair"]["profile"] == "subnet_data_integration"
    assert result["builder_repair"]["requires_root_mcp"] is True
    assert result["builder_repair"]["target_refs"][0] == (
        "modal:subscription_status_modal"
    )


def test_validation_gate_qualification_targets_exact_manifest_with_structured_edit(
    tmp_path: Path,
) -> None:
    source = _manifest_source_tree(tmp_path / "demo_skill")
    ticket = {
        "ticket_id": "dticket.validation",
        "summary": "Skill demo_skill failed the validation publication gate",
        "component_ref": "skill:demo_skill",
        "target_scope": {"type": "skill", "id": "demo_skill"},
        "metadata": {
            "error": (
                "RuntimeError: Generated project validation failed: "
                "skills/demo_skill/skill.yaml: data_routes.budget_missing: "
                "browser data route must declare a bounded budget "
                "(skill.yaml:data_routes[0].budget)"
            )
        },
        "evidence_refs": [],
    }

    result = prepare_repair_qualification(
        ticket,
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="demo_skill",
    )

    assert result["ready"] is True
    assert result["confidence"] == "high"
    assert result["validation_findings"] == [
        {
            "path": "skills/demo_skill/skill.yaml",
            "code": "data_routes.budget_missing",
        }
    ]
    repair = result["builder_repair"]
    assert repair["target_files"] == ["skills/demo_skill/skill.yaml"]
    assert repair["requires_root_mcp"] is False
    operation = repair["structured_edits"]["operations"][0]
    assert operation["path"] == "skills/demo_skill/skill.yaml"
    assert "max_payload_bytes: 65536" in operation["new"]
    assert "max_payload_bytes" not in operation["old"]


def test_webui_tool_validation_qualification_includes_manifest_ui_and_handler(
    tmp_path: Path,
) -> None:
    source = _manifest_source_tree(tmp_path / "demo_skill")
    (source / "handlers").mkdir()
    (source / "handlers" / "main.py").write_text(
        """from adaos.sdk.core.decorators import tool

@tool(summary="Refresh usage")
def refresh_usage(webspace_id: str = "desktop"):
    return {"ok": True, "used": 10, "remaining": 90}
""",
        encoding="utf-8",
    )
    (source / "webui.json").write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "type": "callSkill",
                        "target": "demo_skill.refresh_usage",
                        "params": {"webspace_id": "$runtime.webspace_id"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    ticket = {
        "ticket_id": "dticket.webui-tool-validation",
        "summary": "Skill demo_skill failed the validation publication gate",
        "component_ref": "skill:demo_skill",
        "target_scope": {"type": "skill", "id": "demo_skill"},
        "metadata": {
            "error": (
                "RuntimeError: Generated project validation failed: "
                "skills/demo_skill/skill.yaml: webui.action.skill_tool_unknown: "
                "callSkill references undeclared tool 'demo_skill.refresh_usage'. "
                "(webui.json:$.actions[0])"
            )
        },
        "evidence_refs": [],
    }

    result = prepare_repair_qualification(
        ticket,
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="demo_skill",
    )

    assert result["ready"] is True
    assert result["model_call_expected"] is True
    repair = result["builder_repair"]
    assert repair["target_files"] == [
        "skills/demo_skill/skill.yaml",
        "skills/demo_skill/handlers/main.py",
        "skills/demo_skill/webui.json",
    ]
    assert "structured_edits" not in repair
    assert any(
        "demo_skill.refresh_usage" in check and "input/output schemas" in check
        for check in repair["acceptance_checks"]
    )


def test_pytest_manifest_tool_set_failure_qualifies_as_structured_edit(
    tmp_path: Path,
) -> None:
    source = _manifest_source_tree(tmp_path / "demo_skill")
    (source / "handlers").mkdir()
    (source / "handlers" / "main.py").write_text(
        """from adaos.sdk.core.decorators import tool

@tool("refresh_codex_usage")
def refresh_codex_usage():
    return {"ok": True}
""",
        encoding="utf-8",
    )
    (source / "webui.json").write_text(
        json.dumps(
            {
                "actions": [
                    {
                        "type": "callSkill",
                        "target": "demo_skill.refresh_codex_usage",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (source / "tests").mkdir()
    test_path = source / "tests" / "test_manifest.py"
    test_path.write_text(
        """def test_manifest_tools(manifest):
    assert {tool["name"] for tool in manifest["tools"]} == {
        "ping",
        "refresh",
    }
""",
        encoding="utf-8",
    )
    ticket = {
        "ticket_id": "dticket.pytest-manifest-tools",
        "summary": "Skill demo_skill failed the validation publication gate",
        "component_ref": "skill:demo_skill",
        "target_scope": {"type": "skill", "id": "demo_skill"},
        "metadata": {
            "error": (
                "skills/demo_skill/tests: packaged pytest failed:\n"
                "Extra items in the left set:\n"
                "E       'refresh_codex_usage'\n"
                "skills\\demo_skill\\tests\\test_manifest.py:2: AssertionError"
            )
        },
        "evidence_refs": [],
    }

    result = prepare_repair_qualification(
        ticket,
        development_source={"status": "source_available", "dev_source_path": str(source)},
        object_type="skill",
        object_id="demo_skill",
    )

    assert result["ready"] is True
    assert result["model_call_expected"] is False
    assert result["validation_findings"] == [
        {
            "path": "skills/demo_skill/tests/test_manifest.py",
            "code": "pytest.failed",
        }
    ]
    repair = result["builder_repair"]
    assert repair["target_files"] == ["skills/demo_skill/tests/test_manifest.py"]
    operation = repair["structured_edits"]["operations"][0]
    assert '"refresh_codex_usage",' in operation["new"]
    assert '"refresh_codex_usage",' not in operation["old"]


def test_service_can_apply_high_confidence_local_qualification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "state"
    source = _source_tree(tmp_path / "subscription_status_skill")
    service = DevelopmentTicketService(state_dir=state_dir)
    signal = service.capture_signal(
        kind="feedback_note",
        summary="В модалке Subscription кнопка Refresh должна сразу обновлять данные.",
        target_scope={
            "type": "skill",
            "id": "subscription_status_skill",
            "source": "workspace",
            "surface": "modal",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="feedback",
        status="captured",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["ticket"]
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "subscription_status_skill",
            "dev_source_path": str(source),
        },
    )

    prepared = service.prepare_builder_repair_qualification(ticket["ticket_id"])
    assert prepared["applied"] is False
    assert prepared["qualification_candidate"]["ready"] is True
    assert prepared["ticket"]["revision"] == ticket["revision"]

    applied = service.prepare_builder_repair_qualification(
        ticket["ticket_id"],
        apply=True,
        expected_revision=ticket["revision"],
    )
    assert applied["applied"] is True
    assert applied["ticket"]["revision"] == ticket["revision"] + 1
    assert applied["autonomous_repair_qualification"]["ready"] is True
    assert applied["autonomous_repair_qualification"]["source_preconditions"]
    assert applied["ticket"]["history"][-1]["kind"] == "builder_repair_requalified"


def test_service_uses_root_accounted_language_qualification_only_after_local_miss(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "state"
    source = _source_tree(tmp_path / "subscription_status_skill")
    service = DevelopmentTicketService(state_dir=state_dir)
    signal = service.capture_signal(
        kind="feedback_note",
        summary="Make this better.",
        target_scope={
            "type": "skill",
            "id": "subscription_status_skill",
            "source": "workspace",
            "surface": "modal",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="feedback",
        status="captured",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["ticket"]
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "subscription_status_skill",
            "dev_source_path": str(source),
        },
    )
    ticket = service._update_ticket(
        ticket["ticket_id"],
        pending_action_refs=[
            {
                "id": "pa.obsolete",
                "kind": BUILDER_CLARIFICATION_PENDING_ACTION_KIND,
                "status": "pending",
            }
        ],
    )
    cancelled_actions: list[tuple[str, str]] = []

    def cancel_pending_action(action_id: str, *, reason: str, **_kwargs):
        cancelled_actions.append((action_id, reason))
        return {"action": {"id": action_id, "status": "cancelled"}}

    monkeypatch.setattr(
        "adaos.services.pending_actions.cancel_pending_action",
        cancel_pending_action,
    )
    calls: list[dict] = []

    def fake_llm(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return {
            "id": "resp.language.1",
            "output_text": json.dumps(
                {
                    "schema": LANGUAGE_QUALIFICATION_PROPOSAL_SCHEMA,
                    "concepts": ["ui", "data"],
                    "prompt_facts": _prompt_facts(),
                    "candidate_paths": [
                        "skills/subscription_status_skill/webui.json",
                        "skills/subscription_status_skill/handlers/main.py",
                    ],
                    "confidence": 0.93,
                    "clarification_question": None,
                    "rationale": "The visible modal and refresh handler are the bounded target.",
                    "development_feedback": [
                        {
                            "category": "ambiguous_contract",
                            "summary": "The refresh contract does not define stale-value behavior.",
                            "blocking": False,
                            "confidence": 0.88,
                            "impact": ["comprehension"],
                            "target_refs": ["sdk:subscription.refresh"],
                            "details": "Two public behaviors are plausible.",
                            "recommendation": "Declare the last-value policy."
                        }
                    ],
                }
            ),
            "usage": {
                "input_tokens": 310,
                "output_tokens": 92,
                "total_tokens": 402,
                "input_tokens_details": {"cached_tokens": 120},
            },
        }

    result = service.qualify_builder_repair_language(
        ticket["ticket_id"],
        apply=True,
        expected_revision=ticket["revision"],
        llm_call=fake_llm,
    )

    assert result["applied"] is True
    assert result["qualification_mode"] == "bounded_language_llm"
    assert result["language_model_called"] is True
    assert result["language_qualification_usage"]["total_tokens"] == 402
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 800
    assert "reasoning" not in calls[0]
    assert calls[0]["text"] == {"format": {"type": "json_object"}}
    request_payload = json.loads(calls[0]["messages"][1]["content"])
    facts_contract = request_payload["prompt_facts_contract"]
    assert "surface_kinds" in facts_contract["required_keys"]
    assert "surface" not in facts_contract["required_keys"]
    assert set(facts_contract["boolean_keys"]) == {
        "requires_i18n",
        "requires_access",
        "requires_conversation",
        "requires_lifecycle",
    }
    assert request_payload["output_example"]["schema"] == (
        "adaos.builder.language_qualification_proposal.v1"
    )
    assert request_payload["output_example"]["prompt_facts"]["schema"] == (
        "adaos.builder.prompt_facts.v1"
    )
    assert calls[0]["request_id"].startswith("builder.language_qualification.")
    updated = result["ticket"]
    assert updated["metadata"]["builder_repair"]["concepts"] == ["data", "ui"]
    prompt_facts = updated["metadata"]["builder_repair"]["prompt_facts"]
    assert prompt_facts["concepts"] == ["data", "ui"]
    assert set(prompt_facts["surface_kinds"]) == {"modal", "ui"}
    assert {"read", "command"}.issubset(prompt_facts["operation_kinds"])
    assert "tool_details" in prompt_facts["data_planes"]
    assert updated["metadata"]["builder_language_qualification"]["status"] == "applied"
    assert any(
        item["kind"] == "builder_language_qualification"
        for item in updated["history"]
    )
    assert updated["history"][-1]["kind"] == "builder_clarifications_superseded"
    usage_ref = next(
        ref
        for ref in updated["evidence_refs"]
        if ref.get("type") == "llm_usage"
    )
    assert usage_ref["total_tokens"] == 402
    assert result["autonomous_repair_qualification"]["ready"] is True
    assert len(result["development_feedback"]) == 1
    assert cancelled_actions == [("pa.obsolete", "qualification_ready")]
    obsolete_ref = next(
        ref for ref in updated["pending_action_refs"] if ref["id"] == "pa.obsolete"
    )
    assert obsolete_ref["status"] == "cancelled"
    assert result["development_feedback"][0]["source"] == "pre_codex_llm"
    assert result["development_feedback"][0]["relation_refs"] == [
        {"type": "dev_ticket", "id": ticket["ticket_id"]}
    ]


def test_service_skips_language_model_when_deterministic_qualification_is_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")
    service = DevelopmentTicketService(state_dir=tmp_path / "state")
    signal = service.capture_signal(
        kind="feedback_note",
        summary="Rename the Subscription modal table title.",
        target_scope={
            "type": "skill",
            "id": "subscription_status_skill",
            "source": "workspace",
            "surface": "modal",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="feedback",
        status="captured",
        owner_area="skill",
    )["ticket"]
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "subscription_status_skill",
            "dev_source_path": str(source),
        },
    )

    def unexpected_llm(*_args, **_kwargs):
        raise AssertionError("deterministic qualification must not spend LLM tokens")

    result = service.qualify_builder_repair_language(
        ticket["ticket_id"],
        llm_call=unexpected_llm,
    )

    assert result["qualification_mode"] == "deterministic"
    assert result["language_model_called"] is False
    assert result["language_qualification_usage"] is None
    assert result["ticket"]["revision"] == ticket["revision"]


def test_language_qualification_publishes_text_clarification_and_reuses_answer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")
    service = DevelopmentTicketService(state_dir=tmp_path / "state")
    signal = service.capture_signal(
        kind="feedback_note",
        summary="The shape in the card is wrong.",
        target_scope={
            "type": "skill",
            "id": "subscription_status_skill",
            "source": "workspace",
            "surface": "modal",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="feedback",
        status="captured",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["ticket"]
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "subscription_status_skill",
            "dev_source_path": str(source),
        },
    )
    published: list[dict] = []

    def publish_pending_action(**kwargs):
        published.append(kwargs)
        return {
            "id": "pa.clarify.1",
            "kind": kwargs["kind"],
            "status": "pending",
            "created_at": 1.0,
        }

    monkeypatch.setattr(
        "adaos.services.pending_actions.publish_pending_action",
        publish_pending_action,
    )
    cancelled: list[tuple[str, str]] = []

    def cancel_pending_action(action_id: str, *, reason: str, **_kwargs):
        cancelled.append((action_id, reason))
        return {"action": {"id": action_id, "status": "cancelled"}}

    monkeypatch.setattr(
        "adaos.services.pending_actions.cancel_pending_action",
        cancel_pending_action,
    )

    def proposal(confidence: float, question: str | None):
        return {
            "id": "resp.clarification",
            "output_text": json.dumps(
                {
                    "schema": LANGUAGE_QUALIFICATION_PROPOSAL_SCHEMA,
                    "concepts": ["ui"],
                    "prompt_facts": _prompt_facts(),
                    "candidate_paths": [
                        "skills/subscription_status_skill/webui.json",
                    ],
                    "confidence": confidence,
                    "clarification_question": question,
                    "rationale": "The visual target needs one bounded identification.",
                }
            ),
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        }

    first = service.qualify_builder_repair_language(
        ticket["ticket_id"],
        expected_revision=ticket["revision"],
        llm_call=lambda *_args, **_kwargs: proposal(
            0.4, "Which visible element has the wrong shape?"
        ),
    )

    assert first["pending_action_published"] is True
    assert first["ticket"]["status"] == "waiting_for_user"
    assert published[0]["default_text_binding"] is True
    assert published[0]["request_text"] == "Which visible element has the wrong shape?"

    answered = service.handle_builder_clarification_response(
        ticket_id=ticket["ticket_id"],
        response_action_id="submit_answer",
        response_payload={"text": "The status badge inside the Global Root card."},
        pending_action_id="pa.clarify.1",
        qualification_request_id=first["pending_action"]["id"],
        requalify=False,
    )
    assert answered["ticket"]["metadata"]["clarification_responses"][-1]["answer"] == (
        "The status badge inside the Global Root card."
    )
    answered_ref = next(
        ref
        for ref in answered["ticket"]["pending_action_refs"]
        if ref["id"] == "pa.clarify.1"
    )
    assert answered_ref["status"] == "responded"

    calls: list[list[dict[str, str]]] = []

    def final_llm(messages, **_kwargs):
        calls.append(messages)
        return proposal(0.95, None)

    final = service.qualify_builder_repair_language(
        ticket["ticket_id"],
        apply=True,
        expected_revision=answered["ticket"]["revision"],
        llm_call=final_llm,
    )
    assert final["applied"] is True
    assert "The status badge inside the Global Root card." in calls[0][1]["content"]
    assert cancelled == []
    clarification_ref = next(
        ref
        for ref in final["ticket"]["pending_action_refs"]
        if ref["id"] == "pa.clarify.1"
    )
    assert clarification_ref["status"] == "responded"


def test_terminal_ticket_transitions_cancel_unanswered_builder_clarifications(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path / "state")
    cancelled: list[tuple[str, str]] = []

    def cancel_pending_action(action_id: str, *, reason: str, **_kwargs):
        cancelled.append((action_id, reason))
        return {"action": {"id": action_id, "status": "cancelled"}}

    monkeypatch.setattr(
        "adaos.services.pending_actions.cancel_pending_action",
        cancel_pending_action,
    )

    def ticket_with_pending(action_id: str) -> dict:
        signal = service.capture_signal(
            kind="feedback_note",
            summary=f"Clarify {action_id}",
            target_scope={"type": "skill", "id": action_id},
            source="client_feedback",
            owner_area="skill",
            component_ref=f"skill:{action_id}",
        )["signal"]
        ticket = service.ensure_ticket_for_signal(
            signal,
            kind="feedback",
            status="waiting_for_user",
            owner_area="skill",
            component_ref=f"skill:{action_id}",
        )["ticket"]
        return service._update_ticket(
            ticket["ticket_id"],
            pending_action_refs=[
                {
                    "id": action_id,
                    "kind": BUILDER_CLARIFICATION_PENDING_ACTION_KIND,
                    "status": "pending",
                }
            ],
        )

    resolved_source = ticket_with_pending("pa.resolve")
    resolved = service.record_resolution(
        resolved_source["ticket_id"],
        evidence_refs=[{"type": "test", "id": "browser.acceptance"}],
        actor="builder.automation",
        resolved_by_overlay="candidate.1",
        expected_revision=resolved_source["revision"],
    )["ticket"]
    closed_source = ticket_with_pending("pa.close")
    closed = service.close_ticket(
        closed_source["ticket_id"],
        reason="refused",
        actor="user:owner",
        expected_revision=closed_source["revision"],
    )

    assert cancelled == [
        ("pa.resolve", "ticket_resolved"),
        ("pa.close", "ticket_closed"),
    ]
    for ticket, action_id in ((resolved, "pa.resolve"), (closed, "pa.close")):
        ref = next(
            item for item in ticket["pending_action_refs"] if item["id"] == action_id
        )
        assert ref["status"] == "cancelled"
        assert ticket["history"][-1]["kind"] == "builder_clarifications_superseded"


def test_language_qualification_failure_falls_back_to_pending_clarification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _source_tree(tmp_path / "subscription_status_skill")
    service = DevelopmentTicketService(state_dir=tmp_path / "state")
    signal = service.capture_signal(
        kind="feedback_note",
        summary="The shape in the card is wrong.",
        target_scope={
            "type": "skill",
            "id": "subscription_status_skill",
            "source": "workspace",
            "surface": "modal",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="feedback",
        status="captured",
        owner_area="skill",
        component_ref="skill:subscription_status_skill",
    )["ticket"]
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "subscription_status_skill",
            "dev_source_path": str(source),
        },
    )
    published: list[dict] = []

    def publish_pending_action(**kwargs):
        published.append(kwargs)
        return {
            "id": "pa.clarify.failure",
            "kind": kwargs["kind"],
            "status": "pending",
            "created_at": 1.0,
        }

    monkeypatch.setattr(
        "adaos.services.pending_actions.publish_pending_action",
        publish_pending_action,
    )

    result = service.qualify_builder_repair_language(
        ticket["ticket_id"],
        expected_revision=ticket["revision"],
        llm_call=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("subnet_identity_required")
        ),
    )

    assert result["applied"] is False
    assert result["qualification_mode"] == "user_clarification_fallback"
    assert result["language_model_called"] is True
    assert result["language_qualification_usage"]["status"] == "failed"
    assert result["pending_action_published"] is True
    assert result["ticket"]["status"] == "waiting_for_user"
    assert published[0]["default_text_binding"] is True
    assert "what should happen instead" in published[0]["request_text"]


def test_package_plan_qualifies_related_tickets_once_with_bounded_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "state"
    source = _source_tree(tmp_path / "subscription_status_skill")
    service = DevelopmentTicketService(state_dir=state_dir)
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "subscription_status_skill",
            "project_id": "subscription_status",
            "dev_source_path": str(source),
        },
    )
    tickets: list[dict] = []
    for summary in (
        "Переименовать заголовок таблицы Subscription.",
        "Refresh должен сразу обновлять данные и расходы Subscription.",
    ):
        signal = service.capture_signal(
            kind="feedback_note",
            summary=summary,
            target_scope={
                "type": "skill",
                "id": "subscription_status_skill",
                "source": "workspace",
                "surface": "modal",
                "project_id": "subscription_status",
                "project_ref": "project:subscription_status",
            },
            source="client_feedback",
            owner_area="skill",
            component_ref="skill:subscription_status_skill",
        )["signal"]
        tickets.append(
            service.ensure_ticket_for_signal(
                signal,
                kind="feedback",
                status="ready_for_builder",
                owner_area="skill",
                component_ref="skill:subscription_status_skill",
            )["ticket"]
        )

    planned = service.plan_builder_package(
        [ticket["ticket_id"] for ticket in tickets],
        actor="builder.qualifier",
        repair_service=BuilderRepairService(state_dir=state_dir),
    )

    assert planned["ready"] is True
    assert planned["execution_budget"]["max_tokens"] == 33000
    assert planned["execution_budget"]["max_billable_tokens"] == 528000
    assert set(planned["repair_hints"]["target_files"]) == {
        "skills/subscription_status_skill/handlers/main.py",
        "skills/subscription_status_skill/webui.json",
        "skills/subscription_status_skill/tests/test_subscription_status_skill.py",
    }
    assert len(planned["repair_hints"]["source_preconditions"]) == 3
    assert all(
        service.autonomous_repair_qualification(ticket["ticket_id"])["ready"]
        for ticket in tickets
    )
    assert all(
        service.get_ticket(ticket["ticket_id"])["status"] == "in_builder"
        for ticket in tickets
    )
