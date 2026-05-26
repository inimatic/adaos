from __future__ import annotations

from pathlib import Path

import adaos.sdk.data.models as models


class FakeRootClient:
    def __init__(self, manifest=None, download_bytes: bytes = b"weights"):
        self.manifest = manifest
        self.download_bytes = download_bytes
        self.uploads = []
        self.downloads = []

    def get_skill_model_manifest(self, *, name: str, label: str = "current", **_: object) -> dict:
        return dict(self.manifest or {})

    def upload_skill_model_artifact(self, **kwargs: object) -> dict:
        self.uploads.append(dict(kwargs))
        return {
            "artifact": kwargs["artifact"],
            "sha256": kwargs["sha256"],
            "size_bytes": kwargs["size_bytes"],
            "version_id": "v-test",
        }

    def download_skill_model_artifact(self, *, dest_path: Path, **kwargs: object) -> dict:
        self.downloads.append(dict(kwargs))
        Path(dest_path).write_bytes(self.download_bytes)
        return {"sha256": self.manifest.get("sha256"), "size_bytes": str(len(self.download_bytes))}


def test_upload_model_skips_when_current_hash_matches(tmp_path, monkeypatch):
    model_path = tmp_path / "model.pt"
    model_path.write_bytes(b"weights")
    sha256, size_bytes = models.hash_file(model_path)
    client = FakeRootClient({"artifact": "model.pt", "sha256": sha256, "size_bytes": size_bytes})
    monkeypatch.setattr(models, "_root_http_client", lambda **_: (client, object()))

    result = models.upload_model(model_path, skill_id="demo_skill")

    assert result["ok"] is True
    assert result["skipped"] is True
    assert client.uploads == []


def test_upload_model_uploads_changed_artifact(tmp_path, monkeypatch):
    model_path = tmp_path / "model.pt"
    model_path.write_bytes(b"new-weights")
    client = FakeRootClient({"artifact": "model.pt", "sha256": "old"})
    monkeypatch.setattr(models, "_root_http_client", lambda **_: (client, object()))

    result = models.update_model_if_changed(model_path, skill_id="demo_skill", metadata={"source": "test"})

    assert result["ok"] is True
    assert result["skipped"] is False
    assert client.uploads[0]["name"] == "demo_skill"
    assert client.uploads[0]["artifact"] == "model.pt"
    assert client.uploads[0]["metadata"] == {"source": "test"}


def test_download_previous_model_resolves_artifact_from_manifest(tmp_path, monkeypatch):
    payload = b"previous"
    model_path = tmp_path / "previous.pt"
    model_path.write_bytes(payload)
    sha256, size_bytes = models.hash_file(model_path)
    model_path.unlink()
    client = FakeRootClient(
        {"artifact": "previous.pt", "sha256": sha256, "size_bytes": size_bytes, "version_id": "prev"},
        download_bytes=payload,
    )
    monkeypatch.setattr(models, "_root_http_client", lambda **_: (client, object()))

    result = models.download_previous_model(tmp_path, skill_id="demo_skill")

    assert result["ok"] is True
    assert Path(result["path"]).name == "previous.pt"
    assert Path(result["path"]).read_bytes() == payload
    assert client.downloads[0]["label"] == "previous"
