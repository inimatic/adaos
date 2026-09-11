from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
from typing import Any, Mapping

import pytest
import yaml
from typer.testing import CliRunner

from adaos.apps.cli.commands import builder as builder_cli
from adaos.e2e.builder import (
    BuilderE2EError,
    BuilderE2ERunner,
    BuilderE2EUnavailable,
    SdkBuilderExecutor,
    _evaluation_application_context,
    compare_builder_e2e_baseline,
    create_builder_e2e_baseline,
    load_builder_e2e_suite,
    _client_capability_environment,
)
from adaos.services.builder.llm_input_attribution import (
    build_llm_input_attribution,
)


def test_artifact_writers_keep_unicode_readable_in_plain_and_compressed_json(tmp_path) -> None:
    from adaos.e2e.builder import _compact_step_output, _write_json
    from adaos.e2e.stand import _json_write

    message = "Конструктор обновил прототип"
    for writer in (_write_json, _json_write):
        path = tmp_path / "readable.json"
        writer(path, {"message": message})
        assert message in path.read_text(encoding="utf-8")
        assert "\\u041a" not in path.read_text(encoding="utf-8")
    value = {"message": message, "payload": message * 4000}
    summary, relative = _compact_step_output(
        value, bundle_dir=tmp_path, case_id="unicode", repetition=1, step_id="design"
    )
    assert relative
    raw = gzip.decompress((tmp_path / relative).read_bytes()).decode("utf-8")
    assert message in raw
    assert json.loads(raw) == value
    assert summary["content_bytes"] == len(raw.encode("utf-8"))


class FixtureExecutor:
    adapter_id = "fixture.v1"

    def __init__(
        self,
        outputs: Mapping[str, Mapping[str, Any]],
        *,
        cleanup_result: Mapping[str, Any] | None = None,
    ) -> None:
        self.outputs = outputs
        self.cleanup_result = dict(cleanup_result or {"status": "passed"})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cleanup_calls: list[dict[str, Any]] = []

    def execute(
        self, step_type: str, inputs: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.calls.append((step_type, dict(inputs)))
        return copy.deepcopy(
            dict(self.outputs[str(inputs.get("fixture") or step_type)])
        )

    def cleanup(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        self.cleanup_calls.append(dict(context))
        return copy.deepcopy(self.cleanup_result)


def _write_suite(
    root: Path,
    *,
    cases: list[dict[str, Any]],
    repetitions: int = 1,
) -> Path:
    case_refs: list[str] = []
    for case in cases:
        case_ref = f"cases/{case['case_id']}.yaml"
        case_path = root / case_ref
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
        case_refs.append(case_ref)
    suite = {
        "schema": "adaos.builder.e2e_suite.v1",
        "suite_id": "fixture-suite",
        "version": "1",
        "cohort": "development",
        "case_refs": case_refs,
        "defaults": {
            "adapter": "fixture.v1",
            "profile": "generic",
            "generation_contract": "webui.v1",
            "repetitions": repetitions,
            "browser": "off",
        },
    }
    path = root / "suite.yaml"
    path.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    return path


def _case(case_id: str = "case-en") -> dict[str, Any]:
    return {
        "schema": "adaos.builder.e2e_case.v1",
        "case_id": case_id,
        "title": "Fixture case",
        "locale": "en",
        "tags": ["smoke"],
        "steps": [
            {
                "id": "first",
                "type": "builder.chat",
                "input": {"fixture": "first", "text": "Create a small dashboard"},
                "expect": {
                    "values": {"ok": True},
                    "paths_present": ["result.id"],
                },
            },
            {
                "id": "second",
                "type": "scenario.validate",
                "input": {"fixture": "second", "scenario_id": "$steps.first.result.id"},
                "expect": {"values": {"ok": True}},
            },
        ],
        "cleanup": {"policy": "always"},
    }


def test_runner_writes_schema_valid_bundle_and_resolves_step_input(
    tmp_path: Path,
) -> None:
    case = _case()
    case["steps"][0]["input"]["text"] = (
        "Create app ${case_instance_id} for ${run_id}"
    )
    suite = _write_suite(tmp_path / "definitions", cases=[case], repetitions=2)
    executor = FixtureExecutor(
        {
            "first": {
                "ok": True,
                "result": {"id": "scenario-created"},
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 60},
                    "output_tokens": 20,
                },
            },
            "second": {"ok": True, "issues": []},
        }
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="fixture-run",
        executor=executor,
    ).run()

    assert report["status"] == "passed"
    assert report["summary"] == {
        "passed": 2,
        "failed": 0,
        "inconclusive": 0,
        "total": 2,
    }
    assert report["metrics"]["fresh_input_tokens"] == 80
    assert report["metrics"]["cached_input_tokens"] == 120
    assert report["metrics"]["output_tokens"] == 40
    assert executor.calls[1][1]["scenario_id"] == "scenario-created"
    assert executor.calls[0][1]["text"].startswith("Create app e2e")
    assert executor.calls[0][1]["text"].endswith("for fixture-run")
    assert executor.calls[0][1]["text"] != executor.calls[2][1]["text"]
    assert len(executor.cleanup_calls) == 2
    bundle = Path(report["bundle_dir"])
    assert (
        json.loads((bundle / "report.json").read_text(encoding="utf-8"))["status"]
        == "passed"
    )
    assert len(list((bundle / "cases" / "case-en").glob("*.json"))) == 2


def test_runner_reports_case_and_step_progress_without_changing_results(
    tmp_path: Path,
) -> None:
    suite = _write_suite(tmp_path / "definitions", cases=[_case()])
    events: list[dict[str, Any]] = []
    executor = FixtureExecutor(
        {
            "first": {"ok": True, "result": {"id": "scenario-created"}},
            "second": {"ok": True},
        }
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="progress-run",
        executor=executor,
        progress=lambda event: events.append(dict(event)),
    ).run()

    assert report["status"] == "passed"
    assert [event["event"] for event in events] == [
        "run_started",
        "case_started",
        "step_started",
        "step_finished",
        "step_started",
        "step_finished",
        "case_finished",
        "run_finished",
    ]
    assert events[3]["status"] == "passed"
    assert events[3]["duration_ms"] >= 0


def test_runner_counts_generation_usage_breakdown_once(tmp_path: Path) -> None:
    case = _case()
    case["steps"] = case["steps"][:1]
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    telemetry = {
        "usage": {
            "input_tokens": 220,
            "cached_input_tokens": 80,
            "output_tokens": 30,
        },
        "usage_breakdown": {
            "primary": {
                "input_tokens": 120,
                "cached_input_tokens": 0,
                "output_tokens": 20,
            },
            "repair": {
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 10,
            },
        },
        "repair": {
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 80,
                "output_tokens": 10,
            }
        },
    }
    executor = FixtureExecutor(
        {
            "first": {
                "ok": True,
                "telemetry": telemetry,
                "generation_diagnostic": {"telemetry": telemetry},
            }
        }
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="usage-breakdown",
        executor=executor,
    ).run()

    assert report["metrics"]["model_calls"] == 2
    assert report["metrics"]["fresh_input_tokens"] == 140
    assert report["metrics"]["cached_input_tokens"] == 80
    assert report["metrics"]["output_tokens"] == 30


def test_required_failure_stops_case_but_optional_failure_does_not(
    tmp_path: Path,
) -> None:
    case = _case()
    case["steps"][0]["required"] = False
    case["steps"][0]["expect"] = {"values": {"ok": True}}
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = FixtureExecutor(
        {
            "first": {"ok": False, "result": {"id": "scenario-created"}},
            "second": {"ok": True},
        }
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="optional-run",
        executor=executor,
    ).run()

    assert report["status"] == "passed"
    result = json.loads(
        (
            Path(report["bundle_dir"]) / "cases" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )
    assert [step["status"] for step in result["steps"]] == ["failed", "passed"]


def test_step_exception_retains_bounded_traceback(tmp_path: Path) -> None:
    case = _case()
    case["steps"] = case["steps"][:1]
    suite = _write_suite(tmp_path / "definitions", cases=[case])

    class FailingExecutor(FixtureExecutor):
        def execute(
            self,
            step_type: str,
            inputs: Mapping[str, Any],
            context: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            raise RuntimeError("diagnostic failure")

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="exception-evidence-run",
        executor=FailingExecutor({}),
    ).run()

    assert report["status"] == "failed"
    result = json.loads(
        (
            Path(report["bundle_dir"]) / "cases" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )
    diagnostic = result["steps"][0]["output"]
    assert diagnostic["error"] == "RuntimeError"
    assert "diagnostic failure" in diagnostic["traceback"]
    assert len(diagnostic["traceback"]) <= 12_000


def test_cleanup_failure_makes_successful_case_inconclusive(tmp_path: Path) -> None:
    case = _case()
    case["steps"] = case["steps"][:1]
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = FixtureExecutor(
        {"first": {"ok": True, "result": {"id": "scenario-created"}}},
        cleanup_result={"status": "failed", "results": [{"ok": False}]},
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="cleanup-failed-run",
        executor=executor,
    ).run()

    assert report["status"] == "inconclusive"
    result = json.loads(
        (
            Path(report["bundle_dir"]) / "cases" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )
    assert result["failure"]["step_id"] == "cleanup"


def test_large_step_output_is_compressed_without_breaking_refs_or_usage(
    tmp_path: Path,
) -> None:
    case = _case()
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = FixtureExecutor(
        {
            "first": {
                "ok": True,
                "result": {"id": "scenario-created"},
                "payload": "x" * 20_000,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
            "second": {"ok": True},
        }
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="compact-output-run",
        executor=executor,
    ).run()

    result = json.loads(
        (
            Path(report["bundle_dir"]) / "cases" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )
    compact = result["steps"][0]["output"]
    assert compact["content_bytes"] > 20_000
    assert compact["compressed_bytes"] < compact["content_bytes"]
    assert result["metrics"]["fresh_input_tokens"] == 100
    assert executor.calls[1][1]["scenario_id"] == "scenario-created"
    evidence_path = Path(report["bundle_dir"]) / compact["evidence_ref"]
    retained = json.loads(gzip.decompress(evidence_path.read_bytes()))
    assert retained["payload"] == "x" * 20_000


def test_suite_loader_rejects_case_path_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text(yaml.safe_dump(_case()), encoding="utf-8")
    suite = {
        "schema": "adaos.builder.e2e_suite.v1",
        "suite_id": "unsafe",
        "version": "1",
        "cohort": "development",
        "case_refs": ["../outside.yaml"],
    }
    suite_dir = tmp_path / "definitions"
    suite_dir.mkdir()
    path = suite_dir / "suite.yaml"
    path.write_text(yaml.safe_dump(suite), encoding="utf-8")

    with pytest.raises(BuilderE2EError, match="escapes suite directory"):
        load_builder_e2e_suite(path)


def test_suite_loader_uses_yaml_12_boolean_rules(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "definitions", cases=[_case()])
    text = suite.read_text(encoding="utf-8").replace("browser: 'off'", "browser: off")
    suite.write_text(text, encoding="utf-8")

    loaded = load_builder_e2e_suite(suite)

    assert loaded.suite["defaults"]["browser"] == "off"


def test_suite_loader_enforces_declared_outcome_grade_gate(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "definitions", cases=[_case()])
    payload = yaml.safe_load(suite.read_text(encoding="utf-8"))
    payload["gates"] = {"outcome_grade_required": True}
    suite.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(BuilderE2EError, match="requires prototype.grade"):
        load_builder_e2e_suite(suite)


def test_baseline_remains_comparable_across_implementation_commits(
    tmp_path: Path,
) -> None:
    suite = _write_suite(tmp_path / "definitions", cases=[_case()])
    executor = FixtureExecutor(
        {"first": {"ok": True, "result": {"id": "scenario"}}, "second": {"ok": True}}
    )
    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="baseline-source",
        executor=executor,
    ).run()
    bundle = Path(report["bundle_dir"])
    run_manifest = json.loads((bundle / "run.json").read_text(encoding="utf-8"))
    baseline = create_builder_e2e_baseline(
        baseline_id="baseline-1",
        accepted_by="test",
        run_manifest=run_manifest,
        report=report,
        source_report_ref="runs/baseline-source/report.json",
    )

    candidate = copy.deepcopy(run_manifest)
    candidate["adapter"] = "sdk.v1"
    candidate["environment"]["repository_commit"] = "new-implementation"
    candidate["environment"]["client_commit"] = "new-client"
    comparison = compare_builder_e2e_baseline(
        run_manifest=candidate,
        metrics={
            **report["metrics"],
            "duration_ms_p50": report["metrics"]["duration_ms_p50"] + 1,
        },
        baseline=baseline,
    )

    assert comparison["status"] == "regressed"
    assert comparison["reasons"] == []
    assert baseline["reference"]["adapter"] == "fixture.v1"
    assert baseline["cohort"]["grader_model"] == "gpt-4.1"
    assert baseline["cohort"]["grader_version"] == "10"


def test_runner_rejects_undeclared_executor_adapter(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "definitions", cases=[_case()])
    executor = FixtureExecutor({})
    executor.adapter_id = "other.v1"

    with pytest.raises(BuilderE2EError, match="does not match"):
        BuilderE2ERunner(
            suite,
            output_root=tmp_path / "runs",
            repo_root=tmp_path,
            executor=executor,
        )


def test_builder_e2e_cli_is_registered() -> None:
    result = CliRunner().invoke(builder_cli.app, ["e2e", "--help"])

    assert result.exit_code == 0
    assert "--baseline" in result.stdout
    assert "--repetitions" in result.stdout
    assert "--resume" in result.stdout
    assert "--run-id" in result.stdout


def test_runner_constructs_public_sdk_adapter(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "definitions", cases=[_case()])
    payload = yaml.safe_load(suite.read_text(encoding="utf-8"))
    payload["defaults"]["adapter"] = "sdk.v1"
    suite.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    runner = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
    )

    assert isinstance(runner.executor, SdkBuilderExecutor)


@pytest.mark.parametrize("generation_contract", ["semantic.v1", "semantic.v2"])
def test_sdk_adapter_routes_declared_generation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generation_contract: str,
) -> None:
    from adaos.sdk.builder import prototype

    captured: dict[str, Any] = {}

    def fake_submit_request(statement: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "statement": statement}

    monkeypatch.setattr(prototype, "submit_request", fake_submit_request)
    executor = SdkBuilderExecutor(repo_root=tmp_path, browser_mode="off")

    result = executor.execute(
        "builder.chat",
        {"text": "Create a queue"},
        {
            "run_id": "semantic-run",
            "case_id": "queue",
            "repetition": 1,
            "locale": "en",
            "generation_contract": generation_contract,
        },
    )

    assert result["ok"] is True
    assert captured["metadata"]["builder_e2e_generation_contract"] == (
        generation_contract
    )
    assert captured["metadata"]["builder_semantic_compiler"] is True


def test_case_repetitions_use_distinct_webspaces(tmp_path: Path) -> None:
    case = _case()
    case["steps"] = case["steps"][:1]
    suite = _write_suite(tmp_path / "definitions", cases=[case], repetitions=2)
    executor = FixtureExecutor(
        {"first": {"ok": True, "result": {"id": "scenario-created"}}}
    )

    BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="isolated-run",
        executor=executor,
    ).run()

    webspaces = [str(inputs["webspace_id"]) for _, inputs in executor.calls]
    assert len(set(webspaces)) == 2
    assert all("case-en" in item for item in webspaces)


def test_runner_injects_case_oracle_only_into_prototype_grade(tmp_path: Path) -> None:
    case = _case()
    case["requirements"] = {
        "primary_jobs": ["review entry"],
        "representative_states": ["empty"],
    }
    case["prohibited_assumptions"] = ["all entries are public"]
    case["steps"].append(
        {
            "id": "grade",
            "type": "prototype.grade",
            "input": {
                "fixture": "grade",
                "scenario_id": "$steps.first.result.id",
            },
            "expect": {"values": {"ok": True, "passed": True}},
        }
    )
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = FixtureExecutor(
        {
            "first": {"ok": True, "result": {"id": "scenario-created"}},
            "second": {"ok": True},
            "grade": {"ok": True, "passed": True},
        }
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="oracle-injection",
        executor=executor,
    ).run()

    assert report["status"] == "passed"
    assert "requirements" not in executor.calls[0][1]
    grade_input = executor.calls[2][1]
    assert grade_input["requirements"] == case["requirements"]
    assert grade_input["prohibited_assumptions"] == case["prohibited_assumptions"]
    assert grade_input["user_turns"] == ["Create a small dashboard"]
    assert grade_input["model"] == "gpt-4.1"
    run_manifest = json.loads(
        (Path(report["bundle_dir"]) / "run.json").read_text(encoding="utf-8")
    )
    assert run_manifest["evaluation"]["prototype_grader"] == {
        "kind": "model",
        "model": "gpt-4.1",
            "version": "10",
    }


def test_evaluation_application_context_is_project_scoped() -> None:
    context = {
        "outputs": {
            "unrelated": {
                "application_operation": {
                    "application": {
                        "application_id": "other",
                        "publisher_ref": "subnet:sn_other",
                        "visibility": "public",
                        "revision": 2,
                    }
                }
            },
            "create": {
                "application_operation": {
                    "application": {
                        "application_id": "target",
                        "publisher_ref": "subnet:sn_owner",
                        "visibility": "private",
                        "revision": 1,
                    }
                }
            },
        }
    }

    assert _evaluation_application_context(
        context, project_ref="project:target"
    ) == {
        "application_id": "target",
        "publisher_ref": "subnet:sn_owner",
        "visibility": "private",
        "revision": 1,
    }
    assert not _evaluation_application_context(
        context, project_ref="project:missing"
    )


def test_declared_retry_is_counted_and_first_attempt_is_retained(
    tmp_path: Path,
) -> None:
    class RetryExecutor(FixtureExecutor):
        def execute(self, step_type, inputs, context):
            if not self.calls:
                self.calls.append((step_type, dict(inputs)))
                raise BuilderE2EUnavailable("temporary test dependency")
            return super().execute(step_type, inputs, context)

    case = _case()
    case["steps"] = case["steps"][:1]
    case["steps"][0]["retry"] = {
        "max_attempts": 2,
        "on": ["unavailable"],
    }
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = RetryExecutor(
        {"first": {"ok": True, "result": {"id": "scenario-created"}}}
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="retry-run",
        executor=executor,
    ).run()
    result = json.loads(
        (
            Path(report["bundle_dir"]) / "cases" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )

    assert result["status"] == "passed"
    assert result["metrics"]["step_attempts"] == 2
    assert result["metrics"]["step_retries"] == 1
    assert [item["status"] for item in result["steps"][0]["attempts"]] == [
        "inconclusive",
        "passed",
    ]


def test_interrupted_case_resumes_from_validated_checkpoint(tmp_path: Path) -> None:
    class InterruptExecutor(FixtureExecutor):
        def execute(self, step_type, inputs, context):
            raise KeyboardInterrupt("simulated interruption")

    case = _case()
    case["steps"] = case["steps"][:1]
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    output_root = tmp_path / "runs"
    interrupted = BuilderE2ERunner(
        suite,
        output_root=output_root,
        repo_root=tmp_path,
        run_id="resume-run",
        executor=InterruptExecutor({}),
    )
    with pytest.raises(KeyboardInterrupt):
        interrupted.run()

    checkpoint = json.loads(
        (
            output_root / "resume-run" / "checkpoints" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["active_step"] == {"attempt": 1, "id": "first", "index": 0}

    executor = FixtureExecutor(
        {"first": {"ok": True, "result": {"id": "scenario-created"}}}
    )
    report = BuilderE2ERunner(
        suite,
        output_root=output_root,
        repo_root=tmp_path,
        run_id="resume-run",
        resume=True,
        executor=executor,
    ).run()
    result = json.loads(
        (
            Path(report["bundle_dir"]) / "cases" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )

    assert result["status"] == "passed"
    assert result["metrics"]["resumed"] is True
    assert result["metrics"]["step_retries"] == 1
    assert [item["status"] for item in result["steps"][0]["attempts"]] == [
        "interrupted",
        "passed",
    ]


def test_input_attribution_violation_invalidates_otherwise_passing_case(
    tmp_path: Path,
) -> None:
    class AttributionExecutor(FixtureExecutor):
        def collect_input_attribution(self, context):
            return {
                "status": "failed",
                "violations": [{"code": "domain_pack_mismatch"}],
            }

    case = _case()
    case["steps"] = case["steps"][:1]
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="attribution-run",
        executor=AttributionExecutor(
            {"first": {"ok": True, "result": {"id": "scenario-created"}}}
        ),
    ).run()

    assert report["status"] == "inconclusive"
    result = json.loads(
        (
            Path(report["bundle_dir"]) / "cases" / "case-en" / "attempt-01.json"
        ).read_text(encoding="utf-8")
    )
    assert result["failure"]["step_id"] == "input_attribution"


def test_attribution_call_count_and_aggregate_usage_are_not_double_counted(
    tmp_path: Path,
) -> None:
    class AttributionUsageExecutor(FixtureExecutor):
        def collect_input_attribution(self, context):
            return {
                "status": "passed",
                "unique_receipt_count": 3,
                "receipts": [{"request_id": str(index)} for index in range(3)],
                "violations": [],
            }

    case = _case()
    case["steps"] = case["steps"][:1]
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = AttributionUsageExecutor(
        {
            "first": {
                "ok": True,
                "result": {"id": "scenario-created"},
                "telemetry": {
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 20,
                    }
                },
            }
        }
    )

    report = BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="attribution-usage",
        executor=executor,
    ).run()

    assert report["metrics"]["model_calls"] == 3
    assert report["metrics"]["fresh_input_tokens"] == 60
    assert report["metrics"]["cached_input_tokens"] == 40
    assert report["metrics"]["output_tokens"] == 20


def test_compatibility_executor_validates_actual_generic_request_journal(
    tmp_path: Path,
) -> None:
    from adaos.e2e.builder import CompatibilityBuilderExecutor

    artifact_root = tmp_path / "scenario"
    journal_dir = artifact_root / "llm_jobs"
    journal_dir.mkdir(parents=True)
    messages = [
        {"role": "system", "content": "Policy"},
        {"role": "user", "content": "Contracts"},
        {"role": "user", "content": "Request"},
    ]
    attribution = build_llm_input_attribution(
        request_id="request-actual",
        route="prototype.transform.async",
        stage="generate",
        attempt=1,
        messages=messages,
        message_purposes=("system_policy", "stable_context", "user_delta"),
        capability_selection={
            "items": [],
            "input_attribution": {"profile": "generic", "domain_packs": []},
        },
        generation_options={"output_mode": "json_patch_batch_v1"},
        created_at="2026-09-10T10:00:00+00:00",
    )
    (journal_dir / "request-actual.request.json").write_text(
        json.dumps(
            {
                "schema": "adaos.builder.llm_job_input.v1",
                "message_sha256": "message-digest",
                "input_attribution": attribution,
            }
        ),
        encoding="utf-8",
    )

    result = CompatibilityBuilderExecutor(repo_root=tmp_path).collect_input_attribution(
        {
            "profile": "generic",
            "generation_contract": "webui.v1",
            "domain_packs": [],
            "outputs": {"create": {"artifact_root": str(artifact_root)}},
        }
    )

    assert result["status"] == "passed"
    assert result["journal_count"] == 1
    assert result["unique_receipt_count"] == 1
    assert result["observed_model_calls"] == 1
    assert result["receipts"][0]["request_id"] == "request-actual"

    mismatch = CompatibilityBuilderExecutor(
        repo_root=tmp_path
    ).collect_input_attribution(
        {
            "profile": "generic",
            "generation_contract": "semantic.v1",
            "domain_packs": [],
            "outputs": {"create": {"artifact_root": str(artifact_root)}},
        }
    )
    assert mismatch["status"] == "failed"
    assert mismatch["violations"][0]["code"] == "generation_contract_mismatch"


def test_compatibility_executor_waits_for_exact_llm_job(
    monkeypatch, tmp_path: Path
) -> None:
    from adaos.e2e.builder import CompatibilityBuilderExecutor

    class Manager:
        def __init__(self) -> None:
            self.calls = 0

        def run_dev_tool(self, _skill, tool, payload, *, timeout):
            assert tool == "get_session"
            assert payload == {"session_id": "session-1", "webspace_id": "e2e-space"}
            assert timeout == 5.0
            self.calls += 1
            status = "running" if self.calls == 1 else "succeeded"
            return {
                "session": {
                    "id": "session-1",
                    "pending_llm_jobs": {
                        "local-job": {
                            "job_id": "root-job",
                            "local_job_id": "local-job",
                            "status": status,
                        }
                    },
                }
            }

    executor = CompatibilityBuilderExecutor(repo_root=tmp_path)
    manager = Manager()
    executor._skill_manager = manager
    monkeypatch.setattr("adaos.e2e.builder.time.sleep", lambda _seconds: None)

    result = executor.execute(
        "builder.wait",
        {
            "session_id": "session-1",
            "job_id": "root-job",
            "webspace_id": "e2e-space",
            "timeout_seconds": 5,
        },
        {"run_id": "run", "timeout_seconds": 5},
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["poll_count"] == 2
    assert manager.calls == 2


def test_compatibility_executor_waits_for_durable_terminal_artifact(
    tmp_path: Path,
) -> None:
    from adaos.e2e.builder import CompatibilityBuilderExecutor

    artifact_root = tmp_path / "scenario"
    journal_dir = artifact_root / "llm_jobs"
    journal_dir.mkdir(parents=True)
    (journal_dir / "root-job.request.json").write_text(
        json.dumps({"messages": [{"role": "user", "content": "full input"}]}),
        encoding="utf-8",
    )
    (journal_dir / "root-job.semantic-repair.request.json").write_text(
        json.dumps(
            {"messages": [{"role": "user", "content": "full repair input"}]}
        ),
        encoding="utf-8",
    )
    (artifact_root / "candidate.primary.json").write_text(
        json.dumps({"response": "full output", "candidate": {"views": []}}),
        encoding="utf-8",
    )
    (journal_dir / "root-job.json").write_text(
        json.dumps(
            {
                "schema": "adaos.builder.llm_job_result.v1",
                "job_id": "root-job",
                "related_ids": ["local-job", "root-job"],
                "status": "succeeded",
                "input_artifact": {"path": "llm_jobs/root-job.request.json"},
                "diagnostic": {
                    "repair_attempted": True,
                    "result": {
                        "attempts": [
                            {
                                "attempt": 1,
                                "ok": False,
                                "request_id": "request-1",
                                "validation": {
                                    "error": "component_contract_invalid",
                                    "request_evaluation": {
                                        "qualification": {
                                            "prototype_brief": {"problem": "secret"}
                                        },
                                        "postconditions": [
                                            {
                                                "id": "ui.information_capture",
                                                "ok": False,
                                                "expected": ["attachment"],
                                                "actual": [],
                                            }
                                        ],
                                    },
                                },
                            }
                        ],
                        "candidate_artifacts": [
                            {
                                "kind": "raw_model_output",
                                "stage": "primary",
                                "path": "candidate.primary.json",
                                "sha256": "candidate-artifact-sha",
                                "response_sha256": "response-sha",
                                "candidate_sha256": "semantic-sha",
                                "structured": True,
                            }
                        ],
                        "repair": {
                            "input_artifact": {
                                "path": (
                                    "llm_jobs/root-job.semantic-repair.request.json"
                                )
                            }
                        },
                    },
                    "telemetry": {
                        "usage": {
                            "input_tokens": 120,
                            "cached_input_tokens": 80,
                            "output_tokens": 15,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    executor = CompatibilityBuilderExecutor(repo_root=tmp_path)

    result = executor.execute(
        "builder.wait",
        {
            "session_id": "session-1",
            "job_id": "root-job",
            "webspace_id": "e2e-space",
            "artifact_root": str(artifact_root),
        },
        {
            "run_id": "run",
            "case_id": "equipment",
            "repetition": 1,
            "bundle_dir": str(tmp_path / "bundle"),
            "timeout_seconds": 5,
        },
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["telemetry"]["usage"]["cached_input_tokens"] == 80
    assert result["terminal_artifact"].endswith("root-job.json")
    assert result["generation_diagnostic"]["repair_attempted"] is True
    assert result["generation_diagnostic"]["attempts"][0]["validation"][
        "postconditions"
    ] == [
        {
            "id": "ui.information_capture",
            "ok": False,
            "expected": ["attachment"],
            "actual": [],
        }
    ]
    assert "qualification" not in json.dumps(result["generation_diagnostic"])
    evidence_path = tmp_path / "bundle" / result["evidence_ref"]
    assert json.loads(evidence_path.read_text(encoding="utf-8"))[
        "candidate_artifacts"
    ] == [
        {
            "candidate_sha256": "semantic-sha",
            "evidence_ref": (
                "evidence/model-io/equipment-attempt-01/root-job/"
                "candidate.primary.json"
            ),
            "kind": "raw_model_output",
            "path": "candidate.primary.json",
            "response_sha256": "response-sha",
            "sha256": "candidate-artifact-sha",
            "stage": "primary",
            "structured": True,
        }
    ]
    assert {
        item["kind"]
        for item in result["generation_diagnostic"]["model_io_artifacts"]
    } == {
        "terminal",
        "request",
        "semantic_repair_request",
        "raw_model_output",
    }
    assert all(
        (tmp_path / "bundle" / reference).is_file()
        for reference in result["evidence_refs"]
    )


def test_runner_injects_case_webspace_into_builder_wait(tmp_path: Path) -> None:
    case = _case()
    case["steps"] = [
        {
            "id": "create",
            "type": "builder.chat",
            "input": {"text": "Create a small application"},
            "expect": {"values": {"ok": True}},
        },
        {
            "id": "wait",
            "type": "builder.wait",
            "input": {"session_id": "session-1", "job_id": "job-1"},
            "expect": {"values": {"ok": True}},
        },
    ]
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = FixtureExecutor(
        {
            "builder.chat": {
                "ok": True,
                "artifact_root": str(tmp_path / "scenario"),
            },
            "builder.wait": {"ok": True, "status": "succeeded"},
        }
    )

    BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="wait-context",
        executor=executor,
    ).run()

    step_type, inputs = executor.calls[1]
    assert step_type == "builder.wait"
    assert inputs["webspace_id"] == "e2e-wait-context-case-en-1"
    assert inputs["artifact_root"] == str(tmp_path / "scenario")


def test_runner_captures_compact_project_primary_ref_for_cleanup(
    tmp_path: Path,
) -> None:
    case = _case()
    case["steps"] = [
        {
            "id": "create",
            "type": "builder.chat",
            "input": {
                "text": "Create a small application",
                "owns_created_draft": True,
            },
            "expect": {"values": {"ok": True}},
        }
    ]
    suite = _write_suite(tmp_path / "definitions", cases=[case])
    executor = FixtureExecutor(
        {
            "builder.chat": {
                "ok": True,
                "draft_id": "draft-1",
                "project_id": "project-1",
                "project": {
                    "id": "project-1",
                    "manifest_digest": "sha256:digest",
                    "primary_ref": "scenario:project-1",
                },
            }
        }
    )

    BuilderE2ERunner(
        suite,
        output_root=tmp_path / "runs",
        repo_root=tmp_path,
        run_id="cleanup-ownership",
        executor=executor,
    ).run()

    ownership = executor.cleanup_calls[0]["owned_artifacts"][0]
    assert ownership["primary_ref"] == "scenario:project-1"


def test_visible_archetype_suite_uses_ordinary_prompts_without_internal_hints() -> None:
    suite_path = Path("e2e/builder/development/archetypes/suite.yaml").resolve()
    loaded = load_builder_e2e_suite(suite_path)

    assert len(loaded.cases) == 8
    assert {case["locale"] for case in loaded.cases} == {"en", "ru"}
    prohibited = (
        "scenario_default",
        "widget",
        "component id",
        "domain pack",
        "adaos/",
        "page_schema",
        "semantic abi",
        "recipe.",
    )
    for case in loaded.cases:
        assert "visible" in case.get("tags", [])
        chat_text = "\n".join(
            str(step.get("input", {}).get("text") or "")
            for step in case["steps"]
            if step["type"] == "builder.chat"
        ).lower()
        assert chat_text
        assert "${run_id}" not in chat_text
        assert "${case_instance_id}" in chat_text
        assert not any(token in chat_text for token in prohibited)


def test_client_profile_evidence_rejects_core_components_missing_at_runtime(
    tmp_path: Path,
) -> None:
    inventory_path = (
        tmp_path
        / "src/adaos/integrations/adaos-client/architecture/evidence"
        / "client-capability-inventory.v1.json"
    )
    catalog_path = tmp_path / "src/adaos/abi/ui.capability_catalog.v1.json"
    inventory_path.parent.mkdir(parents=True)
    catalog_path.parent.mkdir(parents=True)
    inventory_path.write_text(
        json.dumps(
            {
                "schema": "adaos.client.capability_inventory.v1",
                "digest": "sha256:inventory",
                "widgets": [
                    {
                        "type": "ui.form",
                        "classification": "generic",
                    }
                ],
                "semantics": {"unsupported": {"action_kinds": ["emit"]}},
            }
        ),
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps(
            {
                "catalog_version": "test",
                "components": [
                    {"manifest": {"widget_type": "ui.form"}},
                    {"manifest": {"widget_type": "ui.table"}},
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = _client_capability_environment(tmp_path)

    assert evidence["status"] == "incompatible"
    assert evidence["missing_runtime_types"] == ["ui.table"]
    assert evidence["semantic_unsupported"] == {"action_kinds": ["emit"]}
