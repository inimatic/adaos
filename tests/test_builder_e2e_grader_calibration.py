"""Opt-in live judge probes; not a held-out or human-labeled quality baseline."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from adaos.e2e.builder import _write_json
from adaos.e2e.builder_grading import grade_builder_prototype
from adaos.services.builder.semantic_prototype import compile_semantic_prototype
from test_builder_semantic_prototype import _multi_resource_fixture


def _probe(name: str) -> tuple[dict, str, bool]:
    _, semantic = _multi_resource_fixture()
    semantic["requirement_bindings"] = []
    if name in {"minimal-browse", "missing-update"}:
        semantic["resources"] = semantic["resources"][:1]
        semantic["relationships"] = []
        semantic["views"] = semantic["views"][:1]
        semantic["commands"] = []
    result = compile_semantic_prototype(semantic, project_ref="project:grader_calibration")
    request = "Show the work items with their result and status."
    if name in {"working-update", "missing-update"}:
        request = "Покажи пункты работы и дай изменить результат выбранного пункта."
    return {
        "webui": result["webui"], "prototype_resources": result["prototype_resources"],
    }, request, name != "missing-update"


@pytest.mark.parametrize("name", ["minimal-browse", "richer-browse", "working-update", "missing-update"])
def test_calibration_probes_compile_without_a_model(name):
    artifact, request, _expected = _probe(name)
    assert artifact["webui"]["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    assert request


@pytest.mark.skipif(os.getenv("ADAOS_E2E_LIVE_GRADER") != "1", reason="explicit live grader opt-in required")
@pytest.mark.parametrize("name", ["minimal-browse", "richer-browse", "working-update", "missing-update"])
def test_live_grader_calibration(name, monkeypatch):
    from adaos.apps.cli.app import Settings, init_ctx
    from dotenv import load_dotenv
    from adaos.services.agent_context import clear_ctx

    load_dotenv()
    if os.getenv("ENV_TYPE", "").lower() != "dev":
        pytest.fail("Live development calibration requires ENV_TYPE=dev")
    live_base = Path(os.environ["ADAOS_E2E_LIVE_BASE_DIR"]).resolve()
    if not (live_base / "node.yaml").is_file():
        pytest.fail("Live calibration requires an existing enrolled node, not a test identity")
    monkeypatch.setenv("ADAOS_BASE_DIR", str(live_base))
    clear_ctx()
    init_ctx(Settings.from_sources())
    artifact, request, expected = _probe(name)
    output = Path(os.environ["ADAOS_E2E_CALIBRATION_OUTPUT"]).resolve() / name
    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "expected.json", {
        "expected_passed": expected, "label_source": "engineering_probe_not_user_gold",
        "request": request, "artifact": artifact,
    })
    grade, _ = grade_builder_prototype(
        artifact=artifact, user_turns=[request], requirements={"primary_jobs": [request]},
        prohibited_assumptions=[], locale="ru" if "update" in name else "en",
        request_recorder=lambda value: _write_json(output / "input.json", value),
    )
    _write_json(output / "grade.json", grade)
    assert grade["passed"] == expected, grade["findings"]
