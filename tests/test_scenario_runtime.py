from __future__ import annotations

from typing import Any, Mapping

import pytest

from adaos.sdk.scenarios.runtime import ActionRegistry, ScenarioModel, ScenarioRuntime


def _model(*, route: str, depends: list[str]) -> ScenarioModel:
    return ScenarioModel.from_payload(
        {
            "id": "research-smoke",
            "version": "0.1.0",
            "depends": depends,
            "runtime": {"skills": {"required": [*depends, "runtime_only_skill"]}},
            "steps": [
                {
                    "name": "invoke",
                    "call": route,
                    "args": {"study_id": "study-1"},
                    "save_as": "result",
                }
            ],
        },
        fallback_id="fallback",
    )


def test_scenario_model_collects_unique_declared_skill_dependencies() -> None:
    model = _model(route="research_skill.get_study", depends=["research_skill"])

    assert model.depends == ["research_skill", "runtime_only_skill"]


@pytest.mark.parametrize("separator", [".", ":"])
def test_runtime_dispatches_declared_skill_tool_routes(separator: str) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def _run(skill: str, tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
        calls.append((skill, tool, dict(args)))
        return {"ok": True, "study_id": args["study_id"]}

    runtime = ScenarioRuntime(dependency_runner=_run)
    model = _model(route=f"research_skill{separator}get_study", depends=["research_skill"])

    result = runtime.run(model)

    assert calls == [("research_skill", "get_study", {"study_id": "study-1"})]
    assert result["result"] == {"ok": True, "study_id": "study-1"}


def test_runtime_does_not_dispatch_undeclared_skill_route() -> None:
    runtime = ScenarioRuntime(dependency_runner=lambda *_args: {"unexpected": True})
    model = _model(route="undeclared_skill.get_study", depends=["research_skill"])

    with pytest.raises(RuntimeError, match="unknown route: undeclared_skill.get_study"):
        runtime.run(model)


def test_runtime_preserves_explicit_route_override() -> None:
    registry = ActionRegistry()
    registry.register("research_skill.get_study", lambda args: {"source": "explicit", **args})
    runtime = ScenarioRuntime(
        registry=registry,
        dependency_runner=lambda *_args: pytest.fail("dependency runner must not replace an explicit route"),
    )
    model = _model(route="research_skill.get_study", depends=["research_skill"])

    result = runtime.run(model)

    assert result["result"] == {"source": "explicit", "study_id": "study-1"}
