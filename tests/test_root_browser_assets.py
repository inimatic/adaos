from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_root_browser_assets_cache_contract(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "owner-token")
    from adaos.apps.api import root_endpoints

    app = FastAPI()
    app.include_router(root_endpoints.router)
    client = TestClient(app)

    response = client.get(
        "/v1/root/browser-assets/cache-contract",
        headers={"X-Owner-Token": "owner-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["contract"]["schema"] == "adaos.root.browser_assets.cache_contract.v1"
    assert payload["contract"]["state"] == "planned"
    assert payload["contract"]["plannedEndpoints"]["ensureBlob"] == "/v1/root/browser-assets/cache/ensure"
    assert payload["contract"]["privateResources"] == "deferred"
