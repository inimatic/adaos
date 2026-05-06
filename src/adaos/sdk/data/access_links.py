from __future__ import annotations

from typing import Any

from adaos.services import access_links as _service


def list_browser_links() -> list[dict[str, Any]]:
    return _service.browser_snapshot()


def list_member_links() -> list[dict[str, Any]]:
    return _service.member_snapshot()


def get_browser_link(device_id: str) -> dict[str, Any] | None:
    return _service.get_link("browser", device_id)


def get_member_link(node_id: str) -> dict[str, Any] | None:
    return _service.get_link("member", node_id)


def rename_browser_link(device_id: str, display_name: str) -> dict[str, Any]:
    return _service.rename_link("browser", device_id, display_name)


def rename_member_link(node_id: str, display_name: str) -> dict[str, Any]:
    return _service.rename_link("member", node_id, display_name)


def set_browser_lifetime(device_id: str, preset: str) -> dict[str, Any]:
    return _service.set_link_lifetime("browser", device_id, preset)


def set_member_lifetime(node_id: str, preset: str) -> dict[str, Any]:
    return _service.set_link_lifetime("member", node_id, preset)


def detach_browser_link(device_id: str) -> dict[str, Any]:
    return _service.detach_link("browser", device_id)


def detach_member_link(node_id: str) -> dict[str, Any]:
    return _service.detach_link("member", node_id)


def lifetime_label(entry: dict[str, Any]) -> str:
    return _service.lifetime_label(entry)

