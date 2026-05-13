from __future__ import annotations

from adaos.services import access_links
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

