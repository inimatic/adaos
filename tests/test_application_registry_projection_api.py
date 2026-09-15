from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import application_registry_projection as registry_api
from adaos.apps.api.auth import require_token
from adaos.services.application_registry_projection import ApplicationRegistryProjection


def test_application_registry_projection_diagnostics_api_uses_runtime_state(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    service = ApplicationRegistryProjection(state_dir)
    epoch = service.start_epoch(runtime_instance_id="runtime.api")
    service.seal_epoch(epoch["epoch_id"], shutdown_request_id="shutdown.api")
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: str(state_dir)))

    app = FastAPI()
    app.include_router(registry_api.router, prefix="/api/application-registry")
    app.dependency_overrides[require_token] = lambda: None
    app.dependency_overrides[registry_api.get_ctx] = lambda: ctx
    client = TestClient(app)

    response = client.get("/api/application-registry/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "adaos.application.registry_projection.diagnostics.v1"
    assert payload["trusted_snapshot"]["trusted_snapshot"] is True
    assert payload["epoch"]["epoch_id"] == epoch["epoch_id"]
    assert payload["seal_receipt"]["shutdown_request_id"] == "shutdown.api"
    assert payload["query_sources"]["application_installed_summaries"] == "sqlite_projection"
