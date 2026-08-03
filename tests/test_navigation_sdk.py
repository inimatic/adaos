from __future__ import annotations

import pytest

from adaos.sdk import navigation


def test_registration_url_uses_intent_as_the_only_discriminator() -> None:
    destination = navigation.registration_destination("4931-E638", zone="ru")

    url = navigation.build_url(destination)

    assert url == "https://inimatic.com/?intent=connect.register&zone=ru&user_code=4931-E638"
    assert "mode=" not in url
    assert navigation.parse_url(url) == destination


def test_webspace_url_preserves_the_full_expected_context() -> None:
    destination = navigation.webspace_destination(
        zone="ru",
        subnet_id="sn_6acf0c01",
        webspace_id="dev1-dev",
        space_kind="preview",
        expected_scenario_id="test05_recipes",
        expected_revision="UI 003",
        preview_stage="prototype",
    )

    url = navigation.build_url(destination)

    assert url == (
        "https://inimatic.com/?intent=webspace.open&zone=ru&subnet_id=sn_6acf0c01"
        "&webspace_id=dev1-dev&space_kind=preview&expected_scenario_id=test05_recipes"
        "&expected_revision=UI+003&preview_stage=prototype"
    )
    assert navigation.parse_url(url) == destination


def test_mode_is_rejected_instead_of_silently_normalized() -> None:
    with pytest.raises(ValueError, match="mode is unsupported"):
        navigation.parse_url("https://inimatic.com/?mode=registration&user_code=4931-E638")


def test_webspace_resolution_is_ordered_and_never_mutates_state() -> None:
    destination = navigation.webspace_destination(
        zone="ru",
        subnet_id="sn_6acf0c01",
        webspace_id="dev1-dev",
        space_kind="preview",
        expected_scenario_id="test05_recipes",
        expected_revision="UI 003",
        preview_stage="prototype",
    )

    zone = navigation.resolve_destination(destination, current={"zone": "lo", "authenticated": True})
    assert (zone["status"], zone["action"], zone["reason"]) == (
        "input_required",
        "switch_zone",
        "zone_mismatch",
    )

    auth = navigation.resolve_destination(destination, current={"zone": "ru", "authenticated": False})
    assert (auth["status"], auth["action"]) == ("waiting", "authenticate")

    subnet = navigation.resolve_destination(
        destination,
        current={"zone": "ru", "authenticated": True, "subnet_id": "sn_other"},
    )
    assert (subnet["status"], subnet["action"]) == ("input_required", "switch_subnet")

    webspace = navigation.resolve_destination(
        destination,
        current={
            "zone": "ru",
            "authenticated": True,
            "subnet_id": "sn_6acf0c01",
            "webspace_id": "desktop",
        },
    )
    assert (webspace["status"], webspace["action"]) == ("input_required", "switch_webspace")

    stale = navigation.resolve_destination(
        destination,
        current={
            "zone": "ru",
            "authenticated": True,
            "subnet_id": "sn_6acf0c01",
            "webspace_id": "dev1-dev",
            "space_kind": "preview",
            "state_sync_fresh": False,
        },
    )
    assert (stale["status"], stale["action"]) == ("waiting", "wait_for_sync")

    mismatch = navigation.resolve_destination(
        destination,
        current={
            "zone": "ru",
            "authenticated": True,
            "subnet_id": "sn_6acf0c01",
            "webspace_id": "dev1-dev",
            "space_kind": "preview",
            "state_sync_fresh": True,
            "current_scenario_id": "other",
            "current_revision": "UI 002",
        },
    )
    assert (mismatch["status"], mismatch["action"]) == ("input_required", "confirm_scenario")
    assert [choice["id"] for choice in mismatch["choices"]] == [
        "open_current",
        "switch_to_expected",
        "cancel",
    ]

    ready = navigation.resolve_destination(
        destination,
        current={
            "zone": "ru",
            "authenticated": True,
            "subnet_id": "sn_6acf0c01",
            "webspace_id": "dev1-dev",
            "space_kind": "preview",
            "state_sync_fresh": True,
            "current_scenario_id": "test05_recipes",
            "current_revision": "UI 003",
        },
    )
    assert (ready["status"], ready["action"], ready["reason"]) == (
        "ready",
        "open",
        "destination_matches_current_context",
    )
