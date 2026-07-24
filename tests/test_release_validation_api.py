from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import release_validation as validation_api
from adaos.apps.api.auth import require_token
from adaos.services.agent_context import get_ctx


class _Bus:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _Service:
    def __init__(self) -> None:
        self.nodes = []

    def snapshot(self):
        return {"mode": "observe-only", "nodes": self.nodes}

    def register_node(self, node):
        value = node.to_dict(public=True)
        self.nodes.append(value)
        return value

    def register_suite(self, suite):
        return suite.to_dict()

    def create_campaign(self, campaign):
        return {**campaign.to_dict(), "assignments": []}

    def campaign(self, campaign_id):
        if campaign_id == "missing":
            raise KeyError("campaign_not_found")
        return {"campaign_id": campaign_id, "state": "pending"}

    def assignment(self, assignment_id):
        return {"assignment_id": assignment_id, "state": "passed"}

    def run_campaign(self, campaign_id):
        return {
            "campaign_id": campaign_id,
            "target_build": "abc123",
            "state": "failed",
            "result": {"passed": 0, "failed": 1, "inconclusive": 0, "timed_out": 0},
        }


def _client(monkeypatch):
    app = FastAPI()
    app.include_router(validation_api.router, prefix="/api/release-validation")
    app.dependency_overrides[require_token] = lambda: "test-token"
    bus = _Bus()
    app.dependency_overrides[get_ctx] = lambda: SimpleNamespace(bus=bus)
    service = _Service()
    monkeypatch.setattr(validation_api, "get_release_validation_service", lambda: service)
    return TestClient(app), service, bus


def test_release_validation_api_registers_observe_contracts(monkeypatch) -> None:
    client, service, _bus = _client(monkeypatch)

    node = client.post(
        "/api/release-validation/nodes",
        json={
            "node_id": "linux-exp-01",
            "display_name": "Linux experimental node",
            "host": "192.168.0.30",
            "identity_file": "d:/private/key",
        },
    )
    suite = client.post(
        "/api/release-validation/suites",
        json={"suite_id": "observe-smoke", "version": "1", "display_name": "Observe smoke"},
    )
    campaign = client.post(
        "/api/release-validation/campaigns",
        json={"suite_id": "observe-smoke", "target_build": "abc123", "node_ids": ["linux-exp-01"]},
    )

    assert node.status_code == 201
    assert node.json()["identity_file"] == "<configured>"
    assert suite.status_code == 201
    assert suite.json()["profile"] == "observe"
    assert campaign.status_code == 201
    assert campaign.json()["state"] == "pending"
    assert service.nodes[0]["allowed_profiles"] == ("observe",)


def test_release_validation_api_allows_latest_installed_campaign(monkeypatch) -> None:
    client, _service, _bus = _client(monkeypatch)

    response = client.post(
        "/api/release-validation/campaigns",
        json={"suite_id": "observe-smoke", "node_ids": ["linux-exp-01"]},
    )

    assert response.status_code == 201
    assert response.json()["target_build"] == ""
    assert response.json()["target_policy"] == "latest_installed"


def test_release_validation_api_notifies_after_terminal_run(monkeypatch) -> None:
    client, _service, bus = _client(monkeypatch)

    response = client.post("/api/release-validation/campaigns/manual-01/run")

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert len(bus.events) == 1
    event = bus.events[0]
    assert event.type == "ui.notify"
    assert event.payload["_meta"]["severity"] == "critical"
    assert "manual-01" in event.payload["text"]


def test_release_validation_api_maps_missing_campaign(monkeypatch) -> None:
    client, _service, _bus = _client(monkeypatch)

    response = client.get("/api/release-validation/campaigns/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "campaign_not_found"
