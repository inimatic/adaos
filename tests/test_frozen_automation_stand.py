import importlib.util
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
