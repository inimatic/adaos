"""Compact semantic composition context for safe prototype UI changes."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .semantic_ui import _resolve_target, _target_location
from .workflow import BuilderWorkflowError


UI_COMPOSITION_SLICE_SCHEMA = "adaos.builder.ui_composition_slice.v1"


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / "builder.ui_composition_slice.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _node_ref(value: Mapping[str, Any], *, collection: str = "") -> str | None:
    identifier = str(value.get("id") or "").strip()
    if not identifier:
        return None
    if collection == "fields":
        return f"field:{identifier}"
    if any(key in value for key in ("type", "fields", "widgets", "actions")):
        return f"widget:{identifier}"
    return f"node:{identifier}"


def _path(value: Any, target: Mapping[str, Any], trail: list[Mapping[str, Any]] | None = None):
    current_trail = list(trail or [])
    if value is target:
        return current_trail
    if isinstance(value, Mapping):
        next_trail = current_trail + [value]
        for child in value.values():
            found = _path(child, target, next_trail)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _path(child, target, current_trail)
            if found is not None:
                return found
    return None


def _sibling_ref(item: Any, *, kind: str, parent: Mapping[str, Any] | None) -> str | None:
    if not isinstance(item, Mapping):
        return None
    identifier = str(item.get("id") or "").strip()
    if not identifier:
        return None
    if kind == "field":
        parent_id = str((parent or {}).get("id") or "").strip()
        return f"field:{parent_id}:{identifier}" if parent_id else None
    return f"widget:{identifier}"


def extract_composition_slice(
    webui: Mapping[str, Any],
    target_ref: str,
    *,
    source_revision: str,
    acceptance: list[Mapping[str, Any]] | None = None,
    evidence_budget: int = 5,
) -> dict[str, Any]:
    """Project just enough structure to interpret spatial UI instructions."""

    if evidence_budget < 1 or evidence_budget > 20:
        raise BuilderWorkflowError("renderer evidence budget must be between 1 and 20")
    target, kind = _resolve_target(webui, target_ref)
    location = _target_location(webui, target)
    if location is None:
        raise BuilderWorkflowError(f"UI composition target is not attached: {target_ref}")
    container, order, parent, collection = location
    path = _path(webui, target) or []
    logical_parent = parent
    if kind == "field" and not str((parent or {}).get("id") or "").strip():
        logical_parent = next(
            (
                item
                for item in reversed(path)
                if str(item.get("id") or "").strip() and str(item.get("type") or "").strip()
            ),
            parent,
        )
    sibling_refs = [
        ref
        for item in container
        if (ref := _sibling_ref(item, kind=kind, parent=logical_parent)) is not None
    ]
    if target_ref not in sibling_refs:
        raise BuilderWorkflowError(f"UI composition target lacks stable sibling identity: {target_ref}")
    ancestor_refs = [ref for item in path if (ref := _node_ref(item)) is not None]
    layout_owner = next(
        (item for item in reversed(path) if isinstance(item, Mapping) and "layout" in item),
        parent if isinstance(parent, Mapping) else {},
    )
    layout = copy.deepcopy(layout_owner.get("layout")) if isinstance(layout_owner, Mapping) else None
    responsive = None
    if isinstance(layout, Mapping):
        responsive = copy.deepcopy(layout.get("responsive"))
    if responsive is None and isinstance(layout_owner, Mapping):
        responsive = copy.deepcopy(layout_owner.get("responsive"))
    label = target.get("label") or target.get("title") or target.get("text")
    bindings = {
        key: copy.deepcopy(target[key])
        for key in ("stateKey", "binding", "dataSource", "value", "items")
        if key in target
    }
    actions = copy.deepcopy(target.get("actions") or [])
    half = evidence_budget // 2
    start = max(0, order - half)
    end = min(len(sibling_refs), start + evidence_budget)
    start = max(0, end - evidence_budget)
    visible = sibling_refs[start:end]
    evidence_source = {
        "target": copy.deepcopy(target),
        "parent_ref": _node_ref(logical_parent or {}),
        "visible_neighbor_refs": visible,
        "layout": layout,
        "responsive": responsive,
    }
    result = {
        "schema": UI_COMPOSITION_SLICE_SCHEMA,
        "slice_id": f"composition-{_digest({'revision': source_revision, 'target': target_ref})[:16]}",
        "source_revision": str(source_revision),
        "target": {
            "ref": target_ref,
            "kind": kind,
            "id": str(target.get("id")),
            "type": str(target.get("type")) if target.get("type") is not None else None,
            "label": str(label) if label is not None else None,
            "area": str(target.get("area")) if target.get("area") is not None else None,
        },
        "parent_ref": _node_ref(logical_parent or {}),
        "siblings": sibling_refs,
        "order": order,
        "ancestors": ancestor_refs,
        "composition": {
            "collection": collection,
            "layout": layout,
            "responsive": responsive,
        },
        "actions": actions if isinstance(actions, list) else [actions],
        "bindings": bindings,
        "acceptance": [copy.deepcopy(dict(item)) for item in (acceptance or [])],
        "renderer_evidence": {
            "kind": "bounded_structured",
            "target_ref": target_ref,
            "visible_neighbor_refs": visible,
            "budget": evidence_budget,
            "truncated": len(sibling_refs) > len(visible),
            "digest": _digest(evidence_source),
        },
    }
    Draft202012Validator(_schema()).validate(result)
    return result


def evaluate_spatial_constraint(slice_value: Mapping[str, Any], constraint: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate order evidence without pretending to render pixels."""

    relation = str(constraint.get("relation") or "").strip()
    reference = str(constraint.get("reference_ref") or "").strip()
    target = str(dict(slice_value.get("target") or {}).get("ref") or "")
    siblings = [str(item) for item in slice_value.get("siblings") or []]
    if relation not in {"before", "after"}:
        raise BuilderWorkflowError("spatial constraint relation must be before or after")
    if target not in siblings or reference not in siblings:
        raise BuilderWorkflowError("spatial constraint requires targets in the same stable sibling collection")
    target_index = siblings.index(target)
    reference_index = siblings.index(reference)
    passed = target_index < reference_index if relation == "before" else target_index > reference_index
    return {
        "passed": passed,
        "relation": relation,
        "target_ref": target,
        "reference_ref": reference,
        "target_order": target_index,
        "reference_order": reference_index,
        "breakpoints": list(constraint.get("breakpoints") or ["all"]),
        "evidence_kind": "declarative_composition",
    }


__all__ = ["UI_COMPOSITION_SLICE_SCHEMA", "evaluate_spatial_constraint", "extract_composition_slice"]
