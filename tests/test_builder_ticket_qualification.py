from __future__ import annotations

import json
from pathlib import Path

from adaos.services.builder.repair import BuilderRepairService
from adaos.services.builder.ticket_qualification import prepare_repair_qualification
from adaos.services.development_tickets import DevelopmentTicketService


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
    assert planned["execution_budget"]["max_billable_tokens"] == 264000
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
