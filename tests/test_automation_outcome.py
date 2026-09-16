import io
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.domain.automation_outcome import OUTCOME_SCHEMA, outcome_message
from adaos.domain.development_feedback import parse_development_feedback, required_user_questions
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker, SubprocessCodexExecutor


def clarification():
    return {"status": "needs_input", "report": "An ownership decision is pending.", "questions": [{
        "id": "ownership", "question": "Shared or personal setting?",
        "reason": "The scope has not been authorized.", "options": ["Shared", "Personal"],
    }]}


def test_outcome_schema_and_governed_question_adapter():
    Draft202012Validator.check_schema(OUTCOME_SCHEMA)
    value = clarification()
    message = outcome_message(json.dumps(value))
    feedback = parse_development_feedback(message)
    assert required_user_questions(feedback) == value["questions"]
    assert message.startswith(value["report"])


@pytest.mark.parametrize("status", ["completed", "blocked"])
def test_other_outcomes_cannot_carry_questions(status):
    value = clarification()
    value["status"] = status
    with pytest.raises(ValueError, match="questions require"):
        outcome_message(json.dumps(value))


@pytest.mark.parametrize("raw", [
    "Should this be shared?", "null", "{}",
    '{"status":"completed","status":"needs_input","report":"x","questions":[]}',
    '{"status":"completed","report":"","questions":[]}',
    '{"status":"needs_input","report":"pending","questions":[]}',
    '{"status":"blocked","report":"unknown issue","questions":[]}',
])
def test_invalid_completion_fails_closed(raw):
    with pytest.raises(ValueError):
        outcome_message(raw)


def test_platform_feedback_is_not_laundered_into_user_question():
    value = clarification()
    value["report"] += '\n```adaos-development-feedback\n' + json.dumps({
        "schema": "adaos.development_feedback_output.v1", "items": [{
            "category": "missing_capability", "blocking": True, "summary": "Missing API",
        }],
    }) + '\n```'
    with pytest.raises(ValueError, match="disguise"):
        outcome_message(json.dumps(value))
    value["status"], value["questions"] = "blocked", []
    assert outcome_message(json.dumps(value)) == value["report"]
    value["status"] = "completed"
    with pytest.raises(ValueError, match="contains a blocker"):
        outcome_message(json.dumps(value))


def test_completed_outcome_quarantines_only_explicitly_nonblocking_invalid_feedback():
    raw = {
        "status": "completed",
        "report": "Implemented.\n```adaos-development-feedback\n" + json.dumps({
            "schema": "adaos.development_feedback_output.v1",
            "items": [{
                "category": "missing_capability",
                "summary": "Descriptor unavailable",
                "blocking": False,
                "application_trace": {
                    "schema": "adaos.development.application_trace.v1",
                    "contract_ref": "mcp:descriptor.get",
                    "operation_id": "descriptor.get",
                    "input_summary": "bounded",
                    "expected_behavior": "Return schema",
                    "observed_behavior": "Not found",
                    "validation_result": "Fallback validation succeeded with the local ABI",
                    "trace_refs": ["trace.demo"],
                },
            }],
        }) + "\n```",
        "questions": [],
    }

    message = outcome_message(json.dumps(raw))
    feedback = parse_development_feedback(message)

    assert message.startswith("Implemented.")
    assert feedback[0]["category"] == "observability_gap"
    assert feedback[0]["blocking"] is False
    assert "validation_result" in feedback[0]["details"]


@pytest.mark.parametrize("blocking", [True, None])
def test_completed_outcome_does_not_quarantine_potentially_blocking_feedback(blocking):
    item = {"category": "missing_capability", "summary": "Descriptor unavailable",
            "application_trace": {"schema": "invalid"}}
    if blocking is not None:
        item["blocking"] = blocking
    raw = {"status": "completed", "report": (
        "Implemented.\n```adaos-development-feedback\n"
        + json.dumps({"schema": "adaos.development_feedback_output.v1", "items": [item]})
        + "\n```"
    ), "questions": []}

    with pytest.raises(ValueError):
        outcome_message(json.dumps(raw))


@pytest.mark.parametrize("raw,expected_code", [(json.dumps(clarification()), 0), ("An ordinary question?", 1)])
def test_subprocess_uses_schema_and_preserves_raw_outcome(tmp_path, monkeypatch, raw, expected_code):
    captured = []

    class Process:
        returncode = 0
        stdin = io.StringIO()

        def poll(self):
            return self.returncode

    def start(command, **kwargs):
        captured.extend(command)
        Path(command[command.index("-o") + 1]).write_text(raw, encoding="utf-8")
        return Process()

    executor = SubprocessCodexExecutor()
    monkeypatch.setattr(executor, "_resolve_executable", lambda: "codex")
    monkeypatch.setattr(executor, "_materialize_sdk_snapshot", lambda root: None)
    monkeypatch.setattr(executor, "_execution_environment", lambda **kwargs: {})
    monkeypatch.setattr("adaos.services.skill_factory_worker.subprocess.Popen", start)
    output = tmp_path / "output"
    result = executor(workspace=tmp_path, prompt="bounded task", output_dir=output)
    assert result.returncode == expected_code
    assert json.loads(Path(captured[captured.index("--output-schema") + 1]).read_text()) == OUTCOME_SCHEMA
    assert (output / "last_message.md").read_text(encoding="utf-8") == raw
    if expected_code == 0:
        assert required_user_questions(parse_development_feedback(result.final_message))
    else:
        assert "Invalid Automation outcome" in result.stderr


@pytest.mark.parametrize("raw", [json.dumps(clarification()), "An ordinary question?"])
def test_recovery_cannot_bypass_unresolved_outcome_even_with_old_passed_checks(tmp_path, raw):
    run = tmp_path / "runs/task.blocked"
    for name in ("input", "output", "runtime", "workspace/.git"):
        (run / name).mkdir(parents=True)
    for name, payload in {
        "input/assignment.json": {"task_id": "task.blocked"},
        "input/automation-outcome.schema.json": OUTCOME_SCHEMA,
        "runtime/state.json": {"status": "failed"},
        "output/test_report.json": {"status": "passed", "ok": True},
    }.items():
        (run / name).write_text(json.dumps(payload), encoding="utf-8")
    (run / "output/last_message.md").write_text(raw, encoding="utf-8")
    worker = LocalSkillFactoryWorker(state_dir=tmp_path / "state", repo_root=tmp_path,
        dev_skills_root=tmp_path / "skills", dev_scenarios_root=tmp_path / "scenarios",
        runs_root=tmp_path / "runs")
    with pytest.raises(ValueError):
        worker.recover_validated_run("task.blocked")
def test_final_schema_does_not_instruct_the_model_to_abandon_tool_work():
    from adaos.domain.automation_outcome import OUTCOME_INSTRUCTION, OUTCOME_SCHEMA
    assert "only the last assistant message" in OUTCOME_INSTRUCTION
    assert "Use the available tools normally" in OUTCOME_INSTRUCTION
    assert "Attempt the relevant admitted tool" in OUTCOME_INSTRUCTION
    assert "does not disable tools" in OUTCOME_SCHEMA["description"]
