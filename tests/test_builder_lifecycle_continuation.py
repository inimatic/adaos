import copy
import importlib.util
from pathlib import Path

import pytest

from adaos.e2e.builder import _digest


def test_continuation_preserves_case_and_cannot_skip_failed_work():
    path = Path(__file__).parents[1] / "e2e/stand/continue-builder-lifecycle.py"
    spec = importlib.util.spec_from_file_location("lifecycle_continuation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case = {"steps": [{"id": "create", "type": "builder.chat"}, {"id": "approve", "type": "prototype.accept"},
                      {"id": "implement", "type": "automation.start"}]}
    checkpoint = {"case_digest": _digest(case), "steps": [{**step, "status": status}
                  for step, status in zip(case["steps"], ("passed", "failed"))]}
    original = copy.deepcopy(checkpoint)
    assert module.lifecycle_tail(checkpoint, case) == case["steps"][1:]
    assert checkpoint == original
    for changed in ({**checkpoint, "active_step": {"id": "approve"}}, {**checkpoint, "case_digest": "other"},
                    {**checkpoint, "steps": []}, {**checkpoint, "steps": [{**step, "status": "failed"} for step in checkpoint["steps"]]},
                    {**checkpoint, "steps": [{**checkpoint["steps"][0], "id": "unknown"}, checkpoint["steps"][1]]}):
        with pytest.raises(ValueError):
            module.lifecycle_tail(changed, case)
    non_lifecycle = copy.deepcopy(case)
    non_lifecycle["steps"][-1]["type"] = "builder.chat"
    with pytest.raises(ValueError, match="lifecycle tail"):
        module.lifecycle_tail({**checkpoint, "case_digest": _digest(non_lifecycle)}, non_lifecycle)
