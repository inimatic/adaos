from __future__ import annotations

from typing import Any, Optional

from adaos.sdk.core.decorators import tool

from .desktop import (
    desktop_get_installed,
    desktop_get_pinned_widgets,
    desktop_get_snapshot,
    desktop_set_installed,
    desktop_set_pinned_widgets,
    desktop_set_snapshot,
    desktop_toggle_app,
    desktop_toggle_install,
)


def _application_catalog_id(application_id: str) -> str:
    token = str(application_id or "").strip()
    if not token:
        return token
    if token.startswith("scenario:") or token.startswith("application:") or token.startswith("app:"):
        return token
    return f"scenario:{token}"


@tool(
    "web.application.toggle_install",
    summary="Add or remove an application/widget catalog item for a webspace.",
    stability="experimental",
    examples=["web.application.toggle_install('application', 'prompt_engineer_scenario')"],
)
def application_toggle_install(
    item_type: str,
    item_id: str,
    webspace_id: Optional[str] = None,
    *,
    live: bool = True,
) -> None:
    """
    Product-terminology alias over ``web.desktop.toggle_install``.

    ``application`` maps to the existing desktop ``app`` catalog type, and
    ``panel`` maps to the existing ``widget`` type. Storage and events keep
    using the current desktop fields.
    """
    normalized_type = str(item_type or "").strip().lower()
    if normalized_type in {"application", "app"}:
        desktop_toggle_install("app", _application_catalog_id(item_id), webspace_id, live=live)
        return
    if normalized_type in {"panel", "widget"}:
        desktop_toggle_install("widget", item_id, webspace_id, live=live)
        return
    desktop_toggle_install(item_type, item_id, webspace_id, live=live)


@tool(
    "web.application.toggle_application",
    summary="Add or remove an application in a webspace.",
    stability="experimental",
    examples=["web.application.toggle_application('prompt_engineer_scenario')"],
)
def application_toggle_application(
    application_id: str,
    webspace_id: Optional[str] = None,
    *,
    live: bool = True,
) -> None:
    desktop_toggle_app(_application_catalog_id(application_id), webspace_id, live=live)


@tool(
    "web.application.get_installed",
    summary="Return installed applications/widgets for a webspace.",
    stability="experimental",
    examples=["web.application.get_installed()", "web.application.get_installed('main')"],
)
def application_get_installed(webspace_id: Optional[str] = None) -> dict:
    payload = desktop_get_installed(webspace_id)
    return {
        **payload,
        "applications": list(payload.get("apps") or []),
    }


@tool(
    "web.application.get_snapshot",
    summary="Return materialized application surface state for a webspace.",
    stability="experimental",
    examples=["web.application.get_snapshot()", "web.application.get_snapshot('main')"],
)
def application_get_snapshot(webspace_id: Optional[str] = None) -> dict:
    payload = desktop_get_snapshot(webspace_id)
    installed = payload.get("installed") if isinstance(payload.get("installed"), dict) else {}
    payload["installedApplications"] = list(installed.get("apps") or [])
    payload["pinnedPanels"] = list(payload.get("pinnedWidgets") or [])
    return payload


@tool(
    "web.application.get_pinned_panels",
    summary="Return pinned panels for a webspace.",
    stability="experimental",
    examples=["web.application.get_pinned_panels()", "web.application.get_pinned_panels('main')"],
)
def application_get_pinned_panels(webspace_id: Optional[str] = None) -> list[dict[str, Any]]:
    return desktop_get_pinned_widgets(webspace_id)


@tool(
    "web.application.set_installed",
    summary="Replace installed applications/widgets for a webspace.",
    stability="experimental",
    examples=["web.application.set_installed(['prompt_engineer_scenario'], ['weather'])"],
)
def application_set_installed(
    application_ids: list[str],
    widget_ids: list[str],
    webspace_id: Optional[str] = None,
    *,
    live: bool = True,
) -> None:
    desktop_set_installed(
        [_application_catalog_id(item) for item in list(application_ids or [])],
        list(widget_ids or []),
        webspace_id,
        live=live,
    )


@tool(
    "web.application.set_pinned_panels",
    summary="Replace pinned panels for a webspace.",
    stability="experimental",
    examples=["web.application.set_pinned_panels([{'id': 'infra-status', 'type': 'visual.metricTile'}])"],
)
def application_set_pinned_panels(
    pinned_panels: list[dict[str, Any]],
    webspace_id: Optional[str] = None,
    *,
    live: bool = True,
) -> None:
    desktop_set_pinned_widgets(list(pinned_panels or []), webspace_id, live=live)


@tool(
    "web.application.set_snapshot",
    summary="Replace materialized application surface state for a webspace.",
    stability="experimental",
    examples=["web.application.set_snapshot({'installedApplications': [], 'pinnedPanels': []})"],
)
def application_set_snapshot(
    snapshot: dict[str, Any],
    webspace_id: Optional[str] = None,
    *,
    live: bool = True,
) -> None:
    payload = dict(snapshot or {})
    if "installedApplications" in payload:
        installed = dict(payload.get("installed") or {})
        installed["apps"] = [_application_catalog_id(item) for item in list(payload.get("installedApplications") or [])]
        payload["installed"] = installed
    if "pinnedPanels" in payload:
        payload["pinnedWidgets"] = list(payload.get("pinnedPanels") or [])
    desktop_set_snapshot(payload, webspace_id, live=live)


__all__ = [
    "application_get_installed",
    "application_get_pinned_panels",
    "application_get_snapshot",
    "application_set_installed",
    "application_set_pinned_panels",
    "application_set_snapshot",
    "application_toggle_application",
    "application_toggle_install",
]
