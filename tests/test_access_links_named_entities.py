from __future__ import annotations

import sys
from types import SimpleNamespace

from adaos.services import access_links
from adaos.services import named_entities
from adaos.services.yjs import gateway_ws


def _patch_registry_store(monkeypatch):
    store: dict[tuple[str, str], dict[str, object]] = {}

    def _get(ns: str, key: str):
        value = store.get((ns, key))
        return dict(value or {})

    def _put(ns: str, key: str, value: dict[str, object]) -> None:
        store[(ns, key)] = dict(value)

    monkeypatch.setattr(access_links.sqlite_db, "durable_state_get", _get)
    monkeypatch.setattr(access_links.sqlite_db, "durable_state_put", _put)
    return store


def test_browser_session_metadata_updates_emit_named_entity_invalidation(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    events: list[dict[str, object]] = []

    def _emit(kind, previous, current, *, reason, registry_changed):
        events.append(
            {
                "kind": kind,
                "reason": reason,
                "registry_changed": registry_changed,
                "previous": dict(previous or {}),
                "current": dict(current or {}),
            }
        )

    monkeypatch.setattr(access_links, "_emit_entity_registry_changed", _emit)

    saved = access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        connection_state="connected",
        online=True,
        browser_family="Edge",
        os_name="Windows",
        form_factor="Desktop",
        user_agent="Mozilla/5.0 Edg/123",
    )

    assert saved is not None
    assert saved["browser_family"] == "Edge"
    assert saved["os_name"] == "Windows"
    assert saved["form_factor"] == "Desktop"
    assert len(events) == 1
    assert events[0]["kind"] == "browser"
    assert events[0]["reason"] == "browser_session.changed"

    events.clear()
    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        connection_state="closed",
        online=False,
    )
    assert events == []

    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        browser_family="Firefox",
    )
    assert len(events) == 1
    assert events[0]["current"]["browser_family"] == "Firefox"


def test_browser_snapshot_includes_active_yws_scoped_clients(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        gateway_ws,
        "active_browser_session_snapshot",
        lambda: {
            "peers": [
                {
                    "device_id": "dev-browser",
                    "client_limit_id": "tab-1",
                    "webspace_id": "desktop",
                    "connection_state": "connected",
                    "session_count": 1,
                }
            ]
        },
    )

    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        online=True,
        browser_family="Chrome",
    )

    snapshot = access_links.browser_snapshot()

    by_id = {item["id"]: item for item in snapshot}
    assert by_id["dev-browser"]["access_class"] == "device"
    assert by_id["dev-browser::tab-1"]["access_class"] == "client"
    assert by_id["dev-browser::tab-1"]["online"] is True
    assert by_id["dev-browser::tab-1"]["last_webspace_id"] == "desktop"
    assert by_id["dev-browser::tab-1"]["parent_browser_device_id"] == "dev-browser"
    assert by_id["dev-browser::tab-1"]["browser_client_id"] == "tab-1"


def test_browser_snapshot_marks_parent_online_from_active_yws_peer(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(access_links, "_now_ts", lambda: 2000.0)
    monkeypatch.setattr(
        gateway_ws,
        "active_browser_session_snapshot",
        lambda: {
            "peers": [
                {
                    "device_id": "dev-browser",
                    "client_limit_id": "tab-1",
                    "webspace_id": "desktop",
                    "connection_state": "connected",
                    "session_count": 1,
                }
            ]
        },
    )

    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="old",
        online=False,
        browser_family="Chrome",
    )

    by_id = {item["id"]: item for item in access_links.browser_snapshot()}

    assert by_id["dev-browser"]["online"] is True
    assert by_id["dev-browser"]["connection_state"] == "connected"
    assert by_id["dev-browser"]["last_webspace_id"] == "desktop"
    assert by_id["dev-browser"]["last_seen_at"] == 2000.0


def test_browser_snapshot_does_not_trust_stale_persisted_browser_online(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_ws, "active_browser_session_snapshot", lambda: {"peers": []})

    monkeypatch.setattr(access_links, "_now_ts", lambda: 1000.0)
    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        connection_state="connected",
        online=True,
        browser_family="Chrome",
    )

    monkeypatch.setattr(access_links, "_now_ts", lambda: 1401.0)
    by_id = {item["id"]: item for item in access_links.browser_snapshot()}

    assert by_id["dev-browser"]["online"] is False
    assert by_id["dev-browser"]["connection_state"] == "stale"
    assert by_id["dev-browser"]["last_seen_at"] == 1000.0


def test_browser_snapshot_marks_parent_and_session_online_from_webrtc_peer(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(access_links, "_now_ts", lambda: 3000.0)
    monkeypatch.setattr(gateway_ws, "active_browser_session_snapshot", lambda: {"peers": []})

    fake_webrtc_peer = SimpleNamespace(
        webrtc_peer_snapshot=lambda: {
            "peers": [
                {
                    "device_id": "dev-browser",
                    "webspace_id": "desktop",
                    "connection_state": "connecting",
                    "yjs_channel_state": "open",
                    "events_channel_state": "open",
                }
            ]
        }
    )
    monkeypatch.setitem(sys.modules, "adaos.services.webrtc.peer", fake_webrtc_peer)

    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="old",
        online=False,
        browser_family="Chrome",
    )

    by_id = {item["id"]: item for item in access_links.browser_snapshot()}

    assert by_id["dev-browser"]["online"] is True
    assert by_id["dev-browser"]["connection_state"] == "connected"
    assert by_id["dev-browser"]["last_webspace_id"] == "desktop"
    assert by_id["dev-browser::webrtc"]["access_class"] == "client"
    assert by_id["dev-browser::webrtc"]["runtime_source"] == "webrtc_peer"
    assert by_id["dev-browser::webrtc"]["yjs_channel_state"] == "open"


def test_rename_browser_device_name_keeps_endpoint_display_name(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)

    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        online=True,
        browser_family="Chrome",
    )
    access_links.rename_link("browser", "dev-browser", "Chrome")

    saved = access_links.rename_browser_device_name("dev-browser", "Мой телефон")
    link = access_links.get_link("browser", "dev-browser")

    assert saved["display_name"] == "Chrome"
    assert saved["device_display_name"] == "Мой телефон"
    assert link is not None
    assert link["display_name"] == "Chrome"
    assert link["device_display_name"] == "Мой телефон"


def test_touch_browser_session_splits_device_and_endpoint_names(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)

    saved = access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        online=True,
        browser_family="Chrome",
        device_display_name="Мой телефон",
        endpoint_display_name="Chrome",
    )

    assert saved is not None
    assert saved["device_display_name"] == "Мой телефон"
    assert saved["display_name"] == "Chrome"


def test_detach_and_deny_have_distinct_admission_policy(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)

    access_links.touch_member_link("member-1", online=True, connection_state="connected")
    detached = access_links.detach_link("member", "member-1")

    assert detached["admission_policy"] == "detached"
    assert detached["revoked"] is False
    assert detached["connection_state"] == "detached"
    assert access_links.authorize_link("member", "member-1") == (True, None)

    denied = access_links.deny_link("member", "member-1")

    assert denied["admission_policy"] == "deny"
    assert denied["revoked"] is True
    assert denied["connection_state"] == "denied"
    assert access_links.authorize_link("member", "member-1") == (False, "denied")


def test_redevice_touch_merges_policy_identity_aliases(monkeypatch) -> None:
    store = _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_registry_changed_if_needed", lambda *args, **kwargs: None)
    canonical = "redevice-5a3a7b0f-b204-41ad-9637-d00898498c54"
    store[("access_links", "registry")] = {
        "redevices": {
            canonical: {
                "id": canonical,
                "kind": "redevice",
                "display_name": "Android ReDevice Legacy",
                "pair_code": "FR57P7TC",
                "code": "FR57P7TC",
                "connection_state": "online",
                "endpoint_policy": {
                    "endpoint_id": canonical,
                    "transport_profile": {"endpoint_id": canonical},
                },
                "endpoint_manifest": {"endpoint_id": "redevice-53f793b0"},
            },
            "redevice-be511fc0": {
                "id": "redevice-be511fc0",
                "kind": "redevice",
                "pair_code": "SNX68P2A",
                "code": "SNX68P2A",
                "connection_state": "online",
                "endpoint_policy": {
                    "endpoint_id": canonical,
                    "transport_profile": {"endpoint_id": canonical},
                },
                "endpoint_manifest": {"endpoint_id": "redevice-be511fc0"},
            },
        }
    }

    saved = access_links.touch_redevice_link(
        "redevice-be511fc0",
        pair_code="SNX68P2A",
        online=True,
        connection_state="online",
        endpoint_policy={"endpoint_id": canonical, "transport_profile": {"endpoint_id": canonical}},
        endpoint_manifest={"endpoint_id": "redevice-be511fc0"},
    )

    links = access_links.list_links("redevice")
    assert saved is not None
    assert saved["id"] == canonical
    assert saved["pair_code"] == "FR57P7TC"
    assert saved["endpoint_manifest"] == {"endpoint_id": "redevice-53f793b0"}
    assert [item["id"] for item in links] == [canonical]


def test_yws_browser_session_metadata_accepts_client_handshake_fields() -> None:
    metadata = gateway_ws._browser_session_metadata(
        {
            "browser_family": "Edge",
            "os_name": "Windows",
            "form_factor": "Desktop",
            "user_agent": "Mozilla/5.0 Edg/123",
        }
    )

    assert metadata == {
        "browser_family": "Edge",
        "os_name": "Windows",
        "form_factor": "Desktop",
        "user_agent": "Mozilla/5.0 Edg/123",
    }


def test_access_links_emits_specific_lifecycle_events_before_registry_invalidation(monkeypatch) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr("adaos.services.agent_context.get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(
        "adaos.services.eventbus.emit",
        lambda _bus, topic, payload, source=None: emitted.append((topic, dict(payload))),
    )

    access_links._emit_entity_registry_changed(
        "browser",
        {},
        {
            "id": "dev-browser",
            "kind": "browser",
            "browser_family": "Edge",
            "os_name": "Windows",
            "form_factor": "Desktop",
            "last_webspace_id": "desktop",
        },
        reason="browser_session.changed",
        registry_changed=True,
    )

    assert [topic for topic, _payload in emitted] == [
        named_entities.ENTITY_OBSERVED,
        named_entities.ENTITY_DRAFT_NAME_SUGGESTED,
        named_entities.ENTITY_REGISTRY_CHANGED,
    ]
    assert emitted[1][1]["current"]["draft_name"] == "Edge on Windows"


def test_transport_metadata_emits_lifecycle_without_registry_invalidation(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    calls: list[dict[str, object]] = []

    def _emit(_kind, _previous, _current, *, reason, registry_changed):
        calls.append({"reason": reason, "registry_changed": registry_changed})

    monkeypatch.setattr(access_links, "_emit_entity_registry_changed", _emit)
    access_links.touch_browser_session("dev-browser", webspace_id="desktop", browser_family="Edge")
    calls.clear()

    access_links.touch_browser_session("dev-browser", user_agent="Mozilla/5.0 Edg/999")

    assert calls == [{"reason": "browser_session.changed", "registry_changed": False}]


def test_add_browser_alias_persists_label_and_updates_named_entity_resolution(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(access_links, "_emit_entity_event_envelopes", lambda events: emitted.extend(events))

    access_links.touch_browser_session(
        "dev-browser",
        webspace_id="desktop",
        browser_family="Edge",
        os_name="Windows",
        form_factor="Desktop",
    )

    result = access_links.add_link_alias(
        "browser",
        "dev-browser",
        "work browser",
        locale="en",
        actor="user:operator",
        request_id="req-1",
    )

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["entry"]["labels"] == [
        {
            "text": "work browser",
            "locale": "en",
            "role": "alias",
            "status": "confirmed",
            "source": "access_links",
            "actor": "user:operator",
            "request_id": "req-1",
            "created_at": result["entry"]["labels"][0]["created_at"],
        }
    ]
    assert [event["topic"] for event in emitted] == [
        named_entities.ENTITY_ALIAS_ADDED,
        named_entities.ENTITY_REGISTRY_CHANGED,
    ]

    resolved = named_entities.resolve_text(
        "open work browser settings",
        kind="device.browser",
        request_locale="en",
    )

    assert resolved["resolved_entities"][0]["canonical_ref"] == "device:browser:dev-browser"
    assert resolved["resolved_entities"][0]["match_type"] == "alias"
    assert resolved["resolved_entities"][0]["locale"] == "en"


def test_add_browser_alias_conflict_does_not_mutate_registry(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(access_links, "_emit_entity_event_envelopes", lambda events: emitted.extend(events))

    access_links.touch_browser_session("browser-1", webspace_id="desktop")
    access_links.touch_browser_session("browser-2", webspace_id="desktop")
    first = access_links.add_link_alias("browser", "browser-1", "screen", locale="en")
    emitted.clear()

    second = access_links.add_link_alias("browser", "browser-2", "screen", locale="en")

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["status"] == "conflict"
    assert second["proposal"]["conflicts"][0]["canonical_ref"] == "device:browser:browser-1"
    assert access_links.get_link("browser", "browser-2")["labels"] == []
    assert [event["topic"] for event in emitted] == [
        named_entities.ENTITY_ALIAS_CONFLICT_DETECTED,
    ]


def test_add_browser_alias_rejects_stale_base_fingerprint(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    monkeypatch.setattr(access_links, "_emit_entity_event_envelopes", lambda events: None)

    access_links.touch_browser_session("browser-1", webspace_id="desktop")
    registry = named_entities.compact_registry_payload(kind="device.browser", webspace_id="desktop")
    base_fingerprint = registry["items"][0]["fingerprint"]
    access_links.rename_link("browser", "browser-1", "Renamed browser")

    result = access_links.add_link_alias(
        "browser",
        "browser-1",
        "office browser",
        locale="en",
        base_fingerprint=base_fingerprint,
    )

    assert result["ok"] is False
    assert result["status"] == "stale"
    assert result["proposal"]["reason"] == "base_fingerprint_mismatch"
    assert access_links.get_link("browser", "browser-1")["labels"] == []


def test_remove_browser_alias_persists_change_and_updates_resolution(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(access_links, "_emit_entity_event_envelopes", lambda events: emitted.extend(events))

    access_links.touch_browser_session("dev-browser", webspace_id="desktop")
    access_links.add_link_alias("browser", "dev-browser", "work browser", locale="en")
    emitted.clear()

    result = access_links.remove_link_alias("browser", "dev-browser", "work browser", locale="en")

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["entry"]["labels"] == []
    assert [event["topic"] for event in emitted] == [
        named_entities.ENTITY_ALIAS_REMOVED,
        named_entities.ENTITY_REGISTRY_CHANGED,
    ]
    resolved = named_entities.resolve_text("open work browser", kind="device.browser", request_locale="en")
    assert resolved["resolved_entities"] == []


def test_deprecate_browser_alias_marks_label_and_updates_resolution(monkeypatch) -> None:
    _patch_registry_store(monkeypatch)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(access_links, "_emit_entity_event_envelopes", lambda events: emitted.extend(events))

    access_links.touch_browser_session("dev-browser", webspace_id="desktop")
    access_links.add_link_alias("browser", "dev-browser", "work browser", locale="en")
    emitted.clear()

    result = access_links.deprecate_link_alias("browser", "dev-browser", "work browser", locale="en")

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["entry"]["labels"][0]["status"] == "deprecated"
    assert [event["topic"] for event in emitted] == [
        named_entities.ENTITY_ALIAS_DEPRECATED,
        named_entities.ENTITY_REGISTRY_CHANGED,
    ]
    resolved = named_entities.resolve_text("open work browser", kind="device.browser", request_locale="en")
    assert resolved["resolved_entities"][0]["canonical_ref"] == "device:browser:dev-browser"
