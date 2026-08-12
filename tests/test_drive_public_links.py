from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.services.drive_public_links import issue_hub_token, issue_public_token, register_hub_public_link


def _client(monkeypatch, base_dir: Path) -> TestClient:
    sys.modules.setdefault("y_py", types.ModuleType("y_py"))
    from adaos.apps.api import root_endpoints

    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: base_dir))
    monkeypatch.setattr(root_endpoints, "get_ctx", lambda: fake_ctx)
    app = FastAPI()
    app.include_router(root_endpoints.router)
    return TestClient(app)


def test_root_drive_public_link_registers_and_streams_without_auth(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROOT_TOKEN", "root-secret")
    source_root = tmp_path / "source"
    source_root.mkdir()
    target = source_root / "hello.txt"
    target.write_text("hello world", encoding="utf-8")
    public_token = issue_public_token()
    hub_token = issue_hub_token()
    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: tmp_path / "runtime"))
    register_hub_public_link(
        public_token=public_token,
        hub_token=hub_token,
        source_root=source_root,
        rel_path="hello.txt",
        subnet_id="sn_test",
        node_id="node_test",
        zone="ru",
        ctx=fake_ctx,
    )
    client = _client(monkeypatch, tmp_path / "runtime")

    registered = client.post(
        "/v1/drive/public-links/register",
        headers={"X-Root-Token": "root-secret"},
        json={
            "public_token": public_token,
            "hub_token": hub_token,
            "subnet_id": "sn_test",
            "node_id": "node_test",
            "skill": "adaos_drive",
            "zone": "ru",
            "filename": "hello.txt",
            "size_bytes": target.stat().st_size,
            "mime_type": "text/plain",
        },
    )
    assert registered.status_code == 200
    assert registered.json()["link"]["public_token"] == public_token

    meta = client.get(f"/v1/drive/public-links/{public_token}/meta")
    assert meta.status_code == 200
    public_meta = meta.json()["link"]
    assert public_meta["filename"] == "hello.txt"
    assert "subnet_id" not in public_meta
    assert "hub_token" not in public_meta

    ranged = client.get(
        f"/v1/drive/public-links/{public_token}/content",
        headers={"Range": "bytes=1-4"},
    )
    assert ranged.status_code == 206
    assert ranged.content == b"ello"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["content-range"] == "bytes 1-4/11"

    head = client.head(f"/v1/drive/public-links/{public_token}/content?download=1")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == "11"
    assert "attachment" in head.headers["content-disposition"]


def test_root_drive_public_link_registration_requires_root_auth(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROOT_TOKEN", "root-secret")
    client = _client(monkeypatch, tmp_path / "runtime")

    response = client.post(
        "/v1/drive/public-links/register",
        json={
            "public_token": issue_public_token(),
            "hub_token": issue_hub_token(),
            "subnet_id": "sn_test",
            "skill": "adaos_drive",
        },
    )

    assert response.status_code == 401

