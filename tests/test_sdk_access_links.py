from __future__ import annotations

from adaos.sdk.data import access_links as sdk_access_links


def test_browser_link_list_uses_metadata_draft_name_when_display_name_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_access_links._service,
        "browser_snapshot",
        lambda: [
            {
                "id": "dev_68e58ce1-2e6b-4615-9b0d-0e8cb46eccbb",
                "display_name": "",
                "access_class": "device",
                "browser_family": "Chrome",
                "os_name": "Windows",
                "form_factor": "Desktop",
                "last_seen_at": 1715000000.0,
            }
        ],
    )

    items = sdk_access_links.list_browser_links()

    assert items[0]["display_name"] == "Chrome on Windows"
    assert items[0]["effective_name"] == "Chrome on Windows"
    assert items[0]["draft_name"] == "Chrome on Windows"
    assert items[0]["display_name_source"] == "browser_metadata"


def test_get_browser_link_preserves_user_display_name_over_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_access_links._service,
        "browser_snapshot",
        lambda: [],
    )
    monkeypatch.setattr(
        sdk_access_links._service,
        "get_link",
        lambda kind, device_id: {
            "id": device_id,
            "kind": kind,
            "display_name": "Dev Browser",
            "browser_family": "Chrome",
            "os_name": "Windows",
        },
    )

    item = sdk_access_links.get_browser_link("browser-1")

    assert item is not None
    assert item["display_name"] == "Dev Browser"
    assert item["effective_name"] == "Dev Browser"
    assert item["display_name_source"] == "policy"


def test_get_browser_link_prefers_live_browser_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_access_links._service,
        "browser_snapshot",
        lambda: [
            {
                "id": "dev-browser::tab-1",
                "kind": "browser",
                "display_name": "",
                "access_class": "client",
                "browser_family": "Chrome",
                "os_name": "Windows",
                "online": True,
            }
        ],
    )
    monkeypatch.setattr(
        sdk_access_links._service,
        "get_link",
        lambda _kind, _device_id: None,
    )

    item = sdk_access_links.get_browser_link("dev-browser::tab-1")

    assert item is not None
    assert item["display_name"] == "Chrome on Windows"
    assert item["online"] is True
    assert item["access_class"] == "client"


def test_rename_browser_device_name_uses_separate_policy_field(monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    def _fake_rename(device_id: str, device_name: str):
        seen.append((device_id, device_name))
        return {
            "id": device_id,
            "display_name": "Chrome",
            "device_display_name": device_name,
        }

    monkeypatch.setattr(sdk_access_links._service, "rename_browser_device_name", _fake_rename)

    result = sdk_access_links.rename_browser_device_name("dev-browser", "Мой телефон")

    assert seen == [("dev-browser", "Мой телефон")]
    assert result["display_name"] == "Chrome"
    assert result["device_display_name"] == "Мой телефон"


def test_browser_link_effective_name_separates_device_and_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        sdk_access_links._service,
        "browser_snapshot",
        lambda: [
            {
                "id": "dev-phone",
                "display_name": "Chrome",
                "device_display_name": "Мой телефон",
                "access_class": "device",
                "browser_family": "Chrome",
            },
            {
                "id": "dev-phone::webrtc",
                "display_name": "Chrome",
                "device_display_name": "Мой телефон",
                "access_class": "client",
            },
        ],
    )

    by_id = {item["id"]: item for item in sdk_access_links.list_browser_links()}

    assert by_id["dev-phone"]["title"] == "Мой телефон"
    assert by_id["dev-phone"]["effective_name"] == "Мой телефон"
    assert by_id["dev-phone"]["endpoint_display_name"] == "Chrome"
    assert by_id["dev-phone::webrtc"]["title"] == "Chrome"
    assert by_id["dev-phone::webrtc"]["endpoint_display_name"] == "Chrome"
