from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch, skill_dir: Path) -> TestClient:
    from adaos.apps.api import node_api

    def fake_find_skill_dir(name: str, **_kwargs):
        if name == "voice_chat_skill":
            return skill_dir
        raise node_api.SkillDirectoryNotFoundError(name)

    monkeypatch.setattr(node_api, "find_skill_dir", fake_find_skill_dir)
    app = FastAPI()
    app.include_router(node_api.router, prefix="/api/node")
    return TestClient(app)


def test_skill_asset_endpoint_serves_assets_with_cache_headers(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "voice_chat_skill"
    asset = skill_dir / "assets" / "icons" / "voice.svg"
    asset.parent.mkdir(parents=True)
    asset.write_text("<svg></svg>", encoding="utf-8")
    client = _client(monkeypatch, skill_dir)

    response = client.get("/api/node/skills/voice_chat_skill/assets/icons/voice.svg?token=dev-local-token")

    assert response.status_code == 200
    assert response.text == "<svg></svg>"
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["x-adaos-cache-key"].startswith("sha256:")
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert "attachment" not in response.headers.get("content-disposition", "")

    cached = client.get(
        "/api/node/skills/voice_chat_skill/assets/icons/voice.svg?token=dev-local-token",
        headers={"If-None-Match": response.headers["etag"]},
    )

    assert cached.status_code == 304
    assert cached.headers["x-adaos-cache-key"] == response.headers["x-adaos-cache-key"]


def test_skill_asset_endpoint_rejects_path_traversal(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "voice_chat_skill"
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "secret.txt").write_text("secret", encoding="utf-8")
    client = _client(monkeypatch, skill_dir)

    response = client.get("/api/node/skills/voice_chat_skill/assets/%2e%2e/secret.txt?token=dev-local-token")

    assert response.status_code in {400, 403, 404}
    assert response.text != "secret"
