from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from adaos.adapters.audio.stt import model_manager


def _archive(tmp_path: Path, *, unsafe: bool = False) -> tuple[Path, dict]:
    archive = tmp_path / "test-model.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.txt" if unsafe else "test-model/conf/model.conf", "test")
        if not unsafe:
            output.writestr("test-model/am/final.mdl", "model")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    descriptor = {
        "id": "test-model",
        "language": "ru-RU",
        "archive_url": "https://invalid.example/test-model.zip",
        "archive_sha256": digest,
        "unpacked_folder": "test-model",
        "quality_tier": "compact",
        "resource_class": "test",
        "expected_runtime_memory_mb": 100,
        "recommended_min_memory_mb": 256,
    }
    return archive, descriptor


def test_install_select_verify_and_recommend(tmp_path: Path) -> None:
    archive, descriptor = _archive(tmp_path)
    base = tmp_path / "models"

    installed = model_manager.install_vosk_model(
        "test-model", base, local_zip=archive, descriptor=descriptor
    )

    assert (installed / "am" / "final.mdl").read_text() == "model"
    assert model_manager.resolve_vosk_model("ru-RU", base) == installed
    marker = json.loads((installed / model_manager.MODEL_MARKER).read_text(encoding="utf-8"))
    assert marker["archive_sha256_actual"] == descriptor["archive_sha256"]
    assert model_manager.recommend_model(
        "ru-RU", base, total_memory_mb=2_048, device_id="phone"
    ) is None

    model_manager.mark_model_verified(
        "test-model", base, device_id="phone", metrics={"commands": 12, "success_rate": 0.92}
    )
    recommended = model_manager.recommend_model(
        "ru-RU", base, total_memory_mb=2_048, device_id="phone"
    )
    assert recommended is not None
    assert recommended["id"] == "test-model"
    assert model_manager.recommend_model(
        "ru-RU", base, total_memory_mb=128, device_id="phone"
    ) is None


def test_install_rejects_hash_mismatch(tmp_path: Path) -> None:
    archive, descriptor = _archive(tmp_path)
    descriptor["archive_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="sha256_mismatch"):
        model_manager.install_vosk_model(
            "test-model", tmp_path / "models", local_zip=archive, descriptor=descriptor
        )


def test_install_rejects_zip_slip(tmp_path: Path) -> None:
    archive, descriptor = _archive(tmp_path, unsafe=True)

    with pytest.raises(ValueError, match="archive_unsafe"):
        model_manager.install_vosk_model(
            "test-model", tmp_path / "models", local_zip=archive, descriptor=descriptor
        )
    assert not (tmp_path / "escape.txt").exists()


def test_catalog_has_verified_mobile_language_choices() -> None:
    catalog = model_manager.model_catalog()

    assert catalog["schema_version"] == "adaos-stt-model-catalog.v1"
    assert {item["language"] for item in catalog["models"]} >= {"ru-RU", "en-US"}
    assert all(len(item["archive_sha256"]) == 64 for item in catalog["models"])

