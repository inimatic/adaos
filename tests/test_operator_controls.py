from __future__ import annotations

import logging

from adaos.services import operator_controls


def test_operator_controls_persist_and_apply(tmp_path, monkeypatch) -> None:
    path = tmp_path / "runtime_controls.json"
    monkeypatch.setattr(operator_controls, "_path", lambda: path)

    updated = operator_controls.update_controls(
        {
            "core_auto_update": False,
            "application_auto_update_default": False,
            "log_level": "DEBUG",
            "rasa_enabled": False,
        }
    )

    assert updated["core_auto_update"] is False
    assert updated["application_auto_update_default"] is False
    assert updated["rasa_enabled"] is False
    assert operator_controls.application_update_policy_default() == "notify"
    assert logging.getLogger("adaos").level == logging.DEBUG


def test_operator_controls_reject_unknown_log_level(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(operator_controls, "_path", lambda: tmp_path / "runtime_controls.json")

    try:
        operator_controls.update_controls({"log_level": "TRACE"})
    except ValueError as exc:
        assert "unsupported log level" in str(exc)
    else:
        raise AssertionError("invalid log level was accepted")
