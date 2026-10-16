"""Disclosed model-free native Builder episode, plus the stable binding pilot."""
from copy import deepcopy
import json
from pathlib import Path

from test_builder_semantic_label_requests import native_label_builder, graph_project  # noqa: F401
from test_builder_application_cbs import _acceptance, _contract
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.e2e.component_contract_conformance import load_slice, matched_runtime_episodes
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from adaos.services.builder.cbs import compile_prototype_cbs
from adaos.services.applications.cbs import ApplicationCBSService
from adaos.domain.capability_binding_state import CapabilityContract


def test_two_native_chat_increments_preserve_data_and_message_requirement_links(native_label_builder, monkeypatch):
    module, workflow, root, document = native_label_builder
    for name, value in {
        "_resolve_builder_context_for_turn": lambda *a: {"builder_webspace_id": "builder-host"},
        "_parse_builder_command": lambda *a, **kw: {"intent": ""},
        "_requested_scenario_id_from_meta": lambda *a: "",
        "_project_external_user_turn": lambda *a, **kw: None,
        "_route_automation_chat": lambda **kw: None,
        "_safe_emit_chat": lambda *a, **kw: None,
        "_dialog_state": lambda *a, **kw: {},
    }.items():
        monkeypatch.setattr(module, name, value)
    original_records = deepcopy(document["resources"])
    for identity, ref, title in (("episode-1", "work-list", "Work queue"), ("episode-2", "people-list", "Portraits")):
        current = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
        old = next(view for view in current["views"] if view["id"] == ref)["title"]["en"]
        result = module.chat(text=f'Rename view "{old}" to "{title}" in English', webspace_id="builder-host",
                             _meta={"source_message_id": identity})
        assert result["status"] == "semantic_label_applied", result
        assert result["model_calls"] == 0
    current = json.loads((root / "semantic.webui.json").read_text(encoding="utf-8"))
    assert current["resources"] == original_records
    assert next(view for view in current["views"] if view["id"] == "work-list")["title"]["en"] == "Work queue"
    assert next(view for view in current["views"] if view["id"] == "people-list")["title"]["en"] == "Portraits"
    projected = workflow.traceability("scenario", root.name)
    nodes = projected["graph"]["nodes"]
    messages = {row["ref"] for row in nodes if row["kind"] == "source_message"}
    assert {"episode-1", "episode-2"} <= messages
    assert len([row for row in projected["graph"]["edges"] if row["relation"] == "implemented_by"]) >= 2


def test_native_issue_compaction_does_not_merge_distinct_phase_obligations(native_label_builder):
    module, _, _, _ = native_label_builder
    row = {"issue_id": "one", "title": "Requirement", "lane": "automation",
           "acceptance_criteria": ["Bounded expectation"], "applicable_phases": ["prototype"]}
    assert module._compact_builder_change_issues([row, {**row, "issue_id": "two", "applicable_phases": ["automation"]}],
        max_count=1, change_id="change") == []
    compacted = module._compact_builder_change_issues([
        {**row, "semantic_refs": ["view:one"], "source_message_ids": ["m1"]},
        {**row, "issue_id": "two", "semantic_refs": ["view:two"], "source_message_ids": ["m2"]}],
        max_count=1, change_id="change")
    assert compacted[0]["applicable_phases"] == ["prototype"]
    assert compacted[0]["semantic_refs"] == ["view:one", "view:two"]
    assert compacted[0]["source_message_ids"] == ["m1", "m2"]


def test_ui_increment_reuses_portable_contract_and_rejects_incompatible_contract_before_generation(tmp_path):
    acceptance = _acceptance()
    acceptance["automation_requirements"] = []
    def signed(value):
        value = deepcopy(value)
        value["digest"] = canonical_payload_digest({k: v for k, v in value.items() if k != "digest"})
        return value
    service = ApplicationCBSService(tmp_path / "state")
    first = compile_prototype_cbs(signed(acceptance))
    service.register(first)
    contracts = [_contract("capability:application.ui.render"), _contract("capability:resource.records.manage")]
    assert service.semantic_viability("scenario:applications", capability_contracts=contracts)["viable"]
    acceptance.update(revision="18", webui_digest="sha256:" + "d" * 64)
    changed = compile_prototype_cbs(signed(acceptance))
    service.register(changed, expected_previous_digest=first["compilation_digest"])
    assert service.semantic_viability("scenario:applications", capability_contracts=contracts)["viable"]
    assert {row["contract_range"] for row in first["requirements"]} == {row["contract_range"] for row in changed["requirements"]}
    assert not service.semantic_viability("scenario:applications", capability_contracts=[]) ["viable"]
    incompatible = [CapabilityContract.create(capability_ref=contract.capability_ref, version="2.0.0", title="Incompatible major",
        operations=({"operation_id": "execute", "input_schema": {"type": "object"},
                     "output_schema": {"type": "object"}, "errors": []},)) for contract in contracts]
    assert not service.semantic_viability("scenario:applications", capability_contracts=incompatible)["viable"]


def test_existing_contract_survives_real_binding_substitution_after_ui_increment(tmp_path):
    document, _ = load_slice(Path(__file__).resolve().parents[1])
    before = compile_semantic_prototype(document, project_ref="project:feedback-pilot")
    changed = deepcopy(document)
    changed["views"][0]["title"]["en"] = "Reviewed work"
    compiled = compile_semantic_prototype(changed, project_ref="project:feedback-pilot")
    # These are actual isolated providers/state, not a production conformance claim.
    rows = matched_runtime_episodes(compiled, tmp_path / "episodes")
    assert all(row["ok"] for row in rows), rows
    assert next(row for row in rows if row["episode"] == "binding_swap")["binding_substitution"]
    assert before["semantic_document"]["resources"] == compiled["semantic_document"]["resources"]
