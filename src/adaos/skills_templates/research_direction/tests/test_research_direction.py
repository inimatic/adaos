from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


def _module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("research_direction.handlers.main", root / "handlers" / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_template_is_one_skill_direction_and_fails_closed_before_codex() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "skill.yaml").read_text(encoding="utf-8"))
    descriptor = yaml.safe_load((root / "research.yaml").read_text(encoding="utf-8"))
    assert manifest["research_direction"]["owner"] == "skill:new_skill"
    assert descriptor["constraints"]["no_direction_scenario"] is True
    readiness = _module().execution_readiness()
    assert readiness["ready"] is False
    assert readiness["missing"]


def test_candidate_validation_requires_scientific_structure() -> None:
    module = _module()
    invalid = module.validate_research_prototype({"title": "TLP"})
    assert invalid["accepted"] is False
    valid = module.validate_research_prototype(
        {
            "schema": "adaos.research.prototype.v1",
            "title": "TLP replication",
            "research_question": "Does TLP improve over MaxPool under a paired protocol?",
            "hypotheses": [{"id": "H1", "statement": "TLP changes validation accuracy."}],
            "experimental_plan": {"design": "paired"},
            "evaluation_plan": {"primary_estimand": "paired accuracy delta"},
        }
    )
    assert valid["accepted"] is True
