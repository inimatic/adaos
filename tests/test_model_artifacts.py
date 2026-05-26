from __future__ import annotations

import json

from adaos.services.models.artifacts import declared_model_artifacts, install_local_artifact, local_artifact_state


def test_declared_model_artifact_defaults_to_skill_data_models(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    weights = skill_dir / "ml" / "weights" / "model.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"weights")
    manifest = {
        "models": {
            "artifacts": {
                "weights": {
                    "path": "ml/weights/model.pt",
                    "capability": "test-capability",
                }
            }
        }
    }

    [artifact] = declared_model_artifacts(manifest, skill_dir=skill_dir)
    state = local_artifact_state(artifact)

    assert artifact.install_path.as_posix() == "data/files/models/model.pt"
    assert state is not None
    assert state.size_bytes == len(b"weights")


def test_install_local_artifact_writes_skill_owned_manifest(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    weights = skill_dir / "model.pt"
    weights.write_bytes(b"weights")
    manifest = {"models": {"artifacts": {"weights": {"path": "model.pt"}}}}
    [artifact] = declared_model_artifacts(manifest, skill_dir=skill_dir)
    state = local_artifact_state(artifact)
    assert state is not None

    entry = install_local_artifact(state, data_root=tmp_path / "runtime" / "data")

    installed = tmp_path / "runtime" / "data" / "files" / "models" / "model.pt"
    manifest_path = tmp_path / "runtime" / "data" / "files" / "models" / "manifest.json"
    assert installed.read_bytes() == b"weights"
    assert entry["install_path"] == "data/files/models/model.pt"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["artifacts"][0]["sha256"] == entry["sha256"]
