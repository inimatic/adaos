from __future__ import annotations

from adaos.services.subnet.rpc_errors import member_rpc_error_payload, rpc_error_code


def test_rpc_error_code_preserves_machine_code_from_exception_chain() -> None:
    try:
        try:
            raise RuntimeError("component_runtime_activation_failed")
        except RuntimeError as exc:
            raise RuntimeError("skill activation failed") from exc
    except RuntimeError as exc:
        assert rpc_error_code(exc) == "component_runtime_activation_failed"
        assert member_rpc_error_payload(exc) == {
            "schema": "adaos.subnet.member_rpc_error.v1",
            "code": "component_runtime_activation_failed",
            "type": "RuntimeError",
            "cause_type": "RuntimeError",
        }


def test_rpc_error_code_classifies_safe_root_exception_type_without_message() -> None:
    try:
        try:
            raise ModuleNotFoundError("No module named 'private_package'")
        except ModuleNotFoundError as exc:
            raise RuntimeError("failed to import handler from /private/path") from exc
    except RuntimeError as exc:
        payload = member_rpc_error_payload(exc)

    assert payload["code"] == "module_not_found"
    assert payload["type"] == "RuntimeError"
    assert payload["cause_type"] == "ModuleNotFoundError"
    assert "private_package" not in str(payload)
    assert "/private/path" not in str(payload)
