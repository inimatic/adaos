from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from adaos.apps.api import resources as resources_api
from adaos.services.development_tickets import DevelopmentTicketService
from adaos.services.resources import ResourceAccessDenied, ResourceConflict, ResourceWorkbenchService


def _client(service: ResourceWorkbenchService) -> TestClient:
    app = FastAPI()
    app.include_router(resources_api.router, prefix="/api/resources")
    app.dependency_overrides[resources_api._get_service] = lambda: service
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-AdaOS-Token": "dev-local-token"}


def test_resource_definitions_validate_and_include_dev_tickets_and_demo_metrics(tmp_path: Path) -> None:
    service = ResourceWorkbenchService(state_dir=tmp_path)
    schema_path = Path(__file__).parents[1] / "src" / "adaos" / "abi" / "resource.definition.v1.schema.json"
    schema = schema_path.read_text(encoding="utf-8")
    validator = Draft202012Validator(__import__("json").loads(schema))

    definitions = service.definitions()
    resource_types = {item["resource_type"] for item in definitions}
    dev_ticket = next(item for item in definitions if item["resource_type"] == "adaos.dev.ticket")

    assert {"adaos.dev.ticket", "demo.metric", "demo.metric_note", "demo.metric_event"} <= resource_types
    assert {"owner_area", "component_ref"} <= set(dev_ticket["query"]["filters"])
    assert {"core_request", "sdk_understanding"} <= {item["id"] for item in dev_ticket["operations"]}
    for definition in definitions:
        validator.validate(definition)
        assert definition["i18n"]["default_locale"] == "en"
        assert "access" in definition
        assert "privacy" in definition


def test_resource_workbench_queries_and_operates_dev_ticket_lifecycle(tmp_path: Path) -> None:
    tickets = DevelopmentTicketService(state_dir=tmp_path)
    workbench = ResourceWorkbenchService(state_dir=tmp_path, ticket_service=tickets)

    created = workbench.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "adaos.dev.ticket",
            "operation_id": "create",
            "payload": {
                "kind": "review_debt",
                "summary": "Check modal action order",
                "target_scope": {
                    "type": "modal",
                    "id": "media_center_player",
                    "project_ref": "project:media_center",
                    "scenario_ref": "scenario:media_center",
                    "skill_ref": "skill:media_center",
                },
            },
            "actor": {"id": "codex:test", "role": "owner"},
        }
    )
    ticket_id = created["result"]["ticket"]["ticket_id"]

    listed = workbench.query(
        {
            "schema": "adaos.resource.query.v1",
            "resource_type": "adaos.dev.ticket",
            "filters": {"status_group": "open", "project_id": "project:media_center"},
            "search": "action order",
            "actor": {"id": "codex:test", "role": "owner"},
        }
    )
    assert [item["ticket_id"] for item in listed["items"]] == [ticket_id]
    assert listed["trace"]["result"]["count"] == 1

    workbench.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "adaos.dev.ticket",
            "operation_id": "claim",
            "record_id": ticket_id,
            "payload": {"owner": "codex"},
            "actor": {"id": "codex:test", "role": "owner"},
        }
    )
    resolved = workbench.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "adaos.dev.ticket",
            "operation_id": "resolve",
            "record_id": ticket_id,
            "evidence_refs": [{"type": "test", "id": "tests/test_resource_workbench.py", "status": "passed"}],
            "actor": {"id": "builder:test", "role": "owner"},
        }
    )
    assert resolved["result"]["ticket"]["status"] == "resolved"

    verified = workbench.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "adaos.dev.ticket",
            "operation_id": "verify",
            "record_id": ticket_id,
            "evidence_refs": [{"type": "runtime_guard", "id": "manual_acceptance", "status": "passed"}],
            "actor": {"id": "human:test", "role": "owner"},
        }
    )
    assert verified["result"]["ticket"]["status"] == "verified"


def test_resource_workbench_core_and_sdk_ticket_routes(tmp_path: Path) -> None:
    tickets = DevelopmentTicketService(state_dir=tmp_path)
    workbench = ResourceWorkbenchService(state_dir=tmp_path, ticket_service=tickets)

    project = workbench.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "adaos.dev.ticket",
            "operation_id": "create",
            "payload": {
                "kind": "development_request",
                "summary": "Builder needs a clearer artifact contract",
                "owner_area": "project",
                "component_ref": "scenario:demo.modal:ticket",
                "target_scope": {
                    "type": "scenario",
                    "id": "demo",
                    "component_ref": "scenario:demo.modal:ticket",
                },
            },
            "actor": {"id": "codex:test", "role": "owner"},
        }
    )
    project_ticket_id = project["result"]["ticket"]["ticket_id"]

    core = workbench.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "adaos.dev.ticket",
            "operation_id": "core_request",
            "record_id": project_ticket_id,
            "payload": {
                "summary": "Expose ticket artifact helpers",
                "component_ref": "core:sdk",
                "desired_contract": "Provide typed Dev Ticket artifact open/read helper methods.",
                "impact": "blocker",
            },
            "evidence_refs": [{"type": "trace", "id": "artifact.helper.missing"}],
            "actor": {"id": "builder:test", "role": "owner"},
        }
    )
    assert core["result"]["ticket"]["owner_area"] == "core"
    assert core["result"]["blocked_tickets"][0]["status"] == "waiting_for_core"

    sdk = workbench.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "adaos.dev.ticket",
            "operation_id": "sdk_understanding",
            "record_id": project_ticket_id,
            "payload": {
                "kind": "sdk_unclear_definition",
                "summary": "Artifact helper behavior is unclear",
                "method_ref": "dev_ticket.artifact",
                "diagnosis": "sdk_doc_ambiguity",
            },
            "actor": {"id": "builder:test", "role": "owner"},
        }
    )
    assert sdk["result"]["ticket"]["owner_area"] == "sdk"

    listed = workbench.query(
        {
            "schema": "adaos.resource.query.v1",
            "resource_type": "adaos.dev.ticket",
            "filters": {"owner_area": "core", "component_ref": "core:sdk"},
            "actor": {"id": "codex:test", "role": "owner"},
        }
    )
    assert [item["ticket_id"] for item in listed["items"]] == [core["result"]["ticket"]["ticket_id"]]


def test_resource_workbench_demo_metric_note_crud_validation_roles_and_traces(tmp_path: Path) -> None:
    service = ResourceWorkbenchService(state_dir=tmp_path)

    with __import__("pytest").raises(ResourceAccessDenied):
        service.operate(
            {
                "schema": "adaos.resource.operation.v1",
                "resource_type": "demo.metric_note",
                "operation_id": "create",
                "payload": {"metric_id": "cpu", "title": "Guest write"},
                "actor": {"id": "guest:test", "role": "guest"},
            }
        )

    with __import__("pytest").raises(ValueError, match="title"):
        service.operate(
            {
                "schema": "adaos.resource.operation.v1",
                "resource_type": "demo.metric_note",
                "operation_id": "create",
                "payload": {"metric_id": "cpu", "title": ""},
                "actor": {"id": "owner:test", "role": "owner"},
            }
        )

    created = service.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "demo.metric_note",
            "operation_id": "create",
            "payload": {"metric_id": "memory", "title": "Check memory pressure", "body": "demo"},
            "actor": {"id": "owner:test", "role": "owner"},
        }
    )
    note = created["result"]["record"]

    with __import__("pytest").raises(ResourceConflict):
        service.operate(
            {
                "schema": "adaos.resource.operation.v1",
                "resource_type": "demo.metric_note",
                "operation_id": "update",
                "record_id": note["id"],
                "expected_revision": 0,
                "payload": {"title": "stale"},
                "actor": {"id": "owner:test", "role": "owner"},
            }
        )

    updated = service.operate(
        {
            "schema": "adaos.resource.operation.v1",
            "resource_type": "demo.metric_note",
            "operation_id": "update",
            "record_id": note["id"],
            "expected_revision": note["revision"],
            "payload": {"title": "Memory pressure acknowledged"},
            "actor": {"id": "owner:test", "role": "owner"},
        }
    )

    assert updated["result"]["record"]["revision"] == 2
    assert service.traces(resource_type="demo.metric_note")
    assert service.events(resource_type="demo.metric_note")


def test_resource_api_query_and_operation(tmp_path: Path) -> None:
    service = ResourceWorkbenchService(state_dir=tmp_path)
    client = _client(service)

    definitions = client.get("/api/resources/definitions", headers=_headers())
    assert definitions.status_code == 200
    assert any(item["resource_type"] == "demo.metric_note" for item in definitions.json()["items"])

    created = client.post(
        "/api/resources/operate",
        headers=_headers(),
        json={
            "resource_type": "demo.metric_note",
            "operation_id": "create",
            "payload": {"metric_id": "queue", "title": "Queue is stable"},
            "actor": {"id": "owner:test", "role": "owner"},
        },
    )
    assert created.status_code == 200, created.text

    queried = client.post(
        "/api/resources/query",
        headers=_headers(),
        json={
            "resource_type": "demo.metric_note",
            "filters": {"metric_id": "queue"},
            "search": "stable",
            "actor": {"id": "owner:test", "role": "owner"},
        },
    )
    assert queried.status_code == 200, queried.text
    assert queried.json()["count"] == 1

    unsupported = client.post(
        "/api/resources/query",
        headers=_headers(),
        json={"resource_type": "demo.metric_note", "filters": {"unknown": "x"}},
    )
    assert unsupported.status_code == 400
