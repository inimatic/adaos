from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from adaos.apps.api import companion_lab
from adaos.services.companion.ledger import Ledger


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_COMPANION_SAGE_ENABLED", "true")
    ledger = Ledger(tmp_path / "lab.sqlite3")
    monkeypatch.setattr(companion_lab, "Ledger", lambda: ledger)
    monkeypatch.setattr(companion_lab, "actor", lambda request: "user:owner")
    app = FastAPI()
    app.dependency_overrides[companion_lab.require_token] = lambda: None
    app.include_router(companion_lab.router)
    session = ledger.start("test:user:owner", {"catalog": "v1"}, actor="user:owner")["session_id"]
    ledger.append(session, "turn", {"text": "test"}, turn="t")
    return TestClient(app), ledger, session


def test_api_seal_late_feedback_workbench(lab):
    client, ledger, session = lab
    prefix = "/sessions/" + session
    assert client.get(prefix+"/workbench").status_code == 409
    sealed = client.post(prefix+"/seal").json()
    assert client.post(prefix+"/feedback", json={"turn_id": "t", "label": "too_slow", "correction": "delay"}).status_code == 200
    assert client.post(prefix+"/review", json={"target": "t", "kind": "anomaly", "payload": {"classification": "latency_anomaly", "decision": "backlog"}}).status_code == 200
    assert ledger.bundle(session)["digest"] == sealed["digest"]
    case = client.get(prefix+"/workbench").json()["cases"][0]
    assert case["classification"] == "latency_anomaly" and not case["review_required"]


def test_api_owner_and_explicit_enablement(lab, monkeypatch):
    client, ledger, session = lab
    other = ledger.start("test:user:other", {}, actor="user:other")["session_id"]
    assert client.get("/sessions/"+other).status_code == 404
    monkeypatch.delenv("ADAOS_COMPANION_SAGE_ENABLED")
    assert client.get("/sessions/"+session).status_code == 404


def test_api_rejects_fabricated_turn_and_model_promotion(lab):
    client, ledger, session = lab
    assert client.post(f"/sessions/{session}/feedback", json={"turn_id": "fake", "label": "correct"}).status_code == 422
    ledger.seal(session)
    assert client.post(f"/sessions/{session}/review", json={"target": "t", "kind": "hypothesis", "payload": {"status": "accepted_canonical"}}).status_code == 409
