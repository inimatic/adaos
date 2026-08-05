from __future__ import annotations

from adaos.services.env_policy import env_bool, env_csv, env_float, env_int, truthy


def test_truthy_uses_consistent_boolean_tokens() -> None:
    assert truthy("yes") is True
    assert truthy("on") is True
    assert truthy("0") is False
    assert truthy("off") is False
    assert truthy(None, default=True) is True
    assert truthy("unexpected", default=False) is False


def test_env_numeric_helpers_clamp_values(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TEST_INT", "2")
    monkeypatch.setenv("ADAOS_TEST_FLOAT", "12.5")

    assert env_int("ADAOS_TEST_INT", 10, minimum=5) == 5
    assert env_float("ADAOS_TEST_FLOAT", 1.0, maximum=10.0) == 10.0


def test_env_bool_and_csv(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_TEST_BOOL", "true")
    monkeypatch.setenv("ADAOS_TEST_CSV", "a, b, a, , c")

    assert env_bool("ADAOS_TEST_BOOL") is True
    assert env_csv("ADAOS_TEST_CSV") == ["a", "b", "c"]
