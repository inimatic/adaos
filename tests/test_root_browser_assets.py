from __future__ import annotations

import hashlib
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


def test_root_browser_assets_cache_ensure_pulls_source(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "owner-token")
    from adaos.apps.api import root_endpoints

    runtime_base = tmp_path / "runtime"
    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: runtime_base))
    monkeypatch.setattr(root_endpoints, "get_ctx", lambda: fake_ctx)
    data = b"<svg><title>Remote</title></svg>"
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(root_endpoints, "_fetch_public_browser_asset_source", lambda _url: (data, "image/svg+xml"))

    app = FastAPI()
    app.include_router(root_endpoints.router)
    client = TestClient(app)

    response = client.post(
        "/v1/root/browser-assets/cache/ensure",
        json={
            "cacheKey": f"sha256:{digest}",
            "sourceUrl": "https://member.example/assets/remote.svg",
            "filename": "remote.svg",
            "sizeBytes": len(data),
        },
        headers={"X-Owner-Token": "owner-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["present"] is True
    assert payload["pulled"] is True
    assert payload["deliveryUrl"].endswith(f"/{digest}/remote.svg")


def test_root_browser_assets_cache_ensure_rejects_private_source(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "owner-token")
    from adaos.apps.api import root_endpoints

    runtime_base = tmp_path / "runtime"
    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: runtime_base))
    monkeypatch.setattr(root_endpoints, "get_ctx", lambda: fake_ctx)
    digest = hashlib.sha256(b"private").hexdigest()

    app = FastAPI()
    app.include_router(root_endpoints.router)
    client = TestClient(app)

    response = client.post(
        "/v1/root/browser-assets/cache/ensure",
        json={
            "cacheKey": f"sha256:{digest}",
            "sourceUrl": "http://127.0.0.1/assets/private.svg",
            "filename": "private.svg",
        },
        headers={"X-Owner-Token": "owner-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "browser_asset_source_private_host"


def test_root_browser_assets_diagnostics_and_gc(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "owner-token")
    from adaos.apps.api import root_endpoints
    from adaos.services.browser_assets import (
        public_blob_file_for_digest,
        publish_public_blob_bytes,
        publish_system_resource_descriptors,
    )

    runtime_base = tmp_path / "runtime"
    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: runtime_base))
    monkeypatch.setattr(root_endpoints, "get_ctx", lambda: fake_ctx)
    published = publish_system_resource_descriptors(base_dir=runtime_base)
    descriptor = published["published"]["assistant.default.avatar"]
    referenced_digest = descriptor["cacheKey"].split(":", 1)[1]
    referenced_file = public_blob_file_for_digest(referenced_digest, base_dir=runtime_base)
    assert referenced_file is not None
    referenced_file.unlink()
    orphan = publish_public_blob_bytes(
        b"<svg><title>Orphan</title></svg>",
        filename="orphan.svg",
        mime="image/svg+xml",
        base_dir=runtime_base,
    )

    app = FastAPI()
    app.include_router(root_endpoints.router)
    client = TestClient(app)
    headers = {"X-Owner-Token": "owner-token"}

    diagnostics = client.get("/v1/root/browser-assets/diagnostics", headers=headers)
    assert diagnostics.status_code == 200
    diag_payload = diagnostics.json()["diagnostics"]
    assert diag_payload["ok"] is False
    assert diag_payload["counts"]["missing"] == 1
    assert diag_payload["missing"][0]["cacheKey"] == descriptor["cacheKey"]

    dry_run = client.post("/v1/root/browser-assets/gc", json={"dryRun": True}, headers=headers)
    assert dry_run.status_code == 200
    assert dry_run.json()["gc"]["counts"]["candidates"] == 1

    gc = client.post("/v1/root/browser-assets/gc", json={"dryRun": False}, headers=headers)
    assert gc.status_code == 200
    assert gc.json()["gc"]["counts"]["removed"] == 1
    orphan_digest = orphan["cacheKey"].split(":", 1)[1]
    assert public_blob_file_for_digest(orphan_digest, base_dir=runtime_base) is None
