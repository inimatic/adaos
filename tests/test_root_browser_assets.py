from __future__ import annotations

from types import SimpleNamespace

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
    assert payload["contract"]["state"] == "partial"
    assert payload["contract"]["plannedEndpoints"]["ensureBlob"] == "/v1/root/browser-assets/cache/ensure"
    assert payload["contract"]["privateResources"] == "deferred"


def test_root_browser_assets_cache_reads_local_static_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "owner-token")
    from adaos.apps.api import root_endpoints
    from adaos.services.browser_assets import publish_system_resource_descriptors

    runtime_base = tmp_path / "runtime"
    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: runtime_base))
    monkeypatch.setattr(root_endpoints, "get_ctx", lambda: fake_ctx)
    published = publish_system_resource_descriptors(base_dir=runtime_base)
    descriptor = published["published"]["assistant.default.avatar"]
    digest = descriptor["cacheKey"].split(":", 1)[1]

    app = FastAPI()
    app.include_router(root_endpoints.router)
    client = TestClient(app)
    headers = {"X-Owner-Token": "owner-token"}

    ensure = client.post(
        "/v1/root/browser-assets/cache/ensure",
        json={"cacheKey": descriptor["cacheKey"]},
        headers=headers,
    )
    assert ensure.status_code == 200
    ensure_payload = ensure.json()
    assert ensure_payload["present"] is True
    assert ensure_payload["deliveryUrl"] == descriptor["url"]
    assert ensure_payload["byteServing"] == "static:/assets"

    redirect = client.get(
        f"/v1/root/browser-assets/blobs/sha256/{digest}",
        headers=headers,
        follow_redirects=False,
    )
    assert redirect.status_code == 307
    assert redirect.headers["location"] == descriptor["url"]

    manifest = client.get(
        "/v1/root/browser-assets/manifests/local/system/adaos-core",
        headers=headers,
    )
    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["manifest"]["resources"]["assistant.default.avatar"]["cacheKey"] == descriptor["cacheKey"]
