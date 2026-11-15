"""Prepare a constrained projection of existing WebUI; never dispatch effects.

Action descriptors stay in the node-owned preparation. Receivers see opaque
action refs. Event resolution returns the original frozen descriptor for the
normal admitted action pipeline; it is neither execution nor an access grant.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

ABI = Path(__file__).resolve().parents[1] / "abi"
PROFILE_ID = "adaos.ui.endpoint.controls"
PROJECTOR_VERSION = "0.1.0"
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PATH = r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
_STATE = re.compile(r"^\$state\.(" + _PATH + r")$")
_VISIBLE = re.compile(r"^\$state\.(" + _PATH + r")\s*(===|!==|==|!=)\s*(.+)$")
_CLIENT_LIMITS = {"max_widgets", "max_buttons_per_widget", "max_label_chars", "max_payload_bytes"}


class _Gap(Exception):
    def __init__(self, code: str, path: str, detail: str):
        self.finding = {"code": "endpoint_ui." + code, "path": path, "detail": detail}


def _require(condition: bool, code: str, path: str, detail: str) -> None:
    if not condition:
        raise _Gap(code, path, detail)


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_bytes(value)).hexdigest()


@lru_cache(maxsize=8)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((ABI / name).read_text(encoding="utf-8")))


def _shape(value: Any, name: str, path: str, code: str) -> None:
    _require(_validator(name).is_valid(value), code, path, "Document does not satisfy its versioned JSON contract.")


def _json(value: Any, *, path: str, max_bytes: int, max_depth: int = 24) -> None:
    visited = 0
    def visit(item: Any, depth: int) -> None:
        nonlocal visited
        visited += 1
        _require(visited <= max_bytes, "limit_exceeded", path, "JSON exceeds the profile traversal budget.")
        _require(depth <= max_depth, "limit_exceeded", path, "JSON nesting exceeds the profile limit.")
        if isinstance(item, dict):
            _require(all(isinstance(k, str) for k in item), "invalid_json", path, "JSON object keys must be strings.")
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        else:
            _require(item is None or type(item) in (str, int, float, bool), "invalid_json", path, "Expected a JSON value.")
            _require(not isinstance(item, str) or len(item) <= max_bytes, "limit_exceeded", path, "String exceeds the profile byte budget.")
            _require(not isinstance(item, float) or math.isfinite(item), "invalid_json", path, "Non-finite numbers are unsupported.")
            _require(type(item) is not int or abs(item) <= 9007199254740991, "invalid_json", path, "Integers must remain exact in the browser JSON number range.")
    visit(value, 0)
    try:
        encoded = _bytes(value)
    except UnicodeError:
        raise _Gap("invalid_json", path, "JSON strings must have valid UTF-8 representations.") from None
    _require(len(encoded) <= max_bytes, "limit_exceeded", path, "JSON exceeds the profile byte budget.")


@lru_cache(maxsize=1)
def _definition() -> dict[str, Any]:
    profile = json.loads((ABI / "ui.endpoint_controls.profile.v1.json").read_text(encoding="utf-8"))
    schema = json.loads((ABI / "ui.endpoint_profile.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(profile)
    return profile


def endpoint_profile() -> dict[str, Any]:
    """Return the exact experimental definition and its content identity."""
    profile = copy.deepcopy(_definition())
    return {"definition": profile, "identity": {"profile_id": profile["profile_id"], "version": profile["version"], "digest": _digest(profile)}}


def _fields(value: Any, allowed: list[str] | set[str], path: str) -> dict[str, Any]:
    _require(isinstance(value, dict), "invalid_shape", path, "Expected an object.")
    _require(not set(value) - set(allowed), "unsupported_field", path, "This object contains fields outside the admitted profile.")
    return value


def _id(value: Any, path: str) -> str:
    _require(isinstance(value, str) and _ID.fullmatch(value) is not None, "invalid_identity", path, "Expected a bounded stable identifier.")
    return value


def _read(state: Mapping[str, Any], path: str, location: str) -> Any:
    # Match browser readByPath, including flattened dotted keys.
    if path in state:
        return state[path]
    parts = path.split(".")
    for index in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:index])
        if prefix in state:
            _require(isinstance(state[prefix], dict), "binding_missing", location, "A required state path is unavailable.")
            return _read(state[prefix], ".".join(parts[index:]), location)
    first = parts[0]
    _require(first in state, "binding_missing", location, "A required state path is unavailable.")
    if len(parts) == 1:
        return state[first]
    _require(isinstance(state[first], dict), "binding_missing", location, "A required state path is unavailable.")
    return _read(state[first], ".".join(parts[1:]), location)


def _bind(value: Any, state: dict[str, Any], path: str) -> Any:
    if isinstance(value, dict):
        _require(value.get("kind") != "expression", "unsupported_binding", path, "Computed expressions are outside this profile.")
        return {key: _bind(item, state, path + "." + key) for key, item in value.items()}
    if isinstance(value, list):
        return [_bind(item, state, path + "[]") for item in value]
    if isinstance(value, str):
        match = _STATE.fullmatch(value)
        if match:
            return copy.deepcopy(_read(state, match[1], path))
        _require(not re.search(r"\$(?:state|event|client|data)\b", value), "unsupported_binding", path, "Only exact $state paths are supported.")
    return value


def _visible(value: Any, state: dict[str, Any], path: str) -> bool:
    if value is None or value == "":
        return True
    _require(isinstance(value, str), "unsupported_visibility", path, "Expected a supported WebUI state condition.")
    if value in ("true", "false", "null"):
        return value == "true"
    match = _VISIBLE.fullmatch(value)
    _require(match is not None, "unsupported_visibility", path, "Only state-to-literal equality is supported.")
    token = match[3].strip()
    if re.fullmatch(r"(?:'[^'\\]*'|\"[^\"\\]*\")", token):
        expected: Any = token[1:-1]
    elif token in ("true", "false", "null"):
        expected = {"true": True, "false": False, "null": None}[token]
    elif re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        expected = float(token)
    else:
        raise _Gap("unsupported_visibility", path, "Expected a plain decimal/boolean/null literal or an unescaped quoted string.")
    _require(not isinstance(expected, float) or math.isfinite(expected), "unsupported_visibility", path, "Comparison literals must be finite.")
    actual = _read(state, match[1], path)
    numbers = type(actual) in (int, float) and type(expected) in (int, float)
    equal = (numbers or type(actual) is type(expected)) and actual == expected
    return equal if match[2] in ("==", "===") else not equal


def _label(value: Any, reference: Any, translations: dict[str, Any], limits: dict[str, int], path: str) -> str:
    if isinstance(reference, dict):
        _fields(reference, {"key", "fallback"}, path + ".i18n")
        value = reference.get("fallback", value)
        reference = reference.get("key")
    _require(reference is None or isinstance(reference, str), "unsupported_binding", path, "Expected a translation key or key/fallback descriptor.")
    if isinstance(reference, str) and reference in translations:
        value = translations[reference]
    _require(isinstance(value, str) and bool(value.strip()), "label_required", path, "A text label or resolved translation is required.")
    _require(len(value) <= limits["max_label_chars"], "limit_exceeded", path, "Label exceeds the profile limit.")
    return value


def _implementation(report: Any, identity: dict[str, Any], limits: dict[str, int]) -> tuple[str, dict[str, int]]:
    _shape(report, "ui.endpoint_implementation.v1.schema.json", "implementation", "implementation_invalid")
    row = _fields(report, {"schema", "endpoint_id", "renderer_build", "profiles", "limits"}, "implementation")
    _require(row.get("schema") == "adaos.ui.endpoint_implementation.v1", "implementation_invalid", "implementation.schema", "Unsupported implementation report schema.")
    endpoint = _id(row.get("endpoint_id"), "implementation.endpoint_id")
    _id(row.get("renderer_build"), "implementation.renderer_build")
    profiles = row.get("profiles")
    _require(isinstance(profiles, list) and 0 < len(profiles) <= 16, "implementation_invalid", "implementation.profiles", "Expected bounded exact profile identities.")
    for candidate in profiles:
        _fields(candidate, {"profile_id", "version", "digest"}, "implementation.profiles[]")
    _require(identity in profiles, "profile_incompatible", "implementation.profiles", "The endpoint has not declared this exact profile version and digest.")
    narrowed = dict(limits)
    for key, value in _fields(row.get("limits", {}), _CLIENT_LIMITS, "implementation.limits").items():
        _require(type(value) is int and value > 0, "implementation_invalid", "implementation.limits." + key, "Expected a positive integer budget.")
        narrowed[key] = min(narrowed[key], value)
    return endpoint, narrowed


def _widget(widget: dict[str, Any], state: dict[str, Any], translations: dict[str, Any], profile: dict[str, Any], limits: dict[str, int]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    path = "widgets." + str(widget.get("id", "unknown"))
    component = profile["components"].get(widget.get("type"))
    _require(component is not None, "component_unsupported", path, "The selected widget type is outside this profile.")
    _fields(widget, component["widget_fields"], path)
    identity = _id(widget.get("id"), path + ".id")
    inputs = _fields(widget.get("inputs"), component["input_fields"], path + ".inputs")
    _require(inputs.get("variant", "toolbar") in ("toolbar", "row", "stack"), "unsupported_layout", path, "Unsupported action layout.")
    _require(inputs.get("size", "medium") in ("small", "medium", "large"), "unsupported_layout", path, "Unsupported action size.")
    _require(type(inputs.get("stretch", False)) is bool, "invalid_shape", path, "Stretch must be boolean.")
    buttons, actions = inputs.get("buttons"), widget.get("actions")
    _require(isinstance(buttons, list) and 0 < len(buttons) <= limits["max_buttons_per_widget"], "limit_exceeded", path, "Expected a bounded nonempty button list.")
    _require(isinstance(actions, list), "action_missing", path, "Actions must be declared explicitly.")
    by_event: dict[str, dict[str, Any]] = {}
    for action in actions:
        _fields(action, component["action_fields"], path + ".actions")
        _require(action.get("type") in component["action_types"], "action_unsupported", path, "Unsupported action type.")
        event = action.get("on")
        _require(isinstance(event, str) and event.startswith("click:") and event not in by_event, "action_invalid", path, "Each click event needs exactly one action.")
        _require(isinstance(action.get("target"), str) and re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_]+", action["target"]) is not None, "action_invalid", path, "The action needs a literal skill method reference.")
        _require(isinstance(action.get("params", {}), dict), "action_invalid", path, "Action params must be an object.")
        _require("timeoutMs" not in action or type(action["timeoutMs"]) is int and action["timeoutMs"] > 0, "action_invalid", path, "Timeout must be a positive integer.")
        _require("invalidates" not in action or isinstance(action["invalidates"], list) and all(isinstance(t, str) for t in action["invalidates"]), "action_invalid", path, "Invalidation tags must be strings.")
        resolved = copy.deepcopy(action)
        resolved["params"] = _bind(action.get("params", {}), state, path + ".actions.params")
        _json(resolved, path=path + ".actions", max_bytes=limits["max_action_bytes"], max_depth=limits["max_depth"])
        by_event[event] = resolved
    compiled, bindings, ids = [], {}, set()
    visible = _visible(widget.get("visibleIf"), state, path + ".visibleIf")
    for button in buttons:
        _fields(button, component["button_fields"], path + ".buttons")
        button_id = _id(button.get("id"), path + ".buttons.id")
        _require(button_id not in ids, "action_invalid", path, "Button identities must be unique.")
        ids.add(button_id)
        event = "click:" + button_id
        _require(event in by_event, "action_missing", path, "Each button needs its original action binding.")
        disabled = _bind(button.get("disabled", False), state, path + ".buttons.disabled")
        _require(type(disabled) is bool, "invalid_shape", path, "Disabled must resolve to boolean.")
        label = _label(button.get("label"), button.get("label_i18n"), translations, limits, path + ".buttons.label")
        _require(button.get("kind", "default") in ("default", "primary", "secondary", "danger"), "unsupported_field", path, "Unsupported button kind.")
        _require("icon" not in button or isinstance(button["icon"], str) and len(button["icon"]) <= 128, "invalid_shape", path, "Icon must be a bounded decorative name.")
        if not _visible(button.get("visibleIf"), state, path + ".buttons.visibleIf") or not visible:
            continue
        ref = "action:" + hashlib.sha256(_bytes([identity, button_id])).hexdigest()
        compiled.append({"id": button_id, "label": label, "disabled": disabled, "action_ref": ref,
                         "icon": button.get("icon", ""), "kind": button.get("kind", "default")})
        if not disabled:
            bindings[ref] = {"widget_id": identity, "event": event, "action": by_event[event]}
    _require(set(by_event) == {"click:" + identity for identity in ids}, "action_invalid", path, "Action events must match the declared buttons.")
    if not visible:
        return None, {}
    title = _label(widget.get("title", identity), widget.get("title_i18n"), translations, limits, path + ".title")
    return {"id": identity, "type": widget["type"], "title": title, "inputs": {"variant": inputs.get("variant", "toolbar"), "size": inputs.get("size", "medium"), "stretch": inputs.get("stretch", False), "buttons": compiled}}, bindings


def project_endpoint_ui(webui: dict[str, Any], *, widget_ids: list[str], state: dict[str, Any], implementation: dict[str, Any], surface_id: str, state_revision: str, generation: int, expires_at: float, translations: dict[str, str] | None = None, now: float | None = None) -> dict[str, Any]:
    """Prepare selected existing WebUI widgets; no network, effects or permissions.

    `implementation` is a declared report, not proof of hardware conformance.
    Store the returned node_bindings on the node; only projection goes on wire.
    """
    try:
        loaded = endpoint_profile()
        profile, identity = loaded["definition"], loaded["identity"]
        base_limits = profile["limits"]
        _json(webui, path="source", max_bytes=base_limits["max_source_bytes"], max_depth=base_limits["max_depth"])
        _json(state, path="state", max_bytes=base_limits["max_state_bytes"], max_depth=base_limits["max_depth"])
        _json(implementation, path="implementation", max_bytes=16384)
        _json(translations if translations is not None else {}, path="translations", max_bytes=65536)
        _require(translations is None or isinstance(translations, dict) and all(isinstance(v, str) for v in translations.values()), "invalid_shape", "translations", "Translations must be a flat string dictionary.")
        _require(isinstance(state, dict), "invalid_shape", "state", "Expected a state snapshot object.")
        endpoint, limits = _implementation(implementation, identity, base_limits)
        _id(surface_id, "surface_id")
        _id(state_revision, "state_revision")
        _require(type(generation) is int and generation > 0, "invalid_identity", "generation", "Generation must be a positive integer.")
        clock = time.time() if now is None else now
        _require(type(clock) in (int, float) and math.isfinite(clock), "deadline_invalid", "now", "Expected a finite server clock.")
        _require(type(expires_at) in (int, float) and math.isfinite(expires_at) and clock < expires_at <= clock + limits["max_ttl_seconds"], "deadline_invalid", "expires_at", "Expected a future deadline within the profile lifetime.")
        _require(isinstance(widget_ids, list) and 0 < len(widget_ids) <= limits["max_widgets"] and all(isinstance(w, str) and _ID.fullmatch(w) for w in widget_ids) and len(set(widget_ids)) == len(widget_ids), "selection_invalid", "widget_ids", "Select a bounded unique list of source widget IDs.")
        _require(isinstance(webui, dict) and webui.get("schema") == profile["source_abi"], "source_abi_unsupported", "source.schema", "Expected the existing WebUI contribution ABI.")
        page = webui
        for part in ("ui", "application", "desktop", "pageSchema"):
            _require(isinstance(page, dict), "source_invalid", "source." + part, "Expected an object in the source page address.")
            page = page.get(part)
        _require(isinstance(page, dict) and isinstance(page.get("widgets"), list), "source_invalid", "source.ui", "Expected the declared desktop page schema.")
        _id(page.get("id"), "source.page_id")
        widgets = page["widgets"]
        _require(all(isinstance(w, dict) and isinstance(w.get("id"), str) for w in widgets), "source_invalid", "source.widgets", "Source widgets require stable identities.")
        indexed = {w["id"]: w for w in widgets}
        _require(len(indexed) == len(widgets), "source_invalid", "source.widgets", "Duplicate widget identities are ambiguous.")
        common_schema = json.loads((ABI / "webui.v1.schema.json").read_text(encoding="utf-8"))
        common = Draft202012Validator({"$ref": "#/$defs/widgetConfig", "$defs": common_schema["$defs"]})
        output, bindings = [], {}
        for widget_id in widget_ids:
            _require(widget_id in indexed, "widget_missing", "widget_ids", "A requested widget is absent from the source.")
            widget = indexed[widget_id]
            _require(common.is_valid(widget), "source_invalid", "widgets." + widget_id, "Widget does not satisfy the existing WebUI contract.")
            result, action_bindings = _widget(widget, state, translations or {}, profile, limits)
            if result is not None:
                output.append(result)
                bindings.update(action_bindings)
        source = {"schema": webui["schema"], "digest": _digest(webui), "page_id": page.get("id", ""), "widget_ids": widget_ids}
        projection = {"schema": profile["projection_abi"], "profile": identity, "source": source,
                      "scope": {"endpoint_id": endpoint, "surface_id": surface_id, "state_revision": state_revision, "generation": generation, "expires_at": expires_at},
                      "layout": profile["layout"], "widgets": output}
        _json(projection, path="projection", max_bytes=limits["max_payload_bytes"], max_depth=limits["max_depth"])
        _shape(projection, "ui.endpoint_projection.v1.schema.json", "projection", "projection_invalid")
        receipt = {"schema": "adaos.ui.endpoint_projection_receipt.v1", "projector_version": PROJECTOR_VERSION,
                   "projection_digest": _digest(projection), "bindings_digest": _digest(bindings), "state_digest": _digest(state),
                   "source_contract_digest": _digest(common_schema),
                   "renderer_build": implementation["renderer_build"], "compatibility": "declared_profile_match", "hardware_qualified": False}
        return {"ok": True, "projection": projection, "node_bindings": bindings, "receipt": receipt, "findings": [], "authority": "preparation_only"}
    except _Gap as gap:
        return {"ok": False, "error": "endpoint_ui_incompatible", "findings": [gap.finding]}


def endpoint_ui_delivery(preparation: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Export receiver data and its opaque node-issued projection echo token.

    The caller still needs an admitted transport. Hashes provide no authority;
    receivers echo this token rather than re-canonicalizing JSON across languages.
    """
    try:
        _json(preparation, path="preparation", max_bytes=262144)
        _require(isinstance(preparation, dict) and preparation.get("ok") is True,
                 "preparation_invalid", "preparation", "A successful node-owned preparation is required.")
        projection, receipt = preparation.get("projection"), preparation.get("receipt")
        _require(isinstance(receipt, dict) and isinstance(preparation.get("node_bindings"), dict),
                 "preparation_invalid", "preparation", "Keep the original node-owned preparation.")
        _shape(projection, "ui.endpoint_projection.v1.schema.json", "projection", "preparation_invalid")
        _require(_digest(projection) == receipt.get("projection_digest") and
                 _digest(preparation["node_bindings"]) == receipt.get("bindings_digest"),
                 "preparation_invalid", "preparation", "Preparation no longer matches its receipt.")
        _require(projection["profile"] == endpoint_profile()["identity"], "profile_incompatible",
                 "projection.profile", "Export requires the exact supported profile.")
        _json(projection, path="projection", max_bytes=_definition()["limits"]["max_payload_bytes"])
        clock = time.time() if now is None else now
        _require(type(clock) in (int, float) and math.isfinite(clock) and
                 clock < projection["scope"]["expires_at"] <= clock + _definition()["limits"]["max_ttl_seconds"],
                 "expired", "projection.scope.expires_at", "Export requires a current bounded preparation.")
        delivery = {"schema": "adaos.ui.endpoint_delivery.v1",
                    "projection_digest": receipt["projection_digest"], "projection": copy.deepcopy(projection)}
        _shape(delivery, "ui.endpoint_delivery.v1.schema.json", "delivery", "preparation_invalid")
        return {"ok": True, "delivery": delivery, "authority": "requires_admitted_transport"}
    except _Gap as gap:
        return {"ok": False, "error": "endpoint_ui_delivery_refused", "findings": [gap.finding]}


def resolve_endpoint_ui_action(preparation: dict[str, Any], event: dict[str, Any], *, authenticated_endpoint_id: str, current_state_revision: str, current_generation: int, now: float | None = None) -> dict[str, Any]:
    """Resolve a normalized event against a NODE-OWNED preparation.

    Authenticated identity/current revision come from the admitted caller context,
    never from endpoint self-assertion. The returned descriptor still requires
    ordinary action policy, idempotency and dispatch; this helper executes nothing.
    """
    try:
        _json(event, path="event", max_bytes=4096)
        _shape(event, "ui.endpoint_action.v1.schema.json", "event", "event_invalid")
        _fields(event, {"schema", "event_id", "endpoint_id", "surface_id", "state_revision", "generation", "projection_digest", "action_ref"}, "event")
        _require(event.get("schema") == "adaos.ui.endpoint_action.v1", "event_invalid", "event.schema", "Unsupported action event schema.")
        _id(event.get("event_id"), "event.event_id")
        _require(isinstance(preparation, dict) and preparation.get("ok") is True, "preparation_invalid", "preparation", "A successful node-owned preparation is required.")
        _json(preparation, path="preparation", max_bytes=262144)
        _require(all(isinstance(preparation.get(key), dict) for key in ("projection", "receipt", "node_bindings")), "preparation_invalid", "preparation", "Projection, bindings and receipt must remain node-owned objects.")
        projection, receipt = preparation["projection"], preparation["receipt"]
        _shape(projection, "ui.endpoint_projection.v1.schema.json", "preparation", "preparation_invalid")
        _require(_digest(projection) == receipt.get("projection_digest") and _digest(preparation["node_bindings"]) == receipt.get("bindings_digest"), "preparation_invalid", "preparation", "Preparation no longer matches its receipt.")
        scope = projection["scope"]
        _require(type(current_generation) is int and current_generation > 0, "stale_event", "current_generation", "Expected the authoritative integer generation.")
        _require(authenticated_endpoint_id == scope["endpoint_id"] == event.get("endpoint_id"), "endpoint_mismatch", "event.endpoint_id", "The event belongs to another endpoint.")
        _require(scope["surface_id"] == event.get("surface_id") and receipt["projection_digest"] == event.get("projection_digest"), "surface_mismatch", "event.surface_id", "The event does not address the current projection.")
        _require(current_state_revision == scope["state_revision"] == event.get("state_revision") and type(event.get("generation")) is int and current_generation == scope["generation"] == event["generation"], "stale_event", "event.generation", "The source state or surface generation changed.")
        _require((time.time() if now is None else now) < scope["expires_at"], "expired", "event", "The prepared surface has expired.")
        binding = preparation["node_bindings"].get(event.get("action_ref"))
        _require(binding is not None, "action_unavailable", "event.action_ref", "The action is hidden, disabled or undeclared.")
        event_key = "endpoint-ui:" + hashlib.sha256(_bytes([scope["endpoint_id"], scope["surface_id"], scope["generation"], event["event_id"]])).hexdigest()
        return {"ok": True, "action": copy.deepcopy(binding["action"]), "widget_id": binding["widget_id"], "event": binding["event"],
                "event_id": event["event_id"], "event_key": event_key, "authority": "requires_admitted_action_dispatch"}
    except _Gap as gap:
        return {"ok": False, "error": "endpoint_ui_event_refused", "findings": [gap.finding]}
