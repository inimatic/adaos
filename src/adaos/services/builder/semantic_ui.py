"""Bounded, reversible semantic edits for declarative scenario interfaces."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .workflow import (
    BUILDER_CONTEXT_PACKET_SCHEMA,
    BuilderWorkflowError,
    BuilderWorkflowService,
    _LOCK,
    _now,
    _replace_path,
)


BUILDER_SEMANTIC_UI_CHANGE_SCHEMA = "adaos.builder.semantic_ui_change.v1"
BUILDER_UI_REVISION_SCHEMA = "adaos.builder.ui_revision.v1"


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuilderWorkflowError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise BuilderWorkflowError(f"{label} must contain a JSON object")
    return dict(value)


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    return _read_json(path, label=name)


def _walk(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _widget_candidates(webui: Mapping[str, Any], widget_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in _walk(webui)
        if isinstance(item, dict)
        and str(item.get("id") or "") == widget_id
        and any(key in item for key in ("type", "widgets", "fields", "actions", "layout"))
    ]


def _resolve_target(webui: Mapping[str, Any], target_ref: str) -> tuple[dict[str, Any], str]:
    parts = str(target_ref or "").split(":")
    if len(parts) == 2 and parts[0] == "widget":
        matches = _widget_candidates(webui, parts[1])
        target_kind = "widget"
    elif len(parts) == 3 and parts[0] == "field":
        widgets = _widget_candidates(webui, parts[1])
        if len(widgets) != 1:
            raise BuilderWorkflowError(
                f"semantic UI target requires exactly one widget {parts[1]!r}; found {len(widgets)}"
            )
        matches = [
            item
            for item in _walk(widgets[0])
            if isinstance(item, dict) and str(item.get("id") or "") == parts[2]
        ]
        target_kind = "field"
    else:
        raise BuilderWorkflowError("semantic UI target_ref must be widget:<id> or field:<widget_id>:<field_id>")
    if len(matches) != 1:
        raise BuilderWorkflowError(
            f"semantic UI target {target_ref!r} must resolve exactly once; found {len(matches)}"
        )
    return matches[0], target_kind


def _target_location(
    value: Any,
    target: Mapping[str, Any],
    *,
    parent: Mapping[str, Any] | None = None,
    collection: str | None = None,
) -> tuple[list[Any], int, Mapping[str, Any] | None, str] | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            found = _target_location(child, target, parent=value, collection=str(key))
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if child is target:
                return value, index, parent, str(collection or "")
            found = _target_location(child, target, parent=parent, collection=collection)
            if found is not None:
                return found
    return None


def _stable_ref(value: Mapping[str, Any] | None) -> str | None:
    if not isinstance(value, Mapping):
        return None
    identifier = str(value.get("id") or "").strip()
    if not identifier:
        return None
    return f"widget:{identifier}"


def _rename(target: dict[str, Any], target_kind: str, value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        text = " ".join(str(value.get("text") or "").split())
        property_name = str(value.get("property") or "").strip()
    else:
        text = " ".join(str(value or "").split())
        property_name = ""
    property_name = property_name or ("title" if target_kind == "widget" else "label")
    if property_name not in {"title", "label"}:
        raise BuilderWorkflowError("rename supports only title or label properties")
    if not text:
        raise BuilderWorkflowError("rename text is required")
    if len(text) > 240:
        raise BuilderWorkflowError("rename text exceeds 240 characters")
    before = copy.deepcopy(target.get(property_name))
    target[property_name] = text
    return {
        "property": property_name,
        "before": before,
        "after": text,
        "undo": {
            "operation": "set_property",
            "property": property_name,
            "value": before,
            "remove_when_null": before is None,
        },
    }


def _add(target: dict[str, Any], target_ref: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BuilderWorkflowError("add requires an object value")
    collection = str(value.get("collection") or "fields").strip()
    if collection not in {"fields", "widgets", "actions"}:
        raise BuilderWorkflowError("add collection must be fields, widgets, or actions")
    item = copy.deepcopy(value.get("item"))
    if not isinstance(item, Mapping):
        raise BuilderWorkflowError("add requires value.item")
    item = dict(item)
    item_id = str(item.get("id") or "").strip()
    if not item_id:
        raise BuilderWorkflowError("added field, widget, or action requires a stable id")
    children = target.setdefault(collection, [])
    if not isinstance(children, list):
        raise BuilderWorkflowError(f"target {target_ref} does not expose a {collection} list")
    if any(isinstance(child, Mapping) and str(child.get("id") or "") == item_id for child in children):
        raise BuilderWorkflowError(f"duplicate {collection} id: {item_id}")
    raw_index = value.get("index")
    index = len(children) if raw_index is None else int(raw_index)
    if index < 0 or index > len(children):
        raise BuilderWorkflowError("add index is outside the target collection")
    children.insert(index, item)
    added_ref = (
        f"field:{str(target.get('id') or '').strip()}:{item_id}"
        if collection == "fields"
        else f"widget:{item_id}"
    )
    return {
        "property": collection,
        "before": None,
        "after": copy.deepcopy(item),
        "undo": {
            "operation": "remove",
            "target_ref": added_ref,
            "parent_ref": target_ref,
            "collection": collection,
            "index": index,
        },
    }


def _remove(
    webui: Mapping[str, Any],
    target: dict[str, Any],
    target_ref: str,
) -> dict[str, Any]:
    location = _target_location(webui, target)
    if location is None:
        raise BuilderWorkflowError(f"semantic UI target {target_ref!r} is not removable")
    container, index, parent, collection = location
    if collection not in {"fields", "widgets", "actions"}:
        raise BuilderWorkflowError("remove is limited to fields, widgets, or actions")
    removed = copy.deepcopy(container[index])
    parent_ref = _stable_ref(parent)
    if parent_ref is None:
        raise BuilderWorkflowError(
            "remove requires a stable widget parent so deterministic undo remains possible"
        )
    del container[index]
    return {
        "property": collection,
        "before": removed,
        "after": None,
        "undo": {
            "operation": "add",
            "parent_ref": parent_ref,
            "collection": collection,
            "index": index,
            "value": removed,
        },
    }


def _move(
    webui: Mapping[str, Any],
    target: dict[str, Any],
    target_ref: str,
    value: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BuilderWorkflowError("move requires an object value")
    location = _target_location(webui, target)
    if location is None:
        raise BuilderWorkflowError(f"semantic UI target {target_ref!r} is not movable")
    container, source_index, _parent, collection = location
    selectors = [key for key in ("before_ref", "after_ref", "index") if value.get(key) is not None]
    if len(selectors) != 1:
        raise BuilderWorkflowError("move requires exactly one of before_ref, after_ref, or index")
    requested_index: int
    if "index" in selectors:
        requested_index = int(value["index"])
        if requested_index < 0 or requested_index >= len(container):
            raise BuilderWorkflowError("move index is outside the target collection")
    else:
        anchor_ref = str(value[selectors[0]] or "").strip()
        if anchor_ref == target_ref:
            raise BuilderWorkflowError("move anchor must differ from target")
        anchor, _anchor_kind = _resolve_target(webui, anchor_ref)
        anchor_location = _target_location(webui, anchor)
        if anchor_location is None or anchor_location[0] is not container:
            raise BuilderWorkflowError("move target and anchor must share the same collection")
        anchor_index = anchor_location[1]
        requested_index = anchor_index if selectors[0] == "before_ref" else anchor_index + 1
    item = container.pop(source_index)
    if requested_index > source_index:
        requested_index -= 1
    target_index = max(0, min(requested_index, len(container)))
    container.insert(target_index, item)
    return {
        "property": collection,
        "before": {"index": source_index},
        "after": {
            "index": target_index,
            "breakpoints": list(value.get("breakpoints") or ["all"]),
        },
        "undo": {
            "operation": "move",
            "target_ref": target_ref,
            "value": {"index": source_index, "breakpoints": list(value.get("breakpoints") or ["all"])},
        },
    }


def _write_bytes_atomic(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.semantic-ui.tmp")
    temporary.write_bytes(raw)
    _replace_path(temporary, path)


@dataclass(slots=True)
class BuilderSemanticUIService:
    workflow: BuilderWorkflowService

    @classmethod
    def from_context(cls) -> "BuilderSemanticUIService":
        return cls(workflow=BuilderWorkflowService.from_context())

    def apply(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one local-reversible operation and record an immutable UI Revision."""

        value = copy.deepcopy(dict(operation))
        value.setdefault("schema", BUILDER_SEMANTIC_UI_CHANGE_SCHEMA)
        value.setdefault("risk", "local_reversible")
        Draft202012Validator(_schema("builder.semantic_ui_change.v1.schema.json")).validate(value)
        if value["operation"] not in {"rename", "move", "add", "remove", "set_data_mode"}:
            raise BuilderWorkflowError(
                "enabled semantic UI operations are rename, move, add, remove, and set_data_mode"
            )

        change_id = str(value["change_id"]).strip()
        operation_id = str(value["operation_id"]).strip()
        source_revision = str(value["source_revision"]).strip()
        target_ref = str(value["target_ref"]).strip()
        project_ref = str(value.get("project_ref") or "").strip()
        if not project_ref:
            raise BuilderWorkflowError("semantic UI operation requires project_ref")
        try:
            object_type, object_id = project_ref.split(":", 1)
        except ValueError as exc:
            raise BuilderWorkflowError("project_ref must be scenario:<id>") from exc
        if object_type != "scenario":
            raise BuilderWorkflowError("semantic UI operations currently support scenarios only")

        with _LOCK:
            projection = self.workflow.describe(object_type, object_id)
            change = projection.get("change")
            if not isinstance(change, Mapping) or str(change.get("change_id") or "") != change_id:
                raise BuilderWorkflowError("semantic UI operation does not match the active Change")
            if not projection["capabilities"].get("can_edit_prototype"):
                raise BuilderWorkflowError("semantic UI operations require an editable Prototype")
            current_revision = self.workflow.current_prototype_revision(object_type, object_id)
            if current_revision != source_revision:
                raise BuilderWorkflowError(
                    f"stale semantic UI source revision: expected {source_revision}, current {current_revision}"
                )

            if value["operation"] == "set_data_mode":
                binding = projection.get("data_binding") if isinstance(projection.get("data_binding"), Mapping) else {}
                selected = value.get("value") if isinstance(value.get("value"), Mapping) else {"profile_id": value.get("value")}
                profile_id = str(selected.get("profile_id") or selected.get("id") or "").strip()
                result = self.workflow.select_binding_profile(
                    object_type,
                    object_id,
                    profile_id,
                    expected_binding_generation=int(binding.get("generation") or 0),
                    confirmed=bool(selected.get("confirmed")),
                )
                return {
                    "ok": True,
                    "operation": value,
                    "revision": source_revision,
                    "revision_ref": f"ui_revision:{source_revision}",
                    "ui_revision_changed": False,
                    "binding": copy.deepcopy(result["workflow"].get("data_binding")),
                    "workflow": result["workflow"],
                }

            root = self.workflow.project_root(object_type, object_id)
            webui_path = root / "webui.json"
            scenario_json_path = root / "scenario.json"
            current_path = root / "ui_revisions" / "current.txt"
            before_webui_raw = webui_path.read_bytes()
            before_scenario_raw = scenario_json_path.read_bytes()
            before_current_raw = current_path.read_bytes()
            before_webui = _read_json(webui_path, label="webui.json")
            after_webui = copy.deepcopy(before_webui)
            target, target_kind = _resolve_target(after_webui, target_ref)
            if value["operation"] == "rename":
                edit = _rename(target, target_kind, value.get("value"))
            elif value["operation"] == "move":
                edit = _move(after_webui, target, target_ref, value.get("value"))
            elif value["operation"] == "add":
                if target_kind != "widget":
                    raise BuilderWorkflowError("add requires a widget parent target")
                edit = _add(target, target_ref, value.get("value"))
            else:
                edit = _remove(after_webui, target, target_ref)
            Draft202012Validator(_schema("webui.v1.schema.json")).validate(after_webui)

            scenario_json = _read_json(scenario_json_path, label="scenario.json")
            scenario_json["ui"] = copy.deepcopy(after_webui.get("ui") or {})
            if isinstance(scenario_json["ui"], dict):
                scenario_json["ui"]["manifest"] = "webui.json"

            revision_dir = root / "ui_revisions"
            revision_dir.mkdir(parents=True, exist_ok=True)
            numbers = [int(path.stem) for path in revision_dir.glob("*.json") if path.stem.isdigit()]
            revision = f"{(max(numbers) + 1) if numbers else 1:03d}"
            revision_path = revision_dir / f"{revision}.json"
            if revision_path.exists():
                raise BuilderWorkflowError(f"semantic UI revision already exists: {revision}")
            built_at = _now()
            revision_payload = {
                "schema": BUILDER_UI_REVISION_SCHEMA,
                "revision": revision,
                "created_at": built_at,
                "scenario_id": object_id,
                "request": {"text": f"Semantic UI {value['operation']} for {target_ref}"},
                "patch": {
                    "id": operation_id,
                    "target": target_ref,
                    "operation": value["operation"],
                    "status": "applied",
                    "source_revision": source_revision,
                    "change_id": change_id,
                    "review_id": value.get("review_id"),
                    "before": edit["before"],
                    "after": edit["after"],
                    "property": edit["property"],
                    "undo": edit["undo"],
                    "acceptance": copy.deepcopy(value.get("acceptance")),
                },
                "before_webui": before_webui,
                "after_webui": after_webui,
                "preview_state": {},
            }
            prompt_state_path = root / "prompt_state.json"
            prompt_state_existed = prompt_state_path.is_file()
            before_prompt_state_raw = prompt_state_path.read_bytes() if prompt_state_existed else None
            context_packet = self.workflow.build_context_packet(
                object_type,
                object_id,
                allowed_paths=["scenario.yaml", "scenario.json", "webui.json"],
                persist=True,
            )
            if context_packet.get("schema") != BUILDER_CONTEXT_PACKET_SCHEMA:
                raise BuilderWorkflowError("semantic UI context packet is invalid")

            new_paths: list[Path] = []
            try:
                _write_bytes_atomic(
                    webui_path,
                    (json.dumps(after_webui, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
                _write_bytes_atomic(
                    scenario_json_path,
                    (json.dumps(scenario_json, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
                _write_bytes_atomic(
                    revision_path,
                    (json.dumps(revision_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
                new_paths.append(revision_path)
                _write_bytes_atomic(current_path, (revision + "\n").encode("utf-8"))
                transition = self.workflow.transition(
                    object_type,
                    object_id,
                    "prototype_revision_recorded",
                    actor="builder.semantic_ui",
                    metadata={
                        "object_type": object_type,
                        "revision": revision,
                        "change_id": change_id,
                        "run_id": operation_id,
                        "context_packet_digest": context_packet["digest"],
                        "input_refs": [f"ui_revision:{source_revision}", target_ref],
                        "output_refs": [f"ui_revision:{revision}"],
                        "evidence_refs": [f"semantic_ui_change:{operation_id}"],
                    },
                    expected_generation=projection["generation"],
                )
            except Exception:
                _write_bytes_atomic(webui_path, before_webui_raw)
                _write_bytes_atomic(scenario_json_path, before_scenario_raw)
                _write_bytes_atomic(current_path, before_current_raw)
                if before_prompt_state_raw is not None:
                    _write_bytes_atomic(prompt_state_path, before_prompt_state_raw)
                elif not prompt_state_existed and prompt_state_path.is_file():
                    prompt_state_path.unlink()
                for path in new_paths:
                    if path.is_file():
                        path.unlink()
                raise

        return {
            "ok": True,
            "operation": value,
            "revision": revision,
            "revision_ref": f"ui_revision:{revision}",
            "undo": edit["undo"],
            "context_packet_digest": context_packet["digest"],
            "workflow": transition["workflow"],
        }


__all__ = [
    "BUILDER_SEMANTIC_UI_CHANGE_SCHEMA",
    "BUILDER_UI_REVISION_SCHEMA",
    "BuilderSemanticUIService",
]
