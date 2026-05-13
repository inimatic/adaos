from __future__ import annotations

from types import SimpleNamespace

from adaos.services import node_display


def test_node_display_prefers_registered_name_over_observed_hostname() -> None:
    payload = node_display.node_display_payload(
        node_id="member-1",
        role="member",
        node_names=["Kitchen Display"],
        observed_name="ZVERZVE-A1BNQF7",
        display_index=1,
    )

    assert payload["node_label"] == "Kitchen Display"
    assert payload["node_name_source"] == "registered"
    assert payload["node_has_explicit_name"] is True


def test_node_display_uses_observed_hostname_before_node_fallback() -> None:
    payload = node_display.node_display_payload(
        node_id="hub-1",
        role="hub",
        node_names=[],
        observed_name="ZVERZVE-A1BNQF7",
        display_index=0,
    )

    assert payload["node_label"] == "ZVERZVE-A1BNQF7"
    assert payload["node_name_source"] == "observed"
    assert payload["node_has_explicit_name"] is False


def test_node_display_falls_back_to_node_index_when_observed_name_is_noise() -> None:
    payload = node_display.node_display_payload(
        node_id="hub-1",
        role="hub",
        node_names=[],
        observed_name="hub",
        display_index=0,
    )

    assert payload["node_label"] == "Node 0"
    assert payload["node_name_source"] == "fallback"


def test_node_display_from_config_uses_hostname_attribute(monkeypatch) -> None:
    monkeypatch.setattr(node_display, "load_node_display_runtime_state", lambda: {})
    payload = node_display.node_display_from_config(
        SimpleNamespace(
            role="hub",
            node_id="hub-1",
            node_names=[],
            primary_node_name="",
            hostname="ZVERZVE-A1BNQF7",
        )
    )

    assert payload["node_label"] == "ZVERZVE-A1BNQF7"
    assert payload["node_name_source"] == "observed"


def test_node_display_from_directory_node_uses_hostname_before_fallback() -> None:
    payload = node_display.node_display_from_directory_node(
        {
            "node_id": "member-1",
            "roles": ["member"],
            "hostname": "Kitchen Host",
            "display_index": 2,
            "runtime_projection": {"node_names": []},
        }
    )

    assert payload["node_label"] == "Kitchen Host"
    assert payload["node_name_source"] == "observed"
