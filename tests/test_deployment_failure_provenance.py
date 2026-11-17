import json

from adaos.services.project_deployment.adapters import LocalComponentDeploymentAdapter
from adaos.services.project_deployment.execution import _safe_error


def test_wrapped_failure_retains_only_bounded_core_frame_identities():
    try:
        LocalComponentDeploymentAdapter._require_package(None)
    except Exception as cause:
        wrapper = RuntimeError("private credential: must-never-appear")
        wrapper.__cause__ = cause
        result = _safe_error(wrapper, code="adapter_phase_failed")
    assert result["origin_frames"]
    assert any(frame["function"] == "_require_package" for frame in result["origin_frames"])
    assert len(result["origin_frames"]) <= 16
    assert all(set(frame) == {"module", "function", "line"} for frame in result["origin_frames"])
    assert all(frame["module"].startswith("adaos.") for frame in result["origin_frames"])
    assert "must-never-appear" not in json.dumps(result)
    assert "test_deployment_failure_provenance" not in json.dumps(result)


def test_failure_provenance_does_not_inspect_values_or_loop_on_a_cause_cycle():
    first = RuntimeError("secret-one")
    second = ValueError("password-two")
    first.__cause__ = second
    second.__cause__ = first
    result = _safe_error(first, code="adapter_phase_failed")
    assert "origin_frames" not in result
    assert "secret-one" not in json.dumps(result)
    assert "password-two" not in json.dumps(result)
