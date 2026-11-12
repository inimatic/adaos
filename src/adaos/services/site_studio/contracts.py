from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


ABI_ROOT = Path(__file__).resolve().parents[2] / "abi"
AUTHORING_CONTEXT_LIMIT = 96 * 1024


class SiteStudioContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    payload = (
        value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def validate_contract(schema_name: str, payload: Mapping[str, Any]) -> None:
    schema_path = ABI_ROOT / schema_name
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            payload
        ),
        key=lambda error: tuple(str(part) for part in error.path),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.path) or "$"
        raise SiteStudioContractError(f"{schema_name}:{path}: {error.message}")
    if schema_name == "site_studio_authoring_context.v1.schema.json":
        size = len(canonical_json(payload).encode("utf-8"))
        if size > AUTHORING_CONTEXT_LIMIT:
            raise SiteStudioContractError(
                f"authoring context is {size} bytes; limit is {AUTHORING_CONTEXT_LIMIT}"
            )


def apply_change_set(
    source: Mapping[str, Any], change_set: Mapping[str, Any]
) -> dict[str, Any]:
    validate_contract("site_change_set.v1.schema.json", change_set)
    if digest(source) != change_set["base_source_digest"]:
        raise SiteStudioContractError("change set base source digest mismatch")
    changed = deepcopy(dict(source))
    seen: set[str] = set()
    for operation in change_set["operations"]:
        operation_id = operation["operation_id"]
        if operation_id in seen:
            raise SiteStudioContractError(f"duplicate operation id: {operation_id}")
        seen.add(operation_id)
        _apply_operation(changed, operation)
    return normalize_site_source(changed)


def normalize_site_source(source: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize authoring compatibility spellings before digesting source."""

    normalized = _normalize_json_numbers(deepcopy(dict(source)))
    _normalize_same_page_targets(normalized)
    return normalized


def _normalize_json_numbers(value: Any) -> Any:
    # JSON.stringify emits integral numbers without a decimal suffix. Keep the
    # retained source and its browser-verified page graph identical for numeric
    # data imported by Python (for example, population scale 1000000.0).
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_normalize_json_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_json_numbers(item) for key, item in value.items()}
    return value


def normalize_same_page_target(value: str) -> str:
    """Map the legacy ``/#anchor`` spelling to one typed same-page intent."""

    token = str(value or "")
    if re.fullmatch(r"/#[A-Za-z][A-Za-z0-9_-]{0,127}", token):
        return token[1:]
    return token


def _normalize_same_page_targets(value: Any, key: str | None = None) -> None:
    if isinstance(value, list):
        for item in value:
            _normalize_same_page_targets(item)
        return
    if not isinstance(value, dict):
        return
    for item_key, item in list(value.items()):
        if (
            isinstance(item, str)
            and item_key.lower() in {"href", "target", "url", "primary_target"}
        ):
            value[item_key] = normalize_same_page_target(item)
        else:
            _normalize_same_page_targets(item, item_key)


def _apply_operation(source: dict[str, Any], operation: Mapping[str, Any]) -> None:
    kind = operation["kind"]
    target = str(operation["target"])
    if kind in {"add_resource", "remove_resource"}:
        resource_id = _single_target(target, "resources")
        resources = source.setdefault("resources", [])
        if kind == "add_resource":
            value = deepcopy(operation.get("value"))
            if not isinstance(value, dict) or value.get("id") != resource_id:
                raise SiteStudioContractError("add_resource value identity mismatch")
            if any(resource.get("id") == resource_id for resource in resources):
                raise SiteStudioContractError(f"resource already exists: {resource_id}")
            resources.append(value)
        else:
            if any(
                section.get("resource_ref") == resource_id
                for route in source.get("routes", [])
                for section in route.get("sections", [])
            ):
                raise SiteStudioContractError(
                    f"resource is still referenced: {resource_id}"
                )
            _remove_by_id(resources, resource_id, "resource")
        return
    if kind == "set_resource_field":
        resource_id, field_path = _parse_target(target, "resources", "data")
        resource = _by_id(source.get("resources"), resource_id, "resource")
        _set_path(resource["data"], field_path, deepcopy(operation.get("value")))
        return
    if kind == "set_route_metadata":
        route_id, field_path = _parse_target(target, "routes", "metadata")
        route = _by_id(source.get("routes"), route_id, "route")
        _set_path(route["metadata"], field_path, deepcopy(operation.get("value")))
        return
    if kind in {"add_route", "remove_route"}:
        route_id = _single_target(target, "routes")
        routes = source.setdefault("routes", [])
        if kind == "add_route":
            value = deepcopy(operation.get("value"))
            if not isinstance(value, dict) or value.get("id") != route_id:
                raise SiteStudioContractError("add_route value identity mismatch")
            if any(route.get("id") == route_id for route in routes):
                raise SiteStudioContractError(f"route already exists: {route_id}")
            routes.append(value)
        else:
            _remove_by_id(routes, route_id, "route")
        return
    if kind == "set_identity_field":
        parts = target.split(".")
        if len(parts) != 2 or parts[0] != "identity":
            raise SiteStudioContractError(f"invalid identity target: {target}")
        if parts[1] == 'favicon_asset_ref' and operation.get('value') in (None, ''):
            source.setdefault('identity', {}).pop(parts[1], None)
            return
        _set_path(
            source.setdefault("identity", {}),
            [parts[1]],
            deepcopy(operation.get("value")),
        )
        return
    if kind == "set_theme_token":
        prefix = "theme.tokens."
        if not target.startswith(prefix) or not target[len(prefix) :]:
            raise SiteStudioContractError(f"invalid theme target: {target}")
        source.setdefault("theme", {}).setdefault("tokens", {})[
            target[len(prefix) :]
        ] = deepcopy(operation.get("value"))
        return
    if kind in {"add_asset", "replace_asset", "remove_asset"}:
        asset_id = _single_target(target, "assets")
        assets = source.setdefault("assets", [])
        if kind == "add_asset":
            value = deepcopy(operation.get("value"))
            if not isinstance(value, dict) or value.get("id") != asset_id:
                raise SiteStudioContractError("add_asset value identity mismatch")
            if any(asset.get("id") == asset_id for asset in assets):
                raise SiteStudioContractError(f"asset already exists: {asset_id}")
            assets.append(value)
        elif kind == "replace_asset":
            value = deepcopy(operation.get("value"))
            if not isinstance(value, dict) or value.get("id") != asset_id:
                raise SiteStudioContractError("replace_asset value identity mismatch")
            for index, asset in enumerate(assets):
                if asset.get("id") == asset_id:
                    assets[index] = value
                    break
            else:
                raise SiteStudioContractError(f"asset not found: {asset_id}")
        else:
            if source.get("identity", {}).get("favicon_asset_ref") == asset_id:
                raise SiteStudioContractError(f"asset is still referenced by favicon: {asset_id}")
            _remove_by_id(assets, asset_id, "asset")
            if any(
                _contains(candidate, asset_id)
                for candidate in source.get("resources", [])
            ):
                raise SiteStudioContractError(f"asset is still referenced: {asset_id}")
        return
    if kind == "assign_asset":
        resource_id, field_path = _parse_target(target, "resources", "data")
        asset_id = str(operation.get("value") or "")
        _by_id(source.get("assets"), asset_id, "asset")
        resource = _by_id(source.get("resources"), resource_id, "resource")
        _set_path(resource["data"], field_path, asset_id)
        return
    if kind in {"add_section", "remove_section", "move_section"}:
        route_id, section_id = _section_target(target)
        sections = _by_id(source.get("routes"), route_id, "route")["sections"]
        if kind == "add_section":
            value = deepcopy(operation.get("value"))
            if not isinstance(value, dict) or value.get("id") != section_id:
                raise SiteStudioContractError("add_section value identity mismatch")
            if any(section.get("id") == section_id for section in sections):
                raise SiteStudioContractError(f"section already exists: {section_id}")
            _insert_relative(
                sections, value, operation.get("before"), operation.get("after")
            )
        elif kind == "remove_section":
            _remove_by_id(sections, section_id, "section")
        else:
            section = _remove_by_id(sections, section_id, "section")
            _insert_relative(
                sections, section, operation.get("before"), operation.get("after")
            )
        return
    raise SiteStudioContractError(f"unsupported site operation: {kind}")


def _parse_target(target: str, root: str, branch: str) -> tuple[str, list[str]]:
    parts = target.split(".")
    if len(parts) < 4 or parts[0] != root or parts[2] != branch:
        raise SiteStudioContractError(f"invalid {root} target: {target}")
    return parts[1], parts[3:]


def _single_target(target: str, root: str) -> str:
    parts = target.split(".")
    if len(parts) != 2 or parts[0] != root or not parts[1]:
        raise SiteStudioContractError(f"invalid {root} target: {target}")
    return parts[1]


def _section_target(target: str) -> tuple[str, str]:
    parts = target.split(".")
    if len(parts) != 4 or parts[0] != "routes" or parts[2] != "sections":
        raise SiteStudioContractError(f"invalid section target: {target}")
    return parts[1], parts[3]


def _by_id(values: Any, identity: str, label: str) -> dict[str, Any]:
    if not isinstance(values, list):
        raise SiteStudioContractError(f"{label} collection is missing")
    result = next((value for value in values if value.get("id") == identity), None)
    if result is None:
        raise SiteStudioContractError(f"{label} not found: {identity}")
    return result


def _remove_by_id(
    values: list[dict[str, Any]], identity: str, label: str
) -> dict[str, Any]:
    for index, value in enumerate(values):
        if value.get("id") == identity:
            return values.pop(index)
    raise SiteStudioContractError(f"{label} not found: {identity}")


def _insert_relative(
    values: list[dict[str, Any]],
    value: dict[str, Any],
    before: Any,
    after: Any,
) -> None:
    if before and after:
        raise SiteStudioContractError("use either before or after")
    anchor = str(before or after or "")
    if not anchor:
        values.append(value)
        return
    index = next(
        (index for index, item in enumerate(values) if item.get("id") == anchor), -1
    )
    if index < 0:
        raise SiteStudioContractError(f"relative section not found: {anchor}")
    values.insert(index if before else index + 1, value)


def _set_path(root: dict[str, Any], path: list[str], value: Any) -> None:
    if not path or any(
        not part or part in {"__proto__", "constructor", "prototype"} for part in path
    ):
        raise SiteStudioContractError("unsafe field path")
    def index(part: str, values: list[Any]) -> int:
        if not re.fullmatch(r"0|[1-9][0-9]*", part) or int(part) >= len(values):
            raise SiteStudioContractError(f"field path requires an existing array index: {part}")
        return int(part)

    current: Any = root
    for part in path[:-1]:
        if isinstance(current, dict):
            nested = current.get(part)
            if nested is None:
                nested = {}
                current[part] = nested
        elif isinstance(current, list):
            nested = current[index(part, current)]
        else:
            raise SiteStudioContractError(f"field path is not a container: {part}")
        if not isinstance(nested, (dict, list)):
            raise SiteStudioContractError(f"field path is not a container: {part}")
        current = nested
    if isinstance(current, list):
        current[index(path[-1], current)] = value
    else:
        current[path[-1]] = value


def _contains(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return value == needle
    if isinstance(value, list):
        return any(_contains(item, needle) for item in value)
    if isinstance(value, dict):
        return any(_contains(item, needle) for item in value.values())
    return False
