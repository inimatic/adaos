from __future__ import annotations

from typing import Any, Mapping

from adaos.services.webui_contract import (
    validate_webui_contract,
    webui_contract_diagnostic_catalog,
)


def param_schema(
    type: str = "string",
    *,
    required: bool = False,
    default: Any = None,
    enum: list[Any] | tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": type}
    if required:
        spec["required"] = True
    if default is not None:
        spec["default"] = default
    if enum is not None:
        spec["enum"] = list(enum)
    return spec


def skill_view(
    title: str | None = None,
    *,
    surfaces: list[str] | tuple[str, ...] | None = None,
    params: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _drop_none(
        {
            "title": title,
            "surfaces": list(surfaces) if surfaces is not None else None,
            "params": dict(params) if params is not None else None,
            "data": dict(data) if data is not None else None,
        }
    )


def skill_interface(
    default_view: str,
    views: Mapping[str, Any],
    *,
    transitions: list[Mapping[str, Any]] | None = None,
    ownership: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _drop_none(
        {
            "schema": "adaos.ui.skill_interface.v1",
            "defaultView": default_view,
            "views": {key: dict(value) for key, value in views.items()},
            "transitions": [dict(item) for item in transitions] if transitions is not None else None,
            "ownership": dict(ownership) if ownership is not None else None,
        }
    )


def modal_route(
    view: str,
    *,
    params: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    title: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _drop_none(
        {
            "view": view,
            "title": title,
            "params": dict(params) if params is not None else None,
            "state": dict(state) if state is not None else None,
            "data": dict(data) if data is not None else None,
        }
    )


def modal_domain_state(
    route: str,
    *,
    view: str | None = None,
    kind: str = "custom",
    entity_type: str | None = None,
    entity_id_param: str | None = None,
    entity_id_state_key: str | None = None,
    draft: bool = False,
    state: Mapping[str, Any] | None = None,
    persistence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entity = _drop_none(
        {
            "type": entity_type,
            "idParam": entity_id_param,
            "idStateKey": entity_id_state_key,
            "draft": True if draft else None,
        }
    )
    return _drop_none(
        {
            "kind": kind,
            "route": route,
            "view": view,
            "entity": entity or None,
            "state": dict(state) if state is not None else None,
            "persistence": dict(persistence) if persistence is not None else None,
        }
    )


def modal_domain_contract(
    default_state: str,
    states: Mapping[str, Any],
    *,
    state_key: str | None = None,
) -> dict[str, Any]:
    return _drop_none(
        {
            "schema": "adaos.ui.modal_domain.v1",
            "defaultState": default_state,
            "stateKey": state_key,
            "states": {key: dict(value) for key, value in states.items()},
        }
    )


def modal_ownership_contract(
    skill: str,
    *,
    domain_store: str | None = None,
    projection: str | None = None,
    route_keys: list[str] | tuple[str, ...] | None = None,
    persistence_ack: str | None = None,
    durability: str | None = None,
) -> dict[str, Any]:
    skill_owner = f"skill:{skill}" if not skill.startswith("skill:") else skill
    return {
        "schema": "adaos.ui.state_ownership.v1",
        "domainState": _drop_none(
            {
                "owner": skill_owner,
                "store": domain_store,
                "projection": projection,
            }
        ),
        "routeState": _drop_none(
            {
                "owner": "browser",
                "scope": "modal",
                "keys": list(route_keys) if route_keys is not None else None,
            }
        ),
        "viewState": {
            "owner": "browser",
            "scope": "modal",
        },
        "persistence": _drop_none(
            {
                "owner": skill_owner,
                "ack": persistence_ack,
                "durability": durability,
            }
        ),
    }


def modal_interface(
    default_route: str,
    routes: Mapping[str, Any],
    *,
    domain: Mapping[str, Any] | None = None,
    ownership: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _drop_none(
        {
            "schema": "adaos.ui.modal_interface.v1",
            "defaultRoute": default_route,
            "domain": dict(domain) if domain is not None else None,
            "ownership": dict(ownership) if ownership is not None else None,
            "routes": {key: dict(value) for key, value in routes.items()},
        }
    )


def navigate_action(
    view: str,
    *,
    params: Mapping[str, Any] | None = None,
    surface: str = "modal",
    on: str | None = None,
    modal_id: str | None = None,
    route: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "navigate",
        "on": on or "click",
        "params": _drop_none(
            {
                "to": view,
                "surface": surface,
                "modalId": modal_id,
                "route": route,
                "params": dict(params) if params is not None else None,
            }
        ),
    }


def navigate_modal_action(
    route: str,
    *,
    params: Mapping[str, Any] | None = None,
    on: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "navigateModal",
        "on": on or "click",
        "params": _drop_none(
            {
                "route": route,
                "params": dict(params) if params is not None else None,
            }
        ),
    }


def validate_webui(webui: Mapping[str, Any], *, skill_id: str | None = None) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in validate_webui_contract(webui, skill_id=skill_id)]


def diagnostic_catalog() -> dict[str, dict[str, str]]:
    return webui_contract_diagnostic_catalog()


def _drop_none(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}
