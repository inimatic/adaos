from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
import json
import logging
import re
import time

from adaos.services.agent_context import get_ctx

_log = logging.getLogger("adaos.webui.contract")
_PARAM_TOKEN_RE = re.compile(r"\$(?:params|address\.params)\.([A-Za-z0-9_.-]+)")
_DYNAMIC_TOKEN_RE = re.compile(r"^\$")
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_LOG_DEDUP_TTL_S = 300.0
_LOG_DEDUP: dict[str, float] = {}
_DIAGNOSTIC_CATALOG: dict[str, dict[str, str]] = {
    "webui.interface.default_view_unknown": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Declare defaultView in interface.views or change defaultView.",
    },
    "webui.interface.transition_from_unknown": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Point transition.from to a declared interface view.",
    },
    "webui.interface.transition_to_unknown": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Point transition.to to a declared interface view.",
    },
    "webui.interface.view_ambiguous": {
        "severity": "error",
        "owner": "runtime",
        "remediation": "Namespace public view ids so each view belongs to one skill.",
    },
    "webui.modal.interface_missing": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Add schema.interface.routes for every implemented view.",
    },
    "webui.modal.default_route_unknown": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Declare defaultRoute in schema.interface.routes.",
    },
    "webui.modal.implements_unknown_view": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Remove the view from implements or declare it in webui.interface.views.",
    },
    "webui.modal.implemented_view_without_route": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Add a modal route whose view matches the implemented view.",
    },
    "webui.modal.route_unknown_view": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Point route.view to a declared public view.",
    },
    "webui.modal.route_not_implemented": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Add the route view to modal implements or remove the route.",
    },
    "webui.modal.route_missing_view_param": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Declare required view params on the matching modal route.",
    },
    "webui.modal.state_unknown_param": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Declare the route param or stop referencing it from route state.",
    },
    "webui.modal.domain.default_state_unknown": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Declare domain.defaultState in domain.states.",
    },
    "webui.modal.domain.state_route_unknown": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Point each domain state to an existing modal route.",
    },
    "webui.modal.domain.state_view_mismatch": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Keep domain state view equal to the target route view.",
    },
    "webui.modal.domain.entity_param_unknown": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Declare entity.idParam in the target modal route params.",
    },
    "webui.modal.domain.ownership_missing": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Add modal interface ownership for domainState, routeState, viewState, and persistence.",
    },
    "webui.modal.ownership_section_missing": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Fill the missing ownership section.",
    },
    "webui.modal.ownership_owner_missing": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Set an explicit owner in each ownership section.",
    },
    "webui.action.navigate_unknown_view": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Point navigate.to to a declared public view.",
    },
    "webui.action.navigate_missing_param": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Pass all required view params in navigate.params.params.",
    },
    "webui.action.navigate_surface_mismatch": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Use a surface declared by the target public view.",
    },
    "webui.action.navigate_modal_outside_modal": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Use navigateModal only inside a modal schema.",
    },
    "webui.action.navigate_modal_unknown_route": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Point navigateModal.params.route to a route declared by this modal.",
    },
    "webui.action.navigate_modal_missing_param": {
        "severity": "error",
        "owner": "skill",
        "remediation": "Pass all required route params in navigateModal.params.params.",
    },
}


def webui_contract_diagnostic_catalog() -> dict[str, dict[str, str]]:
    """Return the stable WebUI contract diagnostics catalog."""

    return {code: dict(meta) for code, meta in sorted(_DIAGNOSTIC_CATALOG.items())}


@dataclass(frozen=True)
class WebUiContractIssue:
    level: str
    code: str
    message: str
    where: str
    skill_id: str | None = None
    source: str | None = None
    modal_id: str | None = None
    view_id: str | None = None
    route_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "")}


def validate_webui_contract(
    webui: Mapping[str, Any] | None,
    *,
    skill_id: str | None = None,
    source: str = "webui.json",
) -> list[WebUiContractIssue]:
    """Validate WebUI domain view/modal-address cross-links for one skill manifest."""

    raw = _mapping(webui)
    if not raw:
        return []
    declared_skill = _clean_token(skill_id) or _clean_token(raw.get("skill")) or "unknown_skill"
    interface = _mapping(raw.get("interface") or raw.get("uiInterface"))
    interfaces = {declared_skill: interface} if interface else {}
    registry = _mapping(raw.get("registry"))
    modals = _mapping(registry.get("modals"))
    widgets = _list(raw.get("widgets") or _mapping(raw.get("catalog")).get("widgets"))
    return _validate_contract(
        interfaces=interfaces,
        modals=modals,
        widgets=widgets,
        source=source,
        default_skill=declared_skill,
    )


def validate_application_ui_contract(
    application: Mapping[str, Any] | None,
    *,
    source: str = "ui.application",
) -> list[WebUiContractIssue]:
    """Validate the already-materialized application contract across skills."""

    raw = _mapping(application)
    if not raw:
        return []
    interfaces = {
        _clean_token(key): value
        for key, value in _mapping(raw.get("interfaces")).items()
        if _clean_token(key) and isinstance(value, Mapping)
    }
    single = _mapping(raw.get("interface"))
    if single:
        skill = _origin_skill_from_interface(single) or "application"
        interfaces.setdefault(skill, single)
    return _validate_contract(
        interfaces=interfaces,
        modals=_mapping(raw.get("modals")),
        widgets=[],
        source=source,
        default_skill=None,
    )


def log_webui_contract_issues(
    issues: list[WebUiContractIssue],
    *,
    webspace_id: str | None = None,
    source: str = "webui_contract",
) -> None:
    """Write runtime-visible UI contract diagnostics into skill UI logs."""

    notable = [issue for issue in issues if issue.level in {"error", "warning"}]
    if not notable:
        return
    now = time.time()
    for issue in notable:
        signature = "|".join(
            [
                str(webspace_id or ""),
                str(issue.skill_id or ""),
                issue.code,
                issue.where,
                issue.message,
            ]
        )
        previous = _LOG_DEDUP.get(signature)
        if previous is not None and now - previous < _LOG_DEDUP_TTL_S:
            continue
        _LOG_DEDUP[signature] = now
        if len(_LOG_DEDUP) > 1024:
            cutoff = now - _LOG_DEDUP_TTL_S * 2
            for key, seen_at in list(_LOG_DEDUP.items()):
                if seen_at < cutoff:
                    _LOG_DEDUP.pop(key, None)
        record = {
            "v": 1,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "ts": now,
            "level": issue.level.upper(),
            "logger": "ui.contract",
            "msg": issue.message,
            "source": source,
            "code": issue.code,
            "skill_id": issue.skill_id or "__ui_runtime__",
            "webspace_id": webspace_id,
            "details": issue.to_dict(),
        }
        try:
            _append_skill_ui_record(issue.skill_id or "__ui_runtime__", record)
        except Exception:
            _log.debug("failed to append UI contract diagnostic", exc_info=True)
        log_method = _log.error if issue.level == "error" else _log.warning
        log_method("%s where=%s skill=%s", issue.message, issue.where, issue.skill_id or "")


def _validate_contract(
    *,
    interfaces: Mapping[str, Any],
    modals: Mapping[str, Any],
    widgets: list[Any],
    source: str,
    default_skill: str | None,
) -> list[WebUiContractIssue]:
    issues: list[WebUiContractIssue] = []
    view_index: dict[str, list[str]] = {}
    view_specs: dict[str, Mapping[str, Any]] = {}
    clean_interfaces: dict[str, Mapping[str, Any]] = {}

    for raw_skill, raw_interface in interfaces.items():
        skill = _clean_token(raw_skill) or default_skill or "unknown_skill"
        interface = _mapping(raw_interface)
        if not interface:
            continue
        clean_interfaces[skill] = interface
        views = _mapping(interface.get("views"))
        default_view = _clean_token(interface.get("defaultView"))
        if default_view and default_view not in views:
            issues.append(
                _issue(
                    "error",
                    "webui.interface.default_view_unknown",
                    f"Skill interface defaultView '{default_view}' is not declared in views.",
                    f"{source}:interface.defaultView",
                    skill_id=skill,
                    view_id=default_view,
                    source=source,
                )
            )
        for view_id, view in views.items():
            token = _clean_token(view_id)
            if not token:
                continue
            view_index.setdefault(token, []).append(skill)
            if isinstance(view, Mapping):
                view_specs[token] = view
        for index, transition in enumerate(_list(interface.get("transitions"))):
            if not isinstance(transition, Mapping):
                continue
            from_view = _clean_token(transition.get("from"))
            to_view = _clean_token(transition.get("to"))
            if from_view and from_view not in views:
                issues.append(
                    _issue(
                        "error",
                        "webui.interface.transition_from_unknown",
                        f"Transition from '{from_view}' references an unknown view.",
                        f"{source}:interface.transitions[{index}].from",
                        skill_id=skill,
                        view_id=from_view,
                        source=source,
                    )
                )
            if to_view and to_view not in views:
                issues.append(
                    _issue(
                        "error",
                        "webui.interface.transition_to_unknown",
                        f"Transition to '{to_view}' references an unknown view.",
                        f"{source}:interface.transitions[{index}].to",
                        skill_id=skill,
                        view_id=to_view,
                        source=source,
                    )
                )

    for view_id, owners in sorted(view_index.items()):
        unique = sorted(set(owners))
        if len(unique) > 1:
            issues.append(
                _issue(
                    "error",
                    "webui.interface.view_ambiguous",
                    f"UI view '{view_id}' is declared by multiple skills: {', '.join(unique)}.",
                    f"{source}:interfaces.*.views.{view_id}",
                    skill_id=",".join(unique),
                    view_id=view_id,
                    source=source,
                )
            )

    for index, widget in enumerate(widgets):
        if isinstance(widget, Mapping):
            issues.extend(
                _validate_action_tree(
                    widget,
                    view_index=view_index,
                    view_specs=view_specs,
                    source=source,
                    where=f"{source}:widgets[{index}]",
                    skill_id=default_skill,
                    modal_id=None,
                    routes={},
                )
            )

    for modal_id_raw, modal_raw in modals.items():
        modal_id = _clean_token(modal_id_raw)
        modal = _mapping(modal_raw)
        if not modal_id or not modal:
            continue
        modal_skill = _origin_skill_from_modal(modal) or default_skill or _infer_modal_skill(modal, view_index)
        modal_interface = _modal_interface(modal)
        routes = _mapping(modal_interface.get("routes"))
        implements = _string_list(modal.get("implements"))
        route_views = {
            _clean_token(route.get("view")): route_id
            for route_id, route in routes.items()
            if isinstance(route, Mapping) and _clean_token(route.get("view"))
        }
        if implements and not modal_interface:
            issues.append(
                _issue(
                    "error",
                    "webui.modal.interface_missing",
                    f"Modal '{modal_id}' implements domain views but has no modal interface routes.",
                    f"{source}:modals.{modal_id}",
                    skill_id=modal_skill,
                    modal_id=modal_id,
                    source=source,
                )
            )
        default_route = _clean_token(modal_interface.get("defaultRoute"))
        if default_route and default_route not in routes:
            issues.append(
                _issue(
                    "error",
                    "webui.modal.default_route_unknown",
                    f"Modal '{modal_id}' defaultRoute '{default_route}' is not declared in routes.",
                    f"{source}:modals.{modal_id}.interface.defaultRoute",
                    skill_id=modal_skill,
                    modal_id=modal_id,
                    route_id=default_route,
                    source=source,
                )
            )
        domain = _mapping(modal_interface.get("domain"))
        ownership = _mapping(modal_interface.get("ownership"))
        issues.extend(
            _validate_modal_domain(
                domain,
                ownership=ownership,
                routes=routes,
                source=source,
                where=f"{source}:modals.{modal_id}.interface.domain",
                skill_id=modal_skill,
                modal_id=modal_id,
            )
        )
        for view_id in implements:
            if view_id not in view_index:
                issues.append(
                    _issue(
                        "error",
                        "webui.modal.implements_unknown_view",
                        f"Modal '{modal_id}' implements unknown UI view '{view_id}'.",
                        f"{source}:modals.{modal_id}.implements",
                        skill_id=modal_skill,
                        modal_id=modal_id,
                        view_id=view_id,
                        source=source,
                    )
                )
            if view_id not in route_views:
                issues.append(
                    _issue(
                        "error",
                        "webui.modal.implemented_view_without_route",
                        f"Modal '{modal_id}' implements view '{view_id}' but no route maps to that view.",
                        f"{source}:modals.{modal_id}.interface.routes",
                        skill_id=modal_skill,
                        modal_id=modal_id,
                        view_id=view_id,
                        source=source,
                    )
                )
        for route_id_raw, route_raw in routes.items():
            route_id = _clean_token(route_id_raw)
            route = _mapping(route_raw)
            if not route_id or not route:
                continue
            view_id = _clean_token(route.get("view"))
            if view_id:
                if view_id not in view_index:
                    issues.append(
                        _issue(
                            "error",
                            "webui.modal.route_unknown_view",
                            f"Modal '{modal_id}' route '{route_id}' references unknown UI view '{view_id}'.",
                            f"{source}:modals.{modal_id}.interface.routes.{route_id}.view",
                            skill_id=modal_skill,
                            modal_id=modal_id,
                            view_id=view_id,
                            route_id=route_id,
                            source=source,
                        )
                    )
                if implements and view_id not in implements:
                    issues.append(
                        _issue(
                            "error",
                            "webui.modal.route_not_implemented",
                            f"Modal '{modal_id}' route '{route_id}' maps view '{view_id}' not listed in implements.",
                            f"{source}:modals.{modal_id}.interface.routes.{route_id}.view",
                            skill_id=modal_skill,
                            modal_id=modal_id,
                            view_id=view_id,
                            route_id=route_id,
                            source=source,
                        )
                    )
                issues.extend(
                    _validate_route_view_params(
                        route,
                        view_specs.get(view_id, {}),
                        source=source,
                        where=f"{source}:modals.{modal_id}.interface.routes.{route_id}",
                        skill_id=modal_skill,
                        modal_id=modal_id,
                        view_id=view_id,
                        route_id=route_id,
                    )
                )
            issues.extend(
                _validate_state_param_tokens(
                    route,
                    source=source,
                    where=f"{source}:modals.{modal_id}.interface.routes.{route_id}.state",
                    skill_id=modal_skill,
                    modal_id=modal_id,
                    view_id=view_id,
                    route_id=route_id,
                )
            )
        issues.extend(
            _validate_modal_ownership(
                ownership,
                domain_present=bool(domain),
                source=source,
                where=f"{source}:modals.{modal_id}.interface.ownership",
                skill_id=modal_skill,
                modal_id=modal_id,
            )
        )
        issues.extend(
            _validate_action_tree(
                modal.get("schema") or modal,
                view_index=view_index,
                view_specs=view_specs,
                source=source,
                where=f"{source}:modals.{modal_id}.schema",
                skill_id=modal_skill,
                modal_id=modal_id,
                routes=routes,
            )
        )

    return issues


def _validate_modal_domain(
    domain: Mapping[str, Any],
    *,
    ownership: Mapping[str, Any],
    routes: Mapping[str, Any],
    source: str,
    where: str,
    skill_id: str | None,
    modal_id: str,
) -> list[WebUiContractIssue]:
    issues: list[WebUiContractIssue] = []
    if not domain:
        return issues
    states = _mapping(domain.get("states"))
    default_state = _clean_token(domain.get("defaultState") or domain.get("default_state"))
    if default_state and default_state not in states:
        issues.append(
            _issue(
                "error",
                "webui.modal.domain.default_state_unknown",
                f"Modal '{modal_id}' domain defaultState '{default_state}' is not declared in states.",
                f"{where}.defaultState",
                skill_id=skill_id,
                modal_id=modal_id,
                source=source,
            )
        )
    if not ownership:
        issues.append(
            _issue(
                "error",
                "webui.modal.domain.ownership_missing",
                f"Modal '{modal_id}' declares domain state without ownership.",
                where,
                skill_id=skill_id,
                modal_id=modal_id,
                source=source,
            )
        )
    for state_id_raw, state_raw in states.items():
        state_id = _clean_token(state_id_raw)
        state = _mapping(state_raw)
        if not state_id or not state:
            continue
        route_id = _clean_token(state.get("route"))
        route = _mapping(routes.get(route_id)) if route_id else {}
        if not route:
            issues.append(
                _issue(
                    "error",
                    "webui.modal.domain.state_route_unknown",
                    f"Modal '{modal_id}' domain state '{state_id}' references unknown route '{route_id}'.",
                    f"{where}.states.{state_id}.route",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    route_id=route_id,
                    source=source,
                )
            )
            continue
        state_view = _clean_token(state.get("view"))
        route_view = _clean_token(route.get("view"))
        if state_view and route_view and state_view != route_view:
            issues.append(
                _issue(
                    "error",
                    "webui.modal.domain.state_view_mismatch",
                    f"Modal '{modal_id}' domain state '{state_id}' view '{state_view}' does not match route '{route_id}' view '{route_view}'.",
                    f"{where}.states.{state_id}.view",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    view_id=state_view,
                    route_id=route_id,
                    source=source,
                )
            )
        entity = _mapping(state.get("entity"))
        id_param = _clean_token(entity.get("idParam") or entity.get("id_param"))
        if id_param and id_param not in _mapping(route.get("params")):
            issues.append(
                _issue(
                    "error",
                    "webui.modal.domain.entity_param_unknown",
                    f"Modal '{modal_id}' domain state '{state_id}' entity idParam '{id_param}' is not declared by route '{route_id}'.",
                    f"{where}.states.{state_id}.entity.idParam",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    route_id=route_id,
                    source=source,
                )
            )
    return issues


def _validate_modal_ownership(
    ownership: Mapping[str, Any],
    *,
    domain_present: bool,
    source: str,
    where: str,
    skill_id: str | None,
    modal_id: str,
) -> list[WebUiContractIssue]:
    issues: list[WebUiContractIssue] = []
    if not ownership:
        return issues
    required_sections = ("domainState", "routeState", "viewState", "persistence")
    for section in required_sections:
        spec = _mapping(ownership.get(section))
        if not spec:
            if domain_present:
                issues.append(
                    _issue(
                        "error",
                        "webui.modal.ownership_section_missing",
                        f"Modal '{modal_id}' ownership is missing '{section}'.",
                        f"{where}.{section}",
                        skill_id=skill_id,
                        modal_id=modal_id,
                        source=source,
                    )
                )
            continue
        if not _clean_token(spec.get("owner")):
            issues.append(
                _issue(
                    "error",
                    "webui.modal.ownership_owner_missing",
                    f"Modal '{modal_id}' ownership section '{section}' has no owner.",
                    f"{where}.{section}.owner",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    source=source,
                )
            )
    return issues


def _validate_route_view_params(
    route: Mapping[str, Any],
    view: Mapping[str, Any],
    *,
    source: str,
    where: str,
    skill_id: str | None,
    modal_id: str,
    view_id: str,
    route_id: str,
) -> list[WebUiContractIssue]:
    issues: list[WebUiContractIssue] = []
    route_params = _mapping(route.get("params"))
    for name in _required_params(view):
        if name not in route_params:
            issues.append(
                _issue(
                    "error",
                    "webui.modal.route_missing_view_param",
                    f"Route '{route_id}' for view '{view_id}' does not declare required view param '{name}'.",
                    f"{where}.params.{name}",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    view_id=view_id,
                    route_id=route_id,
                    source=source,
                )
            )
    return issues


def _validate_state_param_tokens(
    route: Mapping[str, Any],
    *,
    source: str,
    where: str,
    skill_id: str | None,
    modal_id: str,
    view_id: str,
    route_id: str,
) -> list[WebUiContractIssue]:
    issues: list[WebUiContractIssue] = []
    route_params = set(_mapping(route.get("params")).keys())
    state = _mapping(route.get("state"))
    for token in _find_param_tokens(state):
        head = token.split(".", 1)[0]
        if head not in route_params:
            issues.append(
                _issue(
                    "error",
                    "webui.modal.state_unknown_param",
                    f"Route '{route_id}' state references unknown param '{token}'.",
                    where,
                    skill_id=skill_id,
                    modal_id=modal_id,
                    view_id=view_id,
                    route_id=route_id,
                    source=source,
                )
            )
    return issues


def _validate_action_tree(
    value: Any,
    *,
    view_index: Mapping[str, list[str]],
    view_specs: Mapping[str, Mapping[str, Any]],
    source: str,
    where: str,
    skill_id: str | None,
    modal_id: str | None,
    routes: Mapping[str, Any],
) -> list[WebUiContractIssue]:
    issues: list[WebUiContractIssue] = []
    if isinstance(value, Mapping):
        actions = value.get("actions")
        if isinstance(actions, list):
            for index, action in enumerate(actions):
                if isinstance(action, Mapping):
                    issues.extend(
                        _validate_action(
                            action,
                            view_index=view_index,
                            view_specs=view_specs,
                            source=source,
                            where=f"{where}.actions[{index}]",
                            skill_id=skill_id,
                            modal_id=modal_id,
                            routes=routes,
                        )
                    )
        for key, nested in value.items():
            if key == "actions":
                continue
            issues.extend(
                _validate_action_tree(
                    nested,
                    view_index=view_index,
                    view_specs=view_specs,
                    source=source,
                    where=f"{where}.{key}",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    routes=routes,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(
                _validate_action_tree(
                    item,
                    view_index=view_index,
                    view_specs=view_specs,
                    source=source,
                    where=f"{where}[{index}]",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    routes=routes,
                )
            )
    return issues


def _validate_action(
    action: Mapping[str, Any],
    *,
    view_index: Mapping[str, list[str]],
    view_specs: Mapping[str, Mapping[str, Any]],
    source: str,
    where: str,
    skill_id: str | None,
    modal_id: str | None,
    routes: Mapping[str, Any],
) -> list[WebUiContractIssue]:
    action_type = _clean_token(action.get("type"))
    params = _mapping(action.get("params"))
    issues: list[WebUiContractIssue] = []
    if action_type == "navigate":
        view_id = _clean_token(params.get("to") or params.get("view"))
        if not view_id or _is_dynamic(view_id):
            return issues
        if view_id not in view_index:
            issues.append(
                _issue(
                    "error",
                    "webui.action.navigate_unknown_view",
                    f"navigate action references unknown UI view '{view_id}'.",
                    where,
                    skill_id=skill_id,
                    view_id=view_id,
                    source=source,
                )
            )
            return issues
        provided = _mapping(params.get("params"))
        for name in _required_params(view_specs.get(view_id, {})):
            if name not in provided:
                issues.append(
                    _issue(
                        "error",
                        "webui.action.navigate_missing_param",
                        f"navigate action to '{view_id}' does not provide required param '{name}'.",
                        f"{where}.params.params.{name}",
                        skill_id=skill_id,
                        view_id=view_id,
                        source=source,
                    )
                )
        surface = _clean_token(params.get("surface")) or "modal"
        surfaces = _view_surfaces(view_specs.get(view_id, {}))
        if surfaces and surface not in surfaces:
            issues.append(
                _issue(
                    "error",
                    "webui.action.navigate_surface_mismatch",
                    f"navigate action uses surface '{surface}' but view '{view_id}' declares {sorted(surfaces)}.",
                    f"{where}.params.surface",
                    skill_id=skill_id,
                    view_id=view_id,
                    source=source,
                )
            )
    elif action_type == "navigateModal":
        if not modal_id:
            issues.append(
                _issue(
                    "error",
                    "webui.action.navigate_modal_outside_modal",
                    "navigateModal action is only valid inside a modal schema.",
                    where,
                    skill_id=skill_id,
                    source=source,
                )
            )
            return issues
        route_id = _clean_token(params.get("route"))
        if not route_id or _is_dynamic(route_id):
            return issues
        route = _mapping(routes.get(route_id))
        if not route:
            issues.append(
                _issue(
                    "error",
                    "webui.action.navigate_modal_unknown_route",
                    f"navigateModal action references unknown route '{route_id}' in modal '{modal_id}'.",
                    f"{where}.params.route",
                    skill_id=skill_id,
                    modal_id=modal_id,
                    route_id=route_id,
                    source=source,
                )
            )
            return issues
        provided = _mapping(params.get("params"))
        for name in _required_params(route):
            if name not in provided:
                issues.append(
                    _issue(
                        "error",
                        "webui.action.navigate_modal_missing_param",
                        f"navigateModal action to route '{route_id}' does not provide required param '{name}'.",
                        f"{where}.params.params.{name}",
                        skill_id=skill_id,
                        modal_id=modal_id,
                        route_id=route_id,
                        source=source,
                    )
                )
    return issues


def _issue(
    level: str,
    code: str,
    message: str,
    where: str,
    *,
    source: str,
    skill_id: str | None = None,
    modal_id: str | None = None,
    view_id: str | None = None,
    route_id: str | None = None,
) -> WebUiContractIssue:
    return WebUiContractIssue(
        level=level,
        code=code,
        message=message,
        where=where,
        skill_id=skill_id,
        source=source,
        modal_id=modal_id,
        view_id=view_id,
        route_id=route_id,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _clean_token(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = _clean_token(item)
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _is_dynamic(value: str) -> bool:
    return bool(_DYNAMIC_TOKEN_RE.search(str(value or "").strip()))


def _modal_interface(modal: Mapping[str, Any]) -> dict[str, Any]:
    direct = _mapping(modal.get("interface"))
    if direct:
        return direct
    schema = _mapping(modal.get("schema"))
    return _mapping(schema.get("interface"))


def _origin_skill_from_modal(modal: Mapping[str, Any]) -> str | None:
    for key in ("originSkill", "origin_skill", "skillId", "skill_id", "skill"):
        token = _clean_token(modal.get(key))
        if token:
            return token
    origin = modal.get("origin")
    if isinstance(origin, Mapping):
        for key in ("skill", "skillId", "skill_id"):
            token = _clean_token(origin.get(key))
            if token:
                return token
    else:
        origin_text = _clean_token(origin)
        if origin_text.startswith("skill:"):
            return origin_text.removeprefix("skill:").strip() or None
    meta = _mapping(modal.get("_adaos"))
    for key in ("originSkill", "skillId", "skill"):
        token = _clean_token(meta.get(key))
        if token:
            return token
    return None


def _origin_skill_from_interface(interface: Mapping[str, Any]) -> str | None:
    meta = _mapping(interface.get("_adaos"))
    for key in ("originSkill", "skillId", "skill"):
        token = _clean_token(meta.get(key))
        if token:
            return token
    return None


def _infer_modal_skill(modal: Mapping[str, Any], view_index: Mapping[str, list[str]]) -> str | None:
    owners: list[str] = []
    for view_id in _string_list(modal.get("implements")):
        owners.extend(view_index.get(view_id, []))
    unique = sorted(set(owners))
    return unique[0] if len(unique) == 1 else None


def _required_params(spec_owner: Mapping[str, Any]) -> list[str]:
    params = _mapping(spec_owner.get("params"))
    out: list[str] = []
    for name, spec in params.items():
        if isinstance(spec, Mapping) and spec.get("required") is True:
            out.append(str(name))
    return out


def _view_surfaces(view: Mapping[str, Any]) -> set[str]:
    surfaces: set[str] = set()
    raw_surface = view.get("surface")
    if isinstance(raw_surface, str):
        surfaces.add(raw_surface.strip())
    elif isinstance(raw_surface, list):
        surfaces.update(str(item or "").strip() for item in raw_surface)
    surfaces.update(str(item or "").strip() for item in _list(view.get("surfaces")))
    return {item for item in surfaces if item}


def _find_param_tokens(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(match.group(1) for match in _PARAM_TOKEN_RE.finditer(value))
    elif isinstance(value, Mapping):
        for nested in value.values():
            found.update(_find_param_tokens(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_find_param_tokens(nested))
    return found


def _append_skill_ui_record(skill_id: str, record: Mapping[str, Any]) -> None:
    path = _skill_ui_log_path(skill_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")


def _skill_ui_log_path(skill_id: str) -> Path:
    safe_skill = _SAFE_TOKEN_RE.sub("_", str(skill_id or "__ui_runtime__").strip()) or "__ui_runtime__"
    try:
        paths = get_ctx().paths
        fn = getattr(paths, "skill_ui_diagnostics_log_path", None)
        if callable(fn):
            return Path(fn(safe_skill))
        return Path(paths.logs_dir()) / f"service.{safe_skill}.ui_runtime.log"
    except Exception:
        return Path(".adaos/logs") / f"service.{safe_skill}.ui_runtime.log"
