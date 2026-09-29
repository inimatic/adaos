from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import y_py as Y


def clone_json_like(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except Exception:
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k): clone_json_like(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clone_json_like(v) for v in value]
        if isinstance(value, tuple):
            return [clone_json_like(v) for v in value]
        if isinstance(value, Mapping):
            return {str(k): clone_json_like(v) for k, v in value.items()}
        items = getattr(value, "items", None)
        if callable(items):
            try:
                return {str(k): clone_json_like(v) for k, v in items()}
            except Exception:
                return {}
        if not isinstance(value, (str, bytes, bytearray)):
            try:
                return [clone_json_like(v) for v in value]
            except Exception:
                pass
        return repr(value)


def mapping_items(value: Any) -> list[tuple[str, Any]] | None:
    if type(value) is dict:
        return [(str(key), item) for key, item in value.items() if str(key)]
    if isinstance(value, Mapping):
        return [(str(key), item) for key, item in value.items() if str(key)]
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return [(str(key), item) for key, item in items() if str(key)]
        except Exception:
            return None
    return None


_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def json_like_equal(current: Any, next_value: Any) -> bool:
    if current is next_value:
        return True

    if isinstance(current, _JSON_SCALAR_TYPES) and isinstance(next_value, _JSON_SCALAR_TYPES):
        try:
            return current == next_value
        except Exception:
            return False

    if type(current) is list and type(next_value) is list:
        # Values read from a YMap JSON array are ordinary Python JSON
        # containers.  Their C-level equality is materially faster than a
        # Python recursive walk for large page/catalog projections.
        try:
            return current == next_value
        except Exception:
            return False

    if type(current) is dict and type(next_value) is dict:
        try:
            return current == next_value
        except Exception:
            return False

    if isinstance(current, (list, tuple)) or isinstance(next_value, (list, tuple)):
        if not isinstance(current, (list, tuple)) or not isinstance(next_value, (list, tuple)):
            return False
        if len(current) != len(next_value):
            return False
        return all(json_like_equal(left, right) for left, right in zip(current, next_value))

    current_items = mapping_items(current)
    next_items = mapping_items(next_value)
    if current_items is not None or next_items is not None:
        if current_items is None or next_items is None:
            return False
        if len(current_items) != len(next_items):
            return False
        next_lookup = {key: item for key, item in next_items}
        if len(next_lookup) != len(next_items):
            return False
        for key, current_item in current_items:
            if key not in next_lookup:
                return False
            if not json_like_equal(current_item, next_lookup[key]):
                return False
        return True

    try:
        return current == next_value
    except Exception:
        return clone_json_like(current) == clone_json_like(next_value)


def is_y_map_value(value: Any) -> bool:
    y_map_type = getattr(Y, "YMap", None)
    return bool(y_map_type) and isinstance(value, y_map_type)


def _attach_empty_y_map(parent_map: Any, txn: Any, key: str) -> Any | None:
    y_map_type = getattr(Y, "YMap", None)
    if not y_map_type or not is_y_map_value(parent_map):
        return None
    try:
        parent_map.set(txn, key, y_map_type({}))
        attached = parent_map.get(key)
    except Exception:
        return None
    return attached if is_y_map_value(attached) else None


def _reconcile_attached_y_map_items(
    node: Any,
    txn: Any,
    next_items: list[tuple[str, Any]],
) -> bool:
    changed = False
    next_keys = {key for key, _item in next_items}
    current_items = mapping_items(node) or []
    current_by_key = dict(current_items)
    for current_key in current_by_key:
        if current_key in next_keys:
            continue
        try:
            node.pop(txn, current_key)
            changed = True
        except Exception:
            continue
    for child_key, raw_child in next_items:
        child_items = mapping_items(raw_child)
        current_child = current_by_key.get(child_key)
        if child_items is not None:
            if is_y_map_value(current_child):
                if _reconcile_attached_y_map_items(
                    current_child,
                    txn,
                    child_items,
                ):
                    changed = True
                continue
            if json_like_equal(current_child, raw_child):
                continue
            attached_child = _attach_empty_y_map(node, txn, child_key)
            if attached_child is None:
                node.set(txn, child_key, clone_json_like(raw_child))
                changed = True
                continue
            changed = True
            reconcile_attached_y_map(attached_child, txn, raw_child)
            continue
        if json_like_equal(current_child, raw_child):
            continue
        node.set(txn, child_key, clone_json_like(raw_child))
        changed = True
    return changed


def reconcile_attached_y_map(node: Any, txn: Any, next_value: Any) -> bool:
    next_items = mapping_items(next_value)
    if next_items is None:
        return False
    return _reconcile_attached_y_map_items(node, txn, next_items)


def set_map_value_if_changed(y_map: Any, txn: Any, key: str, value: Any) -> tuple[bool, str]:
    try:
        current = y_map.get(key)
    except Exception:
        current = None
    value_items = mapping_items(value)
    if value_items is not None:
        if is_y_map_value(current):
            return _reconcile_attached_y_map_items(current, txn, value_items), "diff"
        if json_like_equal(current, value):
            attached = _attach_empty_y_map(y_map, txn, key)
            if attached is not None:
                _reconcile_attached_y_map_items(attached, txn, value_items)
                return True, "diff"
            return False, "diff"
        attached = _attach_empty_y_map(y_map, txn, key)
        if attached is not None:
            _reconcile_attached_y_map_items(attached, txn, value_items)
            return True, "diff"
        y_map.set(txn, key, clone_json_like(value))
        return True, "replace"
    if json_like_equal(current, value):
        return False, "replace"
    y_map.set(txn, key, clone_json_like(value))
    return True, "replace"
