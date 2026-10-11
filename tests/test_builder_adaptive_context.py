from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder_context import (
    ContextBudgetExceeded, ContextSection, automation_core_projection,
    compile_automation_prompt, compile_sections, context_strategy, context_use_receipt, digest,
)
from adaos.services.context_control import ContextAccessDenied, ContextControlService
from adaos.services.context_learning import aggregate_context_misses, validate_learning_rule
from adaos.services.prompt_rules import select_prompt_rules


def test_budget_counts_final_prompt_configuration_provider_and_reserve():
    sections = [ContextSection("authority", "exact authority"),
                ContextSection("expensive", "x" * 1000, required=False, utility=3),
                ContextSection("cheap", "useful" * 10, required=False, utility=2)]
    prompt, receipt = compile_sections(sections, capacity_tokens=240, reserve_tokens=20,
                                       target_tokens=100, overhead_text="schema" * 10,
                                       provider_overhead_tokens=20)
    assert "exact authority" in prompt and "useful" in prompt
    assert "x" * 1000 not in prompt
    assert receipt["provider_overhead_tokens"] == 20
    assert receipt["configuration_tokens"] == 15
    assert receipt["must_keep_complete"]
    with pytest.raises(ContextBudgetExceeded) as blocked:
        compile_sections([ContextSection("continuation", "q" * 800)], capacity_tokens=240,
                         reserve_tokens=20, overhead_text="s" * 80, provider_overhead_tokens=20)
    assert blocked.value.receipt["estimated_tokens"] > 220
    assert blocked.value.receipt["must_keep_complete"]


def test_exact_deduplication_merges_authority_and_sources():
    prompt, receipt = compile_sections([
        ContextSection("optional", "same exact bytes", required=False, source_refs=("one",)),
        ContextSection("required", "same exact bytes", source_refs=("two",)),
    ])
    assert prompt.count("same exact bytes") == 1
    assert receipt["selected"][0]["required"]
    assert receipt["selected"][0]["source_refs"] == ["one", "two"]
    assert receipt["omitted"][0]["reason"] == "duplicate"
    with pytest.raises(ValueError, match="duplicate context section key"):
        compile_sections([ContextSection("one", "a"), ContextSection("one", "b")])


def test_core_preserves_acceptance_data_policy_and_unknown_facets():
    packet = {"allowed_paths": ["one.py"], "artifacts": {"accepted": {"custom_identity": "opaque"}},
              "facets": {"constraints": {"acceptance_constraints": ["never publish"]},
                         "data_policy": {"first_install": "empty", "preserve_updates": True},
                         "future_authority": {"do_not_guess": True},
                         "ui_capabilities": {"catalog_digest": "fixed", "items": ["large"]}}}
    core = automation_core_projection(packet)
    assert core["artifacts"] == packet["artifacts"]
    for key in ("constraints", "data_policy", "future_authority"):
        assert core["facets"][key] == packet["facets"][key]
    assert core["facets"]["ui_capabilities"] == {"catalog_digest": "fixed"}
    assert packet["facets"]["ui_capabilities"]["items"] == ["large"]


@pytest.mark.parametrize("strategy", ["full", "core", "accents", "adaptive"])
def test_every_arm_preserves_root_target_and_mandatory_boundaries(strategy):
    prompt = """# task
## Approved implementation brief
Do exactly this.
## Task-scoped Root MCP route
bound_target_id=hub:only; no other targets.
## Selected AdaOS rule capsules
### adaos.builder.execution_boundary.v1 (authority)
Never publish.
### adaos.skill.sdk_boundary.v1 (authority)
Public SDK only.
### adaos.skill.member_subnet.v1 (optional)
Provider hint.
## Required result
Exact acceptance.
"""
    compiled, receipt = compile_automation_prompt(prompt, projection={}, packet_ref="packet.json", strategy=strategy)
    assert all(value in compiled for value in ("bound_target_id=hub:only", "Never publish", "Public SDK only", "Exact acceptance"))
    assert receipt["must_keep_complete"]
    assert ("Provider hint" in compiled) == (strategy != "core")
    assert context_strategy() == "full"


def test_opened_is_only_retrieval_evidence_not_causal_use():
    events = json.dumps({"type": "item.completed", "item": {
        "id": "read-1", "type": "command_execution", "command": "Get-Content exact.json", "status": "completed"}})
    receipt = context_use_receipt(events, selected=[{"name": "exact.json", "path": "/input/exact.json", "sha256": "fixed"}],
                                  final_message="Used exact.json", outcome={"independent_validation": "passed"},
                                  feedback=[{"category": "missing_contract", "target_refs": ["sdk:absent"]}])
    assert receipt["opened"][0]["evidence_kind"] == "observed_retrieval_attempt"
    assert receipt["declared_cited_refs"] == ["/input/exact.json"]
    assert receipt["causal_use"] == "not_established"
    assert receipt["tool_boundary_count"] == 1
    assert receipt["context_misses"][0]["contract_refs"] == ["sdk:absent"]


def test_fallback_is_per_dimension_and_false_is_not_unknown():
    selected = select_prompt_rules(target_type="skill", evidence="needs approval and pending action",
                                   facts={"profile": "resource_crud", "requires_conversation": False})
    human = next(item for item in selected if item["id"] == "adaos.skill.human_decision.v1")
    assert "text_fallback" in human["selection_reason"]
    assert "pending action" in human["search_hooks"]
    denied = select_prompt_rules(target_type="skill", evidence="needs approval and pending action",
                                 facts={"requires_human_decision": False})
    assert "adaos.skill.human_decision.v1" not in {item["id"] for item in denied}


def test_miss_aggregation_counts_tasks_not_retries_or_anecdotes():
    miss = {"category": "missing_contract", "contract_refs": ["sdk:one"],
            "scope": {"platform_version": "v1"}}
    rows = [{"run_ref": task, "logical_task_ref": task, "receipt_ref": "retained:" + task, "context_misses": [miss, miss]}
            for task in ("task:1", "task:1", "task:2")]
    group = aggregate_context_misses(rows)[0]
    assert group["distinct_task_count"] == 2
    assert group["review_eligible"] and group["status"] == "observation_only"
    assert aggregate_context_misses([{"run_ref": "one", "context_misses": [{"summary": "unexpected"}]}]) == []
    assert aggregate_context_misses([{ "logical_task_ref": "task:1", "context_misses": [
        {**miss, "scope": {"platform_version": "v1", "sdk_commit": None}}]}]) == []


def test_learning_requires_retained_observations_heldout_counterexamples_and_no_binding(tmp_path):
    service = ContextControlService(tmp_path)
    refs = [service.record_receipt({"run_ref": task, "logical_task_ref": task, "plan_ref": "plan:one", "context_misses": [
        {"category": "missing_contract", "contract_refs": ["sdk:one"], "scope": {"platform_version": "v1"}}]})["receipt_ref"]
            for task in ("task:1", "task:2")]
    content = {"schema": "adaos.context.learning_rule.v1", "invariant": "Bounded hypothesis",
               "applies_when": "Exact contract/version", "excludes": ["Other versions"],
               "expires_at": "2099-01-01T00:00:00Z", "observed_task_refs": ["task:1", "task:2"],
               "scope": {"platform_version": "v1", "contract_refs": ["sdk:one"]}}
    candidate = service.propose_memory({"kind": "procedural", "source_refs": refs, "content": content,
                                        "proposed_by": "llm:one", "authority_ref": "core:review"})
    with pytest.raises(ValueError, match="held-out"):
        service.qualify_memory(candidate["candidate_id"], qualified_by="reviewer", validation_refs=refs)
    evaluation = {"schema": "adaos.context.learning_evaluation.v1", "authority": "independent_validator",
                  "rule_digest": digest(content), "held_out_task_refs": ["task:3"],
                  "generalization_passed": True, "counterexamples_passed": True,
                  "authority_violations": 0, "evidence_refs": ["checks:heldout"]}
    evaluation_ref = service.put_artifact(evaluation)["ref"]
    service.qualify_memory(candidate["candidate_id"], qualified_by="reviewer", validation_refs=[evaluation_ref])
    with pytest.raises(ContextAccessDenied, match="authoritative subject bindings"):
        service.promote_memory(candidate["candidate_id"], actor_ref="reviewer", subject_refs=["platform:adaos"])
    promoted = service.promote_memory(candidate["candidate_id"], actor_ref="reviewer",
                                     subject_refs=["learning:one"], bind=False)
    assert promoted["capsule"]["valid_to"] == content["expires_at"]
    rejected_scope = service.resolve({"subject_refs": [promoted["capsule"]["capsule_id"]]})
    assert rejected_scope["denied"][0]["reason"] == "learned_rule_scope_not_admitted"
    allowed_scope = service.resolve({"subject_refs": [promoted["capsule"]["capsule_id"]],
                                     "policy": {"learning_scope": content["scope"]}})
    assert allowed_scope["required"][0]["ref"] == promoted["capsule"]["capsule_id"]
    with pytest.raises(ValueError, match="held-out"):
        validate_learning_rule(content, [{**evaluation, "held_out_task_refs": ["task:1"]}])
    mismatched = service.propose_memory({"kind": "procedural", "source_refs": refs,
                                        "content": {**content, "scope": {"platform_version": "v1", "contract_refs": ["sdk:unrelated"]}},
                                        "proposed_by": "llm:two", "authority_ref": "core:review"})
    with pytest.raises(ValueError, match="matching contract/scope"):
        service.qualify_memory(mismatched["candidate_id"], qualified_by="reviewer", validation_refs=[evaluation_ref])


def test_learning_scope_does_not_drop_commit_or_other_qualifiers(tmp_path):
    service = ContextControlService(tmp_path)
    learned = service.register_capsule({"kind": "procedural_memory", "subject_refs": ["learning:scoped"],
                                        "authority_ref": "reviewer", "content": {"invariant": "guidance"},
                                        "metadata": {"learning_scope": {"platform_version": "v1",
                                            "contract_refs": ["sdk:one"], "sdk_commit": "exact"}}}, bind=False)
    for current in ({"platform_version": "v1", "contract_refs": ["sdk:one"]},
                    {"platform_version": "v1", "contract_refs": ["sdk:one"], "sdk_commit": "other"}):
        resolution = service.resolve({"subject_refs": [learned["capsule_id"]], "policy": {"learning_scope": current}})
        assert resolution["denied"][0]["reason"] == "learned_rule_scope_not_admitted"
    admitted = service.resolve({"subject_refs": [learned["capsule_id"]], "policy": {"learning_scope": {
        "platform_version": "v1", "contract_refs": ["sdk:one"], "sdk_commit": "exact"}}})
    assert admitted["required"][0]["ref"] == learned["capsule_id"]


def test_context_planner_ranks_real_utility_per_token(tmp_path):
    service = ContextControlService(tmp_path)
    plan = service.plan({"token_budget": 100, "resolution": {
        "subject_refs": [], "required": [], "candidates": [
            {"ref": "large", "estimated_tokens": 100, "utility": 2},
            {"ref": "small", "estimated_tokens": 60, "utility": 1.5},
        ]}})
    assert [item["ref"] for item in plan["selected"]] == ["small"]
    assert plan["selected"][0]["selection_reason"] == "utility_per_token"


def test_required_dependency_cannot_be_weakened_by_an_optional_shortcut(tmp_path):
    service = ContextControlService(tmp_path)
    capsules = [service.register_capsule({"kind": "project", "subject_refs": ["project:one"],
                                        "authority_ref": "project:one",
                                        "content": {"name": name}}, bind=False)
                for name in ("root", "chain", "leaf")]
    root, chain, leaf = capsules
    for origin, target, required, relation in ((root, leaf, False, "bounded_by"),
                                               (root, chain, True, "uses"), (chain, leaf, True, "uses")):
        service.add_relationship({"from_capsule_id": origin["capsule_id"],
                                  "to_capsule_id": target["capsule_id"],
                                  "relation_type": relation, "required": required})
    resolution = service.resolve({"subject_refs": [root["capsule_id"]]})
    assert leaf["capsule_id"] in {item["ref"] for item in resolution["required"]}
    assert resolution["candidates"] == []


def test_worker_final_gate_covers_continuation_and_never_calls_model_on_overflow(tmp_path):
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker

    calls = []
    worker = LocalSkillFactoryWorker(state_dir=tmp_path / "state", repo_root=Path(__file__).resolve().parents[1],
                                    dev_skills_root=tmp_path / "skills", dev_scenarios_root=tmp_path / "scenarios",
                                    executor=lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ContextBudgetExceeded):
        worker._execute_codex(task_id="task:one", workspace=tmp_path / "workspace",
                              output_dir=tmp_path / "run/output", prompt="continuation" * 1_000,
                              assignment={"realize_request": {"artifacts": {
                                  "execution_budget": {"max_context_tokens": 15_000}}}})
    assert calls == []
    receipt = json.loads((tmp_path / "run/runtime/final-input-budget.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "blocked" and receipt["must_keep_complete"]
    assert receipt["provider_overhead_tokens"] == 14_000


def test_adaptive_capsules_separate_contract_facets_from_required_task(tmp_path, monkeypatch):
    from adaos.services.builder.automation import BuilderAutomationService

    monkeypatch.setenv("ADAOS_BUILDER_CONTEXT_STRATEGY", "adaptive")
    service = BuilderAutomationService(state_dir=tmp_path / "state", repo_root=Path(__file__).resolve().parents[1],
                                       dev_skills_root=tmp_path / "skills", dev_scenarios_root=tmp_path / "scenarios")
    packet = {"schema": "adaos.builder.context_packet.v1", "digest": "sha256:" + "a" * 64,
              "project": {"ref": "skill:demo"}, "change": {"change_id": "change:one", "intent": "Scoped change"},
              "facets": {"execution_authority": {"status": "present", "allowed_paths": ["skills/demo/"]},
                         "ui_capabilities": {"status": "present", "catalog_digest": "sha256:" + "b" * 64,
                                             "items": [{"id": "example", "summary": "contract" * 1000}]}}}
    control = service._compile_iteration_context(session={"session_id": "session:one", "iteration": 0,
                                                          "change_set_id": "one", "execution_budget": {"max_context_tokens": 3000}},
                                                 kind="skill", project_id="demo", context_packet=packet,
                                                 source_snapshot={"digest": "sha256:" + "c" * 64},
                                                 implementation_brief="Implement the scoped change.")
    resolution = service._contexts().get_artifact(control["resolution_ref"])
    assert any(item["kind"] == "contract" for item in resolution["candidates"])
    assert all(item["kind"] != "contract" for item in resolution["required"])
    task = next(item for item in resolution["required"] if item["kind"] == "task")
    task_content = service._contexts().get_artifact(task["artifact_ref"])["content"]
    assert task_content["implementation_brief"] == "Implement the scoped change."
    assert task_content["projection"]["canonical_packet"]["ref"] == control["context_packet_ref"]


def test_prompt_routing_never_treats_bookkeeping_keys_as_positive_facts():
    from adaos.services.skill_factory_worker import _selected_prompt_rule_capsules

    selected = _selected_prompt_rule_capsules(target_type="skill", context_packet={}, repair_hints={
        "profile": "small_repair", "prompt_facts": {}, "acceptance_checks": ["Keep a label unchanged"]})
    assert "adaos.skill.async_llm_job.v1" not in {item["id"] for item in selected}


def test_prompt_routing_does_not_recycle_generic_contract_prose_as_task_facts():
    from adaos.services.skill_factory_worker import _selected_prompt_rule_capsules

    packet = {"coverage": {"required": ["application_permissions", "data_policy"]},
              "facets": {"application_permissions": {"status": "present", "authoring_contract": {
                  "semantics": "approval, consent and model calls are available platform features"}},
                         "data_policy": {"status": "present", "contract": "provider storage activation"}}}
    hints = {"profile": "project_batch", "evidence_text": "Keep a label unchanged"}
    ids = {item["id"] for item in _selected_prompt_rule_capsules(
        target_type="scenario", context_packet=packet, repair_hints=hints)}
    assert not ids.intersection({"adaos.skill.human_decision.v1", "adaos.skill.async_llm_job.v1",
                                 "adaos.skill.resource_storage.v1", "adaos.skill.runtime_lifecycle.v1"})
    hints["evidence_text"] = "Implement a pending action requiring approval"
    assert "adaos.skill.human_decision.v1" in {item["id"] for item in _selected_prompt_rule_capsules(
        target_type="scenario", context_packet=packet, repair_hints=hints)}


@pytest.mark.parametrize("strategy", ["core", "accents", "adaptive"])
def test_missing_governed_authority_is_not_replaced_with_an_empty_positive_claim(strategy):
    prompt = "# task\n## Governed Change context\nNo governed context supplied; fail closed on ambiguity.\n"
    compiled, _ = compile_automation_prompt(prompt, projection={}, packet_ref="packet.json", strategy=strategy)
    assert "fail closed on ambiguity" in compiled
    assert "Exact authority" not in compiled


def test_adaptive_prefetch_preserves_bound_root_authority(tmp_path):
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker

    worker = LocalSkillFactoryWorker(state_dir=tmp_path / "state", repo_root=Path(__file__).resolve().parents[1],
                                    dev_skills_root=tmp_path / "skills", dev_scenarios_root=tmp_path / "scenarios")
    workspace = tmp_path / "workspace"
    (workspace / "skills/demo").mkdir(parents=True)
    inputs = tmp_path / "input"
    worker._build_packet({"task_id": "prefetch", "target": {"type": "skill", "id": "demo"},
                          "forge": {"sparse_paths": ["skills/demo/"]},
                          "mcp": {"root_mcp": {"url": "https://example.invalid/mcp", "required": False,
                                                "bound_target_id": "hub:only"}},
                          "realize_request": {"artifacts": {"context_strategy": "adaptive"}}},
                         workspace, inputs, descriptor_working_set={"headers": [{"item_id": "sdk:method"}]})
    prompt = (inputs / "task.md").read_text(encoding="utf-8")
    assert "hub:only" in prompt and "Only this bound_target_id is authorized" in prompt
