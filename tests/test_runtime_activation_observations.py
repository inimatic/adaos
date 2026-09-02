from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from adaos.services.runtime_activation_observations import emit_runtime_activation_failure


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
