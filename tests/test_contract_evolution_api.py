from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api.auth import require_token
from adaos.apps.api.contract_evolution import router
from adaos.services.agent_context import get_ctx
from adaos.services.applications.conditions import application_condition_projection
from adaos.services.contract_evolution.records import digest, seal
from adaos.services.contract_evolution.service import ContractEvolutionService, USAGE


def test_authenticated_read_model_exposes_exact_consumer_and_does_not_write(tmp_path: Path):
    service = ContractEvolutionService(tmp_path)
    change = service.publish_change(contract_ref="sdk:example.read", contract_kind="sdk_operation", owner_ref="core:sdk",
        before=digest("old"), after=digest("new"), classification="breaking")
    usage = service.register_usage(seal(USAGE, application_ref="application:demo", consumer_ref="scenario:demo",
        consumer_digest=digest("release"), producer_digest=digest("compiler"), complete=True, unresolved=[],
        edges=[{"contract_ref": change["contract_ref"], "contract_digest": change["before"], "surface": ["read"], "exposure": "compiled"}]))
    wave = service.create_wave([change["change_id"]], owner_ref="core:sdk")
    assessment = service.assess(wave_id=wave["wave_id"], usage_lock_id=usage["usage_lock_id"])[0]
    service.materialize_ticket(assessment["assessment_id"])
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_ctx] = lambda: SimpleNamespace(authority_state_dir=tmp_path)
    app.dependency_overrides[require_token] = lambda: None
    before = service.store.path.read_bytes()
    with TestClient(app) as client:
        response = client.get("/api/v1/contract-evolution/applications/demo", params={"consumer_digest": usage["consumer_digest"]})
        assert response.status_code == 200
        posture = response.json()
        assert posture["posture"] == "action_required"
        assert posture["ticket_count"] == 1
        unrelated = client.get("/api/v1/contract-evolution/applications/demo", params={"consumer_digest": digest("other-release")}).json()
        assert unrelated["posture"] == "unknown"
        assert unrelated["assessment_count"] == 0
        assert client.get("/api/v1/contract-evolution/applications/demo").json()["posture"] == "unknown"
        assert client.get("/api/v1/contract-evolution/events", params={"after": -1}).status_code == 422
        assert client.get("/api/v1/contract-evolution/waves").json()["items"][0]["assessed_edges"] == 1
    assert service.store.path.read_bytes() == before
    model = {"installed": True, "available": True, "installation": {"status": "active"},
             "update_available": False, "runtime_revisions": {"status": "aligned"}, "contract_evolution": posture}
    projection = application_condition_projection(model)
    conditions = {item["type"]: item for item in projection["conditions"]}
    assert conditions["ReleaseCurrent"]["status"] == "True"
    assert conditions["RuntimeIntegrity"]["status"] == "True"
    assert conditions["ContractAligned"]["status"] == "False"
    assert conditions["CompatibilityWork"]["status"] == "True"
    assert projection["attention"]["status"] == "action_required"


def test_missing_owner_credential_cannot_read_evolution_records(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_TOKEN", "isolated-cef-test-owner")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_ctx] = lambda: SimpleNamespace(authority_state_dir=tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/contract-evolution/events").status_code == 401
    assert not (tmp_path / "contract_evolution").exists()


def test_available_migrator_is_not_an_executed_verification():
    model = {"contract_evolution": {"posture": "migration_ready", "ticket_count": 0}}
    conditions = {item["type"]: item for item in application_condition_projection(model)["conditions"]}
    assert conditions["VerificationFresh"]["status"] == "False"
    model["contract_evolution"] = {"posture": "unknown", "ticket_count": 0, "reason": "AssessmentQueryLimitExceeded"}
    conditions = {item["type"]: item for item in application_condition_projection(model)["conditions"]}
    assert conditions["CompatibilityWork"]["status"] == "Unknown"
