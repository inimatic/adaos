from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from adaos.services.runtime_activation_observations import (
    classify_runtime_activation_failure,
    emit_runtime_activation_failure,
    emit_runtime_activation_success,
)


def test_activation_failure_classifier_preserves_gate_semantics() -> None:
    assert classify_runtime_activation_failure("skill tests failed: test_demo", default="prepare") == "tests"
    assert classify_runtime_activation_failure("scenario validation failed", default="install") == "validation"
    assert classify_runtime_activation_failure("dependency unavailable", default="prepare") == "prepare"


class _Bus:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


def test_activation_failure_event_matches_abi() -> None:
    bus = _Bus()

    payload = emit_runtime_activation_failure(
        bus,
        component_type="scenario",
        component_id="demo_metrics",
        stage="validation",
        error="scenario contract is invalid",
        source="cli.scenario.validate",
        report_policy="project_inbox",
        webspace_id="desktop",
        operation_id="scenario-validate:demo_metrics",
    )

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "abi"
        / "runtime.activation_observation.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)
    assert bus.events[0].type == "scenarios.activation.failed"
    assert bus.events[0].payload["report_policy"] == "project_inbox"
    assert bus.events[0].payload["scenario_id"] == "demo_metrics"


def test_activation_success_event_matches_abi_and_keeps_exact_gate() -> None:
    bus = _Bus()

    payload = emit_runtime_activation_success(
        bus,
        component_type="scenario",
        component_id="demo_metrics",
        stage="validation",
        source="cli.scenario.validate",
        report_policy="project_inbox",
        webspace_id="desktop",
        operation_id="scenario-validate:demo_metrics",
    )

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "abi"
        / "runtime.activation_observation.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)
    assert bus.events[0].type == "scenarios.activation.passed"
    assert bus.events[0].payload["status"] == "passed"
    assert bus.events[0].payload["stage"] == "validation"
    assert "error" not in bus.events[0].payload
