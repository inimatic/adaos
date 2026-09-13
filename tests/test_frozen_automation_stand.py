import importlib.util
import json
from pathlib import Path

import pytest
import yaml


spec = importlib.util.spec_from_file_location("frozen_automation", Path(__file__).parents[1] / "e2e/stand/run-frozen-automation.py")
stand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stand)


def plan(*types):
    return {"schema": "adaos.e2e.frozen_automation.v1", "steps": [
        {"id": str(index), "type": kind} for index, kind in enumerate(types)
    ]}


@pytest.mark.parametrize("forbidden", ["builder.chat", "trial.prepare", "trial.decide", "release.promote"])
def test_frozen_automation_cannot_regenerate_or_deliver(forbidden):
    with pytest.raises(ValueError, match="cannot generate"):
        stand.validate_plan(plan("automation.start", forbidden))


def test_correction_does_not_accept_functional_ui_as_prototype():
    with pytest.raises(ValueError, match="reapprove"):
        stand.validate_plan(plan("prototype.accept", "automation.submit", "automation.wait"))
    stand.validate_plan(plan("automation.submit", "automation.wait"))


def test_acceptance_order_and_single_submission():
    with pytest.raises(ValueError, match="precede"):
        stand.validate_plan(plan("automation.start", "prototype.accept"))
    with pytest.raises(ValueError, match="Exactly one"):
        stand.validate_plan(plan("automation.start", "automation.submit"))


def test_retained_equipment_plan_is_automation_only():
    path = Path(__file__).parents[1] / "e2e/builder/development/lifecycle/equipment-inspections-automation.yaml"
    stand.validate_plan(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_automation_review_requires_exact_existing_task(tmp_path):
    spec = importlib.util.spec_from_file_location("review_snapshot", Path(__file__).parents[1] / "e2e/stand/review-builder-snapshot.py")
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    metadata = {"object_type": "scenario", "object_id": "test_demo", "task_id": "task.current"}
    (tmp_path / "snapshot.json").write_text(json.dumps(metadata), encoding="utf-8")
    ui = {"ui": {"application": {"desktop": {}}}}
    (tmp_path / "webui.json").write_text(json.dumps(ui), encoding="utf-8")
    assert review.read_automation_snapshot(tmp_path, "test_demo", "task.current") == (metadata, ui)
    for target, revision in (("test_other", "task.current"), ("test_demo", "task.previous"), ("test_demo", "002")):
        with pytest.raises(ValueError, match="exact retained"):
            review.read_automation_snapshot(tmp_path, target, revision)
    (tmp_path / "webui.json").unlink()
    with pytest.raises(FileNotFoundError):
        review.read_automation_snapshot(tmp_path, "test_demo", "task.current")
