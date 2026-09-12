import importlib.util
from pathlib import Path

import pytest

from adaos.e2e.builder import _load_document, _resolve_value


def test_application_probe_admits_only_owned_skills():
    path = Path(__file__).parents[1] / "e2e/stand/check-application-tools.py"
    spec = importlib.util.spec_from_file_location("application_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = {"components": {"owned": [{"ref": "skill:owned"}], "dependencies": [{"ref": "skill:shared"}]}}
    assert module.admitted_skill(manifest, "owned") == "owned"
    for skill in ("shared", "../owned", "", "owned:other", "unknown"):
        with pytest.raises(ValueError):
            module.admitted_skill(manifest, skill)


def test_application_probe_fixture_separates_http_status_from_domain_rejections():
    plan = _load_document(Path("e2e/builder/development/lifecycle/equipment-inspections-http.yaml"))
    steps = {step["id"]: step for step in plan["steps"]}
    assert len(steps) == len(plan["steps"]) == 18
    assert steps["reader-direct-write-denied"]["caller"] == "reader"
    assert steps["reader-direct-write-denied"]["expect"]["values"]["http_status"] == 403
    assert steps["defect-completion-denied"]["expect"]["values"]["http_status"] == 200
    context = {"run_id": "r", "case_id": "c", "case_instance_id": "test", "repetition": 1, "locale": "en",
               "outputs": {"create-item": {"body": {"result": {"item": {"id": "item"}}}}}}
    resolved = _resolve_value(steps["defect-completion-denied"]["expect"], context)
    assert resolved["values"]["body.result.problem_items.0.id"] == "item"
