from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import operations as operations_api
from adaos.apps.api.auth import require_token
from adaos.services.agent_context import get_ctx


class _FakeManager:
    def snapshot(self, *, webspace_id=None):
        return {"webspace_id": webspace_id, "by_id": {}, "order": [], "active": []}

    def operation(self, operation_id: str):
        if operation_id == "missing":
            raise KeyError(operation_id)
        return {"operation_id": operation_id, "status": "failed", "can_retry": True}

    def cancel_operation(self, operation_id: str):
        if operation_id == "terminal":
            raise ValueError("operation_not_active")
        return {"operation_id": operation_id, "status": "cancelling"}


def _client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(operations_api.router, prefix="/api/operations")
    app.dependency_overrides[require_token] = lambda: "test-token"
    app.dependency_overrides[get_ctx] = lambda: object()
    manager = _FakeManager()
    monkeypatch.setattr(operations_api, "get_operation_manager", lambda ctx: manager)
    monkeypatch.setattr(
        operations_api,
        "retry_operation",
        lambda operation_id, ctx: {
            "operation_id": "retry-1",
            "retry_of": operation_id,
            "attempt": 2,
            "status": "accepted",
        },
    )
    return TestClient(app)


def test_operations_api_exposes_snapshot_and_operation_detail(monkeypatch) -> None:
    client = _client(monkeypatch)

    listing = client.get("/api/operations", params={"webspace_id": "desktop"})
    detail = client.get("/api/operations/op-1")
    missing = client.get("/api/operations/missing")

    assert listing.status_code == 200
    assert listing.json()["webspace_id"] == "desktop"
    assert detail.json()["operation_id"] == "op-1"
    assert missing.status_code == 404
    assert missing.json()["detail"] == "operation_not_found"


def test_operations_api_maps_cancel_conflict_and_retries(monkeypatch) -> None:
    client = _client(monkeypatch)

    cancelling = client.post("/api/operations/op-1/cancel")
    conflict = client.post("/api/operations/terminal/cancel")
    retried = client.post("/api/operations/op-1/retry")

    assert cancelling.status_code == 202
    assert cancelling.json()["status"] == "cancelling"
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "operation_not_active"
    assert retried.status_code == 202
    assert retried.json()["retry_of"] == "op-1"
