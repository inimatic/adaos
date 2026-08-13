from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.services.drive_public_links import issue_hub_token, issue_public_token, register_hub_public_link
from adaos.services.public_grants import normalize_public_capabilities


def _client(monkeypatch, base_dir: Path) -> TestClient:
    sys.modules.setdefault("y_py", types.ModuleType("y_py"))
    from adaos.apps.api import root_endpoints

    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: base_dir))
    monkeypatch.setattr(root_endpoints, "get_ctx", lambda: fake_ctx)
    app = FastAPI()
    app.include_router(root_endpoints.router)
    return TestClient(app)


def test_public_capabilities_are_readonly() -> None:
    assert normalize_public_capabilities(["read", "upload", "download", "delete"]) == (
        "read",
        "download",
    )
    assert normalize_public_capabilities(["read", "list", "upload"], resource_kind="folder") == (
        "read",
        "list",
    )


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
    assert public_meta["grant_schema"] == "adaos.public_grant.v1"
    assert public_meta["public_face"]["id"] == "adaos_drive.files.public"
    assert public_meta["resource_kind"] == "file"
    assert public_meta["readonly"] is True
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


def test_root_drive_public_folder_lists_and_streams_children(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ROOT_TOKEN", "root-secret")
    source_root = tmp_path / "source"
    docs = source_root / "docs"
    nested = docs / "nested"
    nested.mkdir(parents=True)
    (docs / "note.md").write_bytes(b"# Note\n")
    (nested / "inner.txt").write_text("inside", encoding="utf-8")
    public_token = issue_public_token()
    hub_token = issue_hub_token()
    fake_ctx = SimpleNamespace(paths=SimpleNamespace(base_dir=lambda: tmp_path / "runtime"))
    register_hub_public_link(
        public_token=public_token,
        hub_token=hub_token,
        source_root=source_root,
        rel_path="docs",
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
            "filename": "docs",
            "resource_kind": "folder",
            "mime_type": "inode/directory",
            "capabilities": ["read", "list", "preview", "download"],
        },
    )
    assert registered.status_code == 200
    assert registered.json()["link"]["resource_kind"] == "folder"

    listing = client.get(f"/v1/drive/public-links/{public_token}/list")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["readonly"] is True
    assert payload["link"]["resource_kind"] == "folder"
    assert {item["name"] for item in payload["items"]} >= {"note.md", "nested"}

    nested_listing = client.get(f"/v1/drive/public-links/{public_token}/list?path=nested")
    assert nested_listing.status_code == 200
    assert nested_listing.json()["path"] == "nested"
    assert nested_listing.json()["items"][0]["kind"] == "parent"

    child = client.get(f"/v1/drive/public-links/{public_token}/content?path=note.md")
    assert child.status_code == 200
    assert child.content == b"# Note\n"

    blocked = client.get(f"/v1/drive/public-links/{public_token}/content?path=../outside.txt")
    assert blocked.status_code == 400


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
