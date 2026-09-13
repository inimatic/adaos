import importlib.util
import json
from pathlib import Path

import pytest
import yaml


spec = importlib.util.spec_from_file_location("frozen_automation", Path(__file__).parents[1] / "e2e/stand/run-frozen-automation.py")
stand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stand)


def plan(*types):
    return {"schema": "adaos.e2e.frozen_automation.v1", "steps": [
        {"id": str(index), "type": kind} for index, kind in enumerate(types)
    ]}


@pytest.mark.parametrize("forbidden", ["builder.chat", "trial.prepare", "trial.decide", "release.promote"])
def test_frozen_automation_cannot_regenerate_or_deliver(forbidden):
    with pytest.raises(ValueError, match="cannot generate"):
        stand.validate_plan(plan("automation.start", forbidden))


def test_correction_does_not_accept_functional_ui_as_prototype():
    with pytest.raises(ValueError, match="reapprove"):
        stand.validate_plan(plan("prototype.accept", "automation.submit", "automation.wait"))
    stand.validate_plan(plan("automation.submit", "automation.wait"))


def test_acceptance_order_and_single_submission():
    with pytest.raises(ValueError, match="precede"):
        stand.validate_plan(plan("automation.start", "prototype.accept"))
    with pytest.raises(ValueError, match="Exactly one"):
        stand.validate_plan(plan("automation.start", "automation.submit"))


@pytest.mark.parametrize("step", ["automation.wait", "automation.recover"])
def test_observation_resume_cannot_follow_an_unpinned_task(step):
    value = plan(step)
    with pytest.raises(ValueError, match="exact session and task"):
        stand.validate_plan(value)
    value["steps"][0]["input"] = {"session_id": "automation.scenario.test", "expected_task_id": "task.one"}
    stand.validate_plan(value)


def test_retained_equipment_plan_is_automation_only():
    path = Path(__file__).parents[1] / "e2e/builder/development/lifecycle/equipment-inspections-automation.yaml"
    stand.validate_plan(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_automation_review_requires_exact_existing_task(tmp_path):
    spec = importlib.util.spec_from_file_location("review_snapshot", Path(__file__).parents[1] / "e2e/stand/review-builder-snapshot.py")
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    metadata = {"object_type": "scenario", "object_id": "test_demo", "task_id": "task.current"}
    (tmp_path / "snapshot.json").write_text(json.dumps(metadata), encoding="utf-8")
    ui = {"ui": {"application": {"desktop": {}}}}
    (tmp_path / "webui.json").write_text(json.dumps(ui), encoding="utf-8")
    assert review.read_automation_snapshot(tmp_path, "test_demo", "task.current") == (metadata, ui)
    for target, revision in (("test_other", "task.current"), ("test_demo", "task.previous"), ("test_demo", "002")):
        with pytest.raises(ValueError, match="exact retained"):
            review.read_automation_snapshot(tmp_path, target, revision)
    (tmp_path / "webui.json").unlink()
    with pytest.raises(FileNotFoundError):
        review.read_automation_snapshot(tmp_path, "test_demo", "task.current")


def test_frozen_fixture_remaps_only_identifiers_and_confines_paths(tmp_path):
    spec = importlib.util.spec_from_file_location("fork_snapshot", Path(__file__).parents[1] / "e2e/stand/fork-frozen-prototype.py")
    fork = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fork)
    original = {"scenario:test_original": ["prototype.test_original.records", {"title": "Neutral title", "count": 4}]}
    mapped = fork.remap(original, "test_original", "test_repeat")
    assert mapped == {"scenario:test_repeat": ["prototype.test_repeat.records", {"title": "Neutral title", "count": 4}]}
    assert fork.remap(mapped, "test_repeat", "test_original") == original
    assert fork.confined(tmp_path, "assets/en.json") == tmp_path / "assets/en.json"
    for relative in (".", "../escape.json", str(tmp_path.parent / "outside.json")):
        with pytest.raises(ValueError, match="escapes"):
            fork.confined(tmp_path, relative)


def test_generation_context_keeps_prior_failure_but_not_orchestrator_metrics():
    from adaos.services.skill_factory_worker import context_packet_prompt_projection
    metrics = {"report_id": "report:1", "evidence_digest": "sha256:abc", "definition_complexity": {"state_count": 60}}
    original = {"previous_run": {"run_id": "run:1", "status": "failed", "error": "Missing handler",
        "output_refs": ["evidence:failure"], "workflow_metrics": metrics}}
    value = context_packet_prompt_projection(original)
    assert value["previous_run"] == {"run_id": "run:1", "status": "failed", "error": "Missing handler",
        "output_refs": ["evidence:failure"], "workflow_metrics_ref": {"report_id": "report:1", "evidence_digest": "sha256:abc"}}
    assert original["previous_run"]["workflow_metrics"] == metrics


def test_review_accepts_test_composition_only_when_it_owns_the_scenario():
    spec = importlib.util.spec_from_file_location("review_snapshot", Path(__file__).parents[1] / "e2e/stand/review-builder-snapshot.py")
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    project = {"catalog": {"title": "Repeat [TEST]", "tags": ["test"]},
        "components": {"owned": [{"ref": "scenario:test_repeat"}]}}
    review.require_test_ownership("test_repeat", {}, project)
    for target in ("real_app", "test_other"):
        with pytest.raises(ValueError, match="owned test"):
            review.require_test_ownership(target, {}, project)


def test_automation_caller_contract_distinguishes_dev_owner_from_delegation():
    from adaos.sdk import access
    capsule = json.loads((Path(__file__).parents[1] / "src/adaos/abi/implementation.bindings.v1.json").read_text(encoding="utf-8"))
    contract = capsule["binding_rules"]["authorization"]
    assert "Personal DEV preview" in contract
    assert "deliberately rejected in personal DEV" in contract
    assert "does not configure this transport" in contract
    assert "node-owner credential" in access.caller.__doc__
    assert "do not qualify delegated" in access.caller.__doc__


def test_automation_read_policy_example_conforms_to_skill_abi():
    import jsonschema

    abi = Path(__file__).parents[1] / "src/adaos/abi"
    capsule = json.loads((abi / "implementation.bindings.v1.json").read_text(encoding="utf-8"))
    schema = json.loads((abi / "skill.schema.json").read_text(encoding="utf-8"))
    route = capsule["examples"]["tool_data_route"]
    fragment = {"$ref": "#/$defs/dataRoute", "$defs": schema["$defs"]}
    jsonschema.Draft202012Validator(fragment).validate(route)
    assert route["read_policy"]["invalidation_tags"] == capsule["examples"]["record_editor"]["dataSource"]["invalidationTags"]
    broken = {**route, "read_policy": "targeted_invalidation"}
    assert list(jsonschema.Draft202012Validator(fragment).iter_errors(broken))


def test_worker_retains_model_response_and_validation_before_repair(tmp_path):
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker

    events = tmp_path / "codex-live.jsonl"
    events.write_text('{"response":"Первый ответ"}\n', encoding="utf-8")
    (tmp_path / "test_report.json").write_text('{"ok":false}', encoding="utf-8")
    LocalSkillFactoryWorker._archive_model_output("task.test", tmp_path, 1)
    LocalSkillFactoryWorker._archive_model_output("task.test", tmp_path, 1)
    archive = tmp_path / "model-attempts/001"
    assert (archive / "codex-live.jsonl").read_bytes() == events.read_bytes()
    receipt = json.loads((archive / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["attempt"] == 1 and receipt["task_id"] == "task.test"
    assert set(receipt["artifacts"]) == {"codex-live.jsonl", "test_report.json"}
    events.write_text('{"response":"Следующий ответ"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite retained"):
        LocalSkillFactoryWorker._archive_model_output("task.test", tmp_path, 1)
    assert "Первый ответ" in (archive / "codex-live.jsonl").read_text(encoding="utf-8")
