from __future__ import annotations

from adaos.services.nlu.runtime_flags import normalize_flag_updates, normalize_flags


def test_normalize_flags_accepts_stage_aliases() -> None:
    flags = normalize_flags(
        {
            "regexp": "off",
            "neuro_lite": "on",
            "neure": 0,
            "rasa": "on",
        }
    )

    assert flags["regex_enabled"] is False
    assert flags["neuro_lite_enabled"] is True
    assert flags["neural_enabled"] is False
    assert flags["rasa_enabled"] is True


def test_normalize_flag_updates_keeps_partial_updates_partial() -> None:
    assert normalize_flag_updates({"flags": {"regex_enabled": False}}) == {"regex_enabled": False}


def test_normalize_flag_updates_accepts_neuro_lite_aliases() -> None:
    assert normalize_flag_updates({"flags": {"neuro": "off", "neuro_light": "on"}}) == {
        "neuro_lite_enabled": True
    }
