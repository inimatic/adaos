from __future__ import annotations

import copy
from pathlib import Path

import pytest

from adaos.sdk.developer import prototypes as developer_prototypes
from adaos.sdk.developer.prototypes import derive_board_resource_spec
from adaos.services.resources.prototype import prototype_webui_digest
from adaos.services.resources import (
    PrototypeResourceService,
    ResourceConflict,
    ResourceWorkbenchService,
)


def _record_schema() -> dict:
    return {
        "type": "object",
        "required": ["id", "title", "status", "revision"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "status": {"enum": ["planned", "doing", "done"]},
            "priority": {"enum": ["low", "medium", "high"]},
            "revision": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    }


def _activity(activity_id: str, operation: str, input_schema: dict, output_schema: dict) -> dict:
    return {
        "activity_id": activity_id,
        "operation": operation,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "side_effect_class": "read_only" if operation in {"list", "get"} else "local_reversible",
        "implementation_status": "prototype_only",
        "implementation_ref": None,
    }


def _bundle(*, revision: str = "ui-001") -> dict:
    record_schema = _record_schema()
    data_definition = {
        "schema": "adaos.builder.prototype_data.v1",
        "source_id": "kanban.cards",
        "mode": "local_crud",
        "record_schema": record_schema,
        "seed": [
            {"id": "one", "title": "Plan release", "status": "planned", "priority": "high", "revision": 1},
            {"id": "two", "title": "Review UI", "status": "doing", "priority": "medium", "revision": 1},
        ],
        "activities": [
            _activity(
                "list",
                "list",
                {"type": "object", "additionalProperties": False},
                {"type": "array", "items": record_schema},
            ),
            _activity(
                "show",
                "get",
                {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                    "additionalProperties": False,
                },
                {"oneOf": [record_schema, {"type": "null"}]},
            ),
            _activity(
                "create",
                "create",
                {
                    "type": "object",
                    "required": ["record"],
                    "properties": {"record": record_schema},
                    "additionalProperties": False,
                },
                record_schema,
            ),
            _activity(
                "update",
                "update",
                {
                    "type": "object",
                    "required": ["id", "patch"],
                    "properties": {
                        "id": {"type": "string"},
                        "patch": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                record_schema,
            ),
            _activity(
                "delete",
                "delete",
                {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                    "additionalProperties": False,
                },
                record_schema,
            ),
            _activity(
                "reset",
                "reset",
                {"type": "object", "additionalProperties": False},
                {"type": "array", "items": record_schema},
            ),
        ],
    }
    definition = {
        "schema": "adaos.resource.definition.v1",
        "resource_type": "prototype.kanban.cards",
        "version": "0.0.0-prototype",
        "title": "Kanban cards",
        "description": "Disposable cards for Builder Preview.",
        "scope": {"owner": "project:kanban", "target_refs": ["project:kanban"]},
        "authority": {
            "provider": "prototype",
            "binding": "kanban.cards",
            "writes": "local_reversible",
            "source_of_truth": "builder_preview",
        },
        "record_schema_ref": "inline:prototype.kanban.cards",
        "record_schema": record_schema,
        "query": {
            "default": "all",
            "filters": ["id", "status", "priority", "search"],
            "sort": ["title", "priority"],
            "cursor": False,
            "include": [],
        },
        "operations": [
            {"id": "list", "kind": "list", "risk": "read"},
            {"id": "show", "kind": "show", "risk": "read"},
            {"id": "create", "kind": "create", "risk": "low"},
            {"id": "update", "kind": "update", "risk": "low"},
            {"id": "delete", "kind": "delete", "risk": "medium"},
            {"id": "reset", "kind": "reset", "risk": "low"},
        ],
        "views": [
            {"id": "board", "kind": "board", "title": "Board"},
            {"id": "detail", "kind": "detail", "title": "Card"},
            {"id": "form", "kind": "form", "title": "Card form"},
        ],
        "events": {"emits": ["resource.record.created", "resource.record.updated", "resource.record.deleted"]},
        "i18n": {"default_locale": "en", "locales": ["en", "ru"]},
        "access": {
            "role_fixtures": {
                "owner": {"create": "allowed", "update": "allowed", "delete": "allowed", "reset": "allowed"},
                "guest": {"create": "denied", "update": "denied", "delete": "denied", "reset": "denied"},
            }
        },
        "privacy": {"sensitivity": "synthetic", "retention": "preview", "external_export": "denied"},
        "readiness": {"states": ["ready", "empty", "validation_error"]},
    }
    return {
        "schema": "adaos.builder.prototype_resource.v1",
        "project_ref": "project:kanban",
        "change_id": "change-kanban",
        "revision": revision,
        "webui_digest": "sha256:" + ("1" if revision == "ui-001" else "2") * 64,
        "resource_definition": definition,
        "data_definition": data_definition,
    }


def _query(service: ResourceWorkbenchService, **values) -> dict:
    return service.query(
        {
            "schema": "adaos.resource.query.v1",
            "resource_type": "prototype.kanban.cards",
            "actor": {"id": "reviewer:test", "role": "owner"},
            **values,
        }
    )


def _operate(service: ResourceWorkbenchService, operation_id: str, **values) -> dict:
    return service.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "prototype.kanban.cards",
            "operation_id": operation_id,
            "actor": {"id": "reviewer:test", "role": "owner"},
            **values,
        }
    )


def test_prototype_resource_runs_generic_query_and_crud_across_service_instances(tmp_path: Path) -> None:
    prototypes = PrototypeResourceService(state_dir=tmp_path)
    first = prototypes.materialize(_bundle())
    duplicate = prototypes.materialize(_bundle())

    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True

    workbench = ResourceWorkbenchService(state_dir=tmp_path)
    definition = workbench.definition("prototype.kanban.cards")
    assert definition is not None
    assert definition["metadata"]["revision"] == "ui-001"
    assert definition["authority"]["source_of_truth"] == "builder_preview"

    filtered = _query(
        workbench,
        filters={"status": "planned"},
        search="release",
        sort=[{"field": "title", "direction": "asc"}],
    )
    assert [item["id"] for item in filtered["items"]] == ["one"]

    created = _operate(
        workbench,
        "create",
        payload={"title": "Test prototype", "status": "planned", "priority": "low"},
    )["result"]["record"]
    assert created["id"].startswith("prec.")
    assert created["revision"] == 1

    updated = _operate(
        ResourceWorkbenchService(state_dir=tmp_path),
        "update",
        record_id=created["id"],
        expected_revision=1,
        payload={"status": "doing"},
    )["result"]["record"]
    assert updated["status"] == "doing"
    assert updated["revision"] == 2

    with pytest.raises(ResourceConflict, match="revision conflict"):
        _operate(
            workbench,
            "update",
            record_id=created["id"],
            expected_revision=1,
            payload={"status": "done"},
        )

    deleted = _operate(
        workbench,
        "delete",
        record_id=created["id"],
        expected_revision=2,
    )
    assert deleted["result"]["deleted"] is True
    assert all(item["id"] != created["id"] for item in _query(workbench)["items"])


def test_new_prototype_revision_replaces_disposable_mutations(tmp_path: Path) -> None:
    prototypes = PrototypeResourceService(state_dir=tmp_path)
    prototypes.materialize(_bundle())
    workbench = ResourceWorkbenchService(state_dir=tmp_path)
    _operate(
        workbench,
        "update",
        record_id="one",
        expected_revision=1,
        payload={"status": "done"},
    )

    rematerialized = prototypes.materialize(_bundle(revision="ui-002"))

    assert rematerialized["duplicate"] is False
    records = _query(ResourceWorkbenchService(state_dir=tmp_path))["items"]
    assert next(item for item in records if item["id"] == "one")["status"] == "planned"


def test_prototype_resource_rejects_durable_or_mismatched_authority(tmp_path: Path) -> None:
    service = PrototypeResourceService(state_dir=tmp_path)
    durable = copy.deepcopy(_bundle())
    durable["resource_definition"]["resource_type"] = "kanban.cards"
    with pytest.raises(ValueError, match="must start"):
        service.materialize(durable)

    mismatch = copy.deepcopy(_bundle())
    mismatch["resource_definition"]["authority"]["binding"] = "other.cards"
    with pytest.raises(ValueError, match="must equal"):
        service.materialize(mismatch)


def test_board_projection_derives_typed_disposable_resource(tmp_path: Path) -> None:
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "kanban",
                        "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [
                            {
                                "id": "cards",
                                "type": "collection.board",
                                "area": "main",
                                "title": "Delivery board",
                                "inputs": {
                                    "lanes": [
                                        {"id": "planned", "label": "Planned"},
                                        {"id": "done", "label": "Done"},
                                    ],
                                    "laneKey": "status",
                                    "titleKey": "title",
                                    "dragDrop": True,
                                },
                                "dataSource": {
                                    "kind": "resourceQuery",
                                    "resourceType": "prototype.delivery.cards",
                                    "queryId": "all",
                                },
                                "actions": [
                                    {
                                        "on": "move",
                                        "type": "resourceOperation",
                                        "target": "prototype.delivery.cards",
                                        "params": {"operation_id": "update"},
                                    },
                                    {
                                        "on": "add",
                                        "type": "openModal",
                                        "params": {"modalId": "create-card"},
                                    },
                                    {
                                        "on": "delete",
                                        "type": "resourceOperation",
                                        "target": "prototype.delivery.cards",
                                        "params": {"operation_id": "delete"},
                                    },
                                ],
                            }
                        ],
                    }
                },
                "modals": {
                    "create-card": {
                        "id": "create-card",
                        "schema": {
                            "id": "create-card",
                            "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
                            "widgets": [
                                {
                                    "id": "create-card-form",
                                    "type": "ui.form",
                                    "area": "main",
                                    "actions": [
                                        {
                                            "on": "submit",
                                            "type": "resourceOperation",
                                            "target": "prototype.delivery.cards",
                                            "params": {"operation_id": "create", "payload": "$event.values"},
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                },
            }
        },
    }
    spec = derive_board_resource_spec(
        webui,
        [
            {"id": "one", "title": "Plan release", "status": "planned", "priority": "high"},
            {"id": "two", "title": "Ship release", "status": "done", "priority": "medium"},
        ],
    )

    definition = spec["resource_definition"]
    assert definition["resource_type"] == "prototype.delivery.cards"
    assert {item["id"] for item in definition["operations"]} == {
        "list",
        "show",
        "create",
        "update",
        "delete",
    }
    assert definition["record_schema"]["properties"]["status"]["enum"] == [
        "planned",
        "done",
    ]
    service = PrototypeResourceService(state_dir=tmp_path)
    materialized = service.materialize(
        {
            "schema": "adaos.builder.prototype_resource.v1",
            "project_ref": "project:delivery",
            "change_id": "change-delivery",
            "revision": "003",
            "webui_digest": "sha256:" + "3" * 64,
            **spec,
        }
    )
    assert materialized["state"]["records"][0]["revision"] == 1
    snapshots = service.acceptance_snapshots(
        project_ref="project:delivery",
        change_id="change-delivery",
        revision="003",
        webui_digest="sha256:" + "3" * 64,
        resource_types=["prototype.delivery.cards"],
    )
    assert snapshots[0]["record_count"] == 2
    assert snapshots[0]["records"] == materialized["state"]["records"]
    assert snapshots[0]["records_digest"].startswith("sha256:")


def test_materialize_resources_stamps_authoritative_revision_identity(monkeypatch) -> None:
    captured: list[dict] = []

    class _PrototypeService:
        def materialize(self, payload):
            captured.append(copy.deepcopy(dict(payload)))
            return {
                "duplicate": False,
                "state": {
                    "resource_type": payload["resource_definition"]["resource_type"],
                    "bundle_digest": "sha256:" + "b" * 64,
                    "generation": 1,
                },
            }

    monkeypatch.setattr(
        developer_prototypes,
        "PrototypeResourceService",
        _PrototypeService,
    )
    webui = {"schema": "adaos.webui.v1", "ui": {"application": {}}}
    spec = {
        "resource_definition": {"resource_type": "prototype.cards"},
        "data_definition": {"source_id": "cards"},
    }

    result = developer_prototypes.materialize_resources(
        project_ref="project:kanban",
        change_id="change-1",
        revision="007",
        webui=webui,
        resources=[spec],
    )

    assert captured[0]["project_ref"] == "project:kanban"
    assert captured[0]["change_id"] == "change-1"
    assert captured[0]["revision"] == "007"
    assert captured[0]["webui_digest"] == prototype_webui_digest(webui)
    assert result["webui_digest"] == captured[0]["webui_digest"]


def test_prototype_webui_digest_ignores_release_version_only() -> None:
    first = {
        "schema": "adaos.webui.v1",
        "ui": {"version": "0.1.0", "application": {"desktop": {"pageSchema": {"widgets": []}}}},
    }
    bumped = copy.deepcopy(first)
    bumped["ui"]["version"] = "0.1.1"
    changed = copy.deepcopy(bumped)
    changed["ui"]["application"]["desktop"]["pageSchema"]["widgets"].append(
        {"id": "board", "type": "collection.board"}
    )

    assert prototype_webui_digest(first) == prototype_webui_digest(bumped)
    assert prototype_webui_digest(first) != prototype_webui_digest(changed)
