"""Exact-source qualification can run a long suite without a model or fake edit."""
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess

import pytest

from adaos.services import skill_factory_worker as worker_module
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker, _generated_test_budget


def assignment(timeout=600, wall=3600):
    return {
        "constraints": {"mode": "dev_ticket_repair", "exact_changed_paths": ["skills/demo/skill.yaml"]},
        "realize_request": {"artifacts": {
            "execution_budget": {"max_wall_seconds": wall},
            "repair_hints": {
                "validation_only": True,
                "validation_timeout_seconds": timeout,
                "source_preconditions": [{"path": "skills/demo/skill.yaml", "sha256": "sha256:" + "a" * 64, "size": 1}],
            },
        }},
    }


def test_exact_source_suite_has_an_explicit_bounded_budget():
    value = assignment()
    before = deepcopy(value)
    assert _generated_test_budget(value) == {
        "schema": "adaos.builder.validation_budget.v1",
        "packaged_pytest_wall_seconds": 600,
        "execution_max_wall_seconds": 3600,
        "source": "builder.exact_source_validation",
    }
    assert value == before


@pytest.mark.parametrize("timeout", [True, "600", 0, -1, 901])
def test_exact_source_budget_rejects_invalid_or_unbounded_requests(timeout):
    with pytest.raises(ValueError):
        _generated_test_budget(assignment(timeout))


def test_exact_source_budget_cannot_exceed_its_execution_authority():
    with pytest.raises(ValueError, match="execution authority"):
        _generated_test_budget(assignment(600, 599))


def test_development_session_is_the_execution_authority():
    value = assignment()
    value["realize_request"]["artifacts"]["development_context"] = {
        "execution_budget": {"max_wall_seconds": 300},
    }
    with pytest.raises(ValueError, match="execution authority"):
        _generated_test_budget(value)


@pytest.mark.parametrize("missing", ["validation_only", "source_preconditions"])
def test_explicit_budget_requires_unchanged_pinned_source(missing):
    value = assignment()
    del value["realize_request"]["artifacts"]["repair_hints"][missing]
    with pytest.raises(ValueError, match="pinned validation-only source"):
        _generated_test_budget(value)


def test_explicit_budget_does_not_expand_ordinary_repair_authority():
    value = assignment()
    value["constraints"]["mode"] = "autonomous_bounded"
    with pytest.raises(ValueError, match="pinned validation-only source"):
        _generated_test_budget(value)


def test_explicit_budget_requires_every_exact_path_to_be_pinned():
    value = assignment()
    value["constraints"]["exact_changed_paths"].append("skills/demo/handlers/run.py")
    with pytest.raises(ValueError, match="pinned validation-only source"):
        _generated_test_budget(value)


def test_ordinary_validation_keeps_its_existing_derived_limit():
    value = assignment()
    del value["realize_request"]["artifacts"]["repair_hints"]["validation_timeout_seconds"]
    assert _generated_test_budget(value)["packaged_pytest_wall_seconds"] == 60


def test_extended_budget_does_not_bypass_source_pin_verification(tmp_path):
    value = assignment()
    source = tmp_path / "skills/demo/skill.yaml"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original")
    value["realize_request"]["artifacts"]["repair_hints"]["source_preconditions"] = [{
        "path": "skills/demo/skill.yaml", "sha256": "sha256:" + hashlib.sha256(b"original").hexdigest(), "size": 8,
    }]
    assert LocalSkillFactoryWorker._validation_only_from_assignment(value, tmp_path)["model_tokens"] == 0
    source.write_bytes(b"modified")
    with pytest.raises(ValueError, match="source precondition changed"):
        LocalSkillFactoryWorker._validation_only_from_assignment(value, tmp_path)


def test_extended_budget_reaches_the_packaged_test_process(tmp_path, monkeypatch):
    tests = tmp_path / "run/workspace/skills/demo/tests"
    tests.mkdir(parents=True)
    (tests / "test_slow.py").write_text("def test_slow(): pass\n", encoding="utf8")
    worker = LocalSkillFactoryWorker(state_dir=tmp_path / "state", repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev/skills", dev_scenarios_root=tmp_path / "dev/scenarios")
    observed = {}

    def timeout_run(*args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(["python", "-m", "pytest"], kwargs["timeout"], output="partial")

    monkeypatch.setattr(worker_module, "_run", timeout_run)
    checks, errors = [], []
    worker._run_generated_tests(tmp_path / "run/workspace", checks, errors, assignment=assignment())
    assert observed["timeout"] == 600
    assert checks[0]["validation_budget"]["source"] == "builder.exact_source_validation"
    assert checks[0]["status"] == "timeout"
    assert "after 600 seconds" in errors[0]
