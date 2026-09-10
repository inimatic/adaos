from __future__ import annotations

import copy
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
    compare_builder_e2e_baseline,
    create_builder_e2e_baseline,
    load_builder_e2e_suite,
)


class FixtureExecutor:
    adapter_id = "fixture.v1"

    def __init__(self, outputs: Mapping[str, Mapping[str, Any]]) -> None:
        self.outputs = outputs
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
        return {"status": "passed"}


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
    suite = _write_suite(tmp_path / "definitions", cases=[_case()], repetitions=2)
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
    assert len(executor.cleanup_calls) == 2
    bundle = Path(report["bundle_dir"])
    assert (
        json.loads((bundle / "report.json").read_text(encoding="utf-8"))["status"]
        == "passed"
    )
    assert len(list((bundle / "cases" / "case-en").glob("*.json"))) == 2


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
