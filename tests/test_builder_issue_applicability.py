"""Regressions for phase leakage and the worker's post-return gate cycle."""
import copy
import json

import pytest

from adaos.services.builder.issue_applicability import project_phase_issues
from adaos.services.builder.workflow import _normalize_issue, BuilderWorkflowError
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker, CodexRunResult
from adaos.services.skill_factory_worker import context_packet_prompt_projection


def issue(identity="no-live-calls", **kwargs):
    return _normalize_issue({"issue_id": identity, "title": identity, "lane": "automation",
        "acceptance_criteria": ["No real provider calls in the Prototype"], **kwargs}, index=1)


def test_typed_prototype_restriction_survives_normalization_but_not_production():
    row = issue(kind="phase_constraint", applicable_phases=["prototype"], effect="forbid",
                effect_ref="provider.calls", verification_scope="prototype", source_ref="message:owner")
    before = copy.deepcopy(row)
    assert project_phase_issues([row], phase="prototype")["issues"] == [row]
    result = project_phase_issues([row], phase="automation")
    assert result["issues"] == []
    assert result["transitions"][0]["action"] == "discharge"
    assert row == before and row["status"] == "open"


def test_production_implementation_and_no_paid_test_calls_do_not_conflict():
    rows = [issue("implemented", effect="require", effect_ref="provider.calls", verification_scope="production"),
            issue("tests", effect="forbid", effect_ref="provider.calls", verification_scope="development")]
    assert len(project_phase_issues(rows, phase="automation")["issues"]) == 2


def test_final_model_projection_keeps_phase_provenance_and_every_bounded_criterion():
    rows = [issue(str(i), kind="product_requirement", applicable_phases=["automation"],
        effect="require", effect_ref=f"operation:{i}", verification_scope="production",
        source_message_ids=[f"message:{i}"], acceptance_criteria=[f"Criterion {j}" for j in range(20)]) for i in range(25)]
    phases = project_phase_issues(rows, phase="automation")
    projected = context_packet_prompt_projection({"change": {"issues": rows, "issue_phase_projection": phases}})
    delivered = projected["change"]["issues"]
    assert len(delivered) == 25 and len(delivered[-1]["acceptance_criteria"]) == 20
    assert delivered[-1]["verification_scope"] == "production" and delivered[-1]["source_message_ids"] == ["message:24"]
    assert len(projected["change"]["issue_phase_projection"]["transitions"]) == 25


def test_contradictory_same_scope_rejected_before_generation():
    rows = [issue("yes", effect="require", effect_ref="provider.calls"),
            issue("no", effect="forbid", effect_ref="provider.calls")]
    with pytest.raises(ValueError, match="Conflicting Issues yes and no"):
        project_phase_issues(rows, phase="automation")


@pytest.mark.parametrize("refs", [["missing"], ["old"]])
def test_transition_cannot_discard_obligation_into_missing_or_self_replacement(refs):
    row = issue("old", phase_dispositions={"automation": {"action": "replace", "reason": "Production binding", "replacement_issue_ids": refs}})
    with pytest.raises(ValueError, match="not active"):
        project_phase_issues([row], phase="automation")


def test_replacement_and_core_obligation_have_separate_authority():
    rows = [issue("old", applicable_phases=["prototype"], phase_dispositions={"automation": {
                "action": "replace", "reason": "Implement production effect", "replacement_issue_ids": ["new"]}}),
            issue("new", applicable_phases=["automation"]), issue("platform", owner="core", kind="platform_obligation")]
    result = project_phase_issues(rows, phase="automation")
    assert [x["issue_id"] for x in result["issues"]] == ["new"]
    assert [x["issue_id"] for x in result["platform_obligations"]] == ["platform"]
    assert result["transitions"][0]["action"] == "replace"


@pytest.mark.parametrize("fields", [{"applicable_phases": "prototype"}, {"kind": "guess"},
    {"phase_dispositions": {"automation": {"action": "discharge"}}}, {"effect": "maybe"}])
def test_invalid_typed_issue_never_silently_becomes_legacy(fields):
    with pytest.raises(BuilderWorkflowError, match="Issue"):
        issue(**fields)


def test_worker_model_receives_gate_owner_even_without_optional_contract_input(tmp_path):
    calls = []
    worker = LocalSkillFactoryWorker(state_dir=tmp_path/"state", repo_root=tmp_path/"repo",
        dev_skills_root=tmp_path/"skills", dev_scenarios_root=tmp_path/"scenarios",
        executor=lambda **kw: calls.append(kw) or CodexRunResult(returncode=0))
    worker._execute_codex(task_id="gate-cycle", workspace=tmp_path, prompt="Prepare source_boundary evidence",
                          output_dir=tmp_path/"attempt/output")
    prompt = calls[0]["prompt"]
    contract = json.loads(next(line for line in prompt.splitlines() if line.startswith('{"automation_gate_contract"')))["automation_gate_contract"]
    assert all(g["owner"] == "worker" and not g["application_may_certify"] for g in contract["gates"])
    assert contract["gates"][0]["prerequisite"] == "candidate_returned"
