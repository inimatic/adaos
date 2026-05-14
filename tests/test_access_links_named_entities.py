from __future__ import annotations

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

    def _emit(kind, previous, current, *, reason):
        events.append(
            {
                "kind": kind,
                "reason": reason,
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
