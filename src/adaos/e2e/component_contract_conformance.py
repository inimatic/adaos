"""Independent bounded reference semantics, never a compiler-as-oracle.

Only the named synthetic records and declared operations are in this model.
No claim is made about general UI semantics or whole-platform verification.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def load_slice(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = json.loads((repo_root / "e2e/builder/development/contract-conformance/slice.json").read_text(encoding="utf-8"))
    source = (repo_root / spec["base_fixture"]).resolve()
    if not source.is_relative_to((repo_root / "e2e/builder/development").resolve()):
        raise ValueError("fixture outside development cohort")
    document = json.loads(source.read_text(encoding="utf-8"))
    view = next(item for item in document["views"] if item["id"] == spec["scope"]["view_ref"])
    view["query_controls"] = copy.deepcopy(spec["query_controls"])
    view["scope_filters"] = [{key: value for key, value in spec["scope"].items() if key != "view_ref"}]
    # Explicit relationship label identity is independent of collection order.
    document["relationships"][0]["label_field_refs"] = ["team_name"]
    records = document["resources"][0]
    records["fields"].append({"id": "review_note", "label": {"key": "record.review_note", "en": "Review note", "ru": "Комментарий"},
        "value_type": "short_text", "required": False, "editable": True})
    for row in records["records"]:
        row["review_note"] = "" if row["state"] == "open" else "Reviewed"
    for view in document["views"]:
        if view["role"] == "editor":
            view["field_refs"].append("review_note")
    for command in document["commands"]:
        if command["kind"] in {"create", "update"}:
            command["input_field_refs"].append("review_note")
        if command["id"] == "complete":
            command["input_field_refs"] = ["review_note"]
            command["guard"]["require_nonempty"] = ["review_note"]
    return document, spec


@dataclass
class CollectionEditorReference:
    records: list[dict[str, Any]]
    scope: dict[str, Any]
    search: str = ""
    user_filters: dict[str, Any] = field(default_factory=dict)
    draft: dict[str, Any] | None = None
    selected_id: str | None = None

    def visible_ids(self) -> list[str]:
        return [row["id"] for row in self.records
                if row[self.scope["field_ref"]] == self.scope["value"]
                and all(row[key] == value for key, value in self.user_filters.items() if value != "")
                and (not self.search or self.search.casefold() in " ".join(str(v) for v in row.values()).casefold())]

    def reset_query(self) -> None:
        self.search = ""
        self.user_filters.clear()

    def open_editor(self, record_id: str) -> None:
        self.selected_id = record_id
        self.draft = copy.deepcopy(next(row for row in self.records if row["id"] == record_id))

    def cancel(self) -> None:
        self.draft = None

    def commit(self, operation: str) -> str:
        if self.draft is None:
            raise ValueError("no editor draft")
        if not all(self.draft.get(name) for name in ("name", "state", "team_id")):
            return "validation_error"
        row = next(row for row in self.records if row["id"] == self.selected_id)
        if operation == "complete":
            if self.draft["state"] == "open" and not self.draft["review_note"]:
                return "guard_error"
            # The declared guard is a conditional requirement, not a predicate
            # that requires 'open'. An already-done record remains admissible.
            patch = {"state": "done", "review_note": self.draft["review_note"]}
        elif operation == "save":
            patch = {name: self.draft[name] for name in ("name", "state", "team_id", "review_note")}
        else:
            raise ValueError("unadmitted reference operation")
        row.update(patch)
        return "committed"


def matched_runtime_episodes(compiled: dict[str, Any], state_root: Path, *, episode_spec=None) -> list[dict[str, Any]]:
    """Compare independently specified outcomes across actual provider changes.

    Each episode has a fresh StateSpace; no always-reject implementation can
    pass because its baseline and compatible variants must commit successfully.
    """
    from adaos.sdk.developer.prototypes import derive_resource_specs
    from adaos.services.resources.prototype import PrototypeResourceService
    from adaos.services.resources.workbench import ResourceWorkbenchService
    specs = derive_resource_specs(compiled["webui"], compiled["prototype_resources"])
    oracle = {'state_field': 'state', 'note_field': 'review_note', 'before_state': 'open', 'after_state': 'done',
              'command_id': 'complete', 'record_id': 'record-1', **(episode_spec or {})}
    state_field, note_field = oracle['state_field'], oracle['note_field']
    payload = {state_field: oracle['after_state'], note_field: 'Approved'}
    rows = []
    for episode, expected in [("baseline", "committed"), ("binding_swap", "committed"),
                              ("schema_extension", "committed"), ("schema_incompatible", "rejected"),
                              ("binding_unavailable", "rejected"), ("authority_denied", "rejected"),
                              ("state_locked", "rejected")]:
        provider = PrototypeResourceService(state_root / episode)
        transformed = copy.deepcopy(specs)
        targets = {spec["resource_definition"]["resource_type"]: spec["resource_definition"]["resource_type"] + ".alternate"
                   for spec in transformed} if episode == "binding_swap" else {}
        for spec in transformed:
            definition, data = spec["resource_definition"], spec["data_definition"]
            if targets:
                original = definition["resource_type"]
                definition["resource_type"] = targets[original]
                data["source_id"] += ".alternate"
                definition["authority"]["binding"] = data["source_id"]
                for relation in definition.get("metadata", {}).get("prototype_policy", {}).get("relationships", []):
                    relation["target_resource_type"] = targets[relation["target_resource_type"]]
            if state_field in definition["record_schema"]["properties"]:
                if episode == "schema_extension":
                    for schema in (definition["record_schema"], data["record_schema"]):
                        schema["properties"]["qualification_marker"] = {"type": "string"}
                elif episode == "schema_incompatible":
                    for schema in (definition["record_schema"], data["record_schema"]):
                        schema["properties"][state_field] = {"enum": [oracle['before_state']]}
                elif episode == "state_locked":
                    definition["metadata"]["prototype_policy"]["read_only_when"] = {"field_ref": state_field, "operator": "equals", "value": oracle['before_state']}
        resource_type = transformed[0]["resource_definition"]["resource_type"]
        original_records = copy.deepcopy(compiled["prototype_resources"][0]["records"])
        before = None
        observed, error = "not_attempted", None
        try:
            if episode != "binding_unavailable":
                for spec in transformed:
                    provider.materialize({"schema": "adaos.builder.prototype_resource.v1",
                        "project_ref": "project:contract-reference", "change_id": "change-contract-reference",
                        "revision": "001", "webui_digest": compiled["semantic_digest"], **spec})
                before = provider.query(resource_type, filters={}, search="", sort=[], limit=None)
            if episode == "authority_denied":
                ResourceWorkbenchService(state_dir=state_root / episode).operate({"schema": "adaos.resource.operation.v1",
                    "resource_type": resource_type, "operation_id": "update", "record_id": oracle['record_id'],
                    "payload": payload, "actor": {"id": "guest", "role": "guest"},
                    "context": {"prototype_command_id": oracle['command_id']}})
            else:
                provider.operate(resource_type, "update", record_id=oracle['record_id'],
                    payload=payload, command_id=oracle['command_id'])
            observed = "committed"
        except (ValueError, PermissionError) as failure:
            observed, error = "rejected", type(failure).__name__ + ": " + str(failure)
        if observed == "committed":
            after = provider.query(resource_type, filters={}, search="", sort=[], limit=None)
            expected_records = copy.deepcopy(before)
            expected_records[0].update(payload)
            if "revision" in expected_records[0]:
                expected_records[0]["revision"] += 1
            preservation = after == expected_records
        elif before is not None:
            preservation = provider.query(resource_type, filters={}, search="", sort=[], limit=None) == before
        else:
            preservation = True
        row = {"episode": episode, "expected": expected, "observed": observed, "ok": observed == expected and preservation,
               "preservation": preservation, "error": error, "binding": resource_type,
               "binding_substitution": targets or None,
               "cost": {"llm_calls": 0, "usd": 0}}
        rows.append(row)
    return rows
