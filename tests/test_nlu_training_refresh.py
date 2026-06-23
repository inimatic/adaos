from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


def test_neuro_lite_sync_curated_examples_copies_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from adaos.services.nlu import neuro_lite_service_bridge as bridge

    source = tmp_path / "curated" / "examples_manifest.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"intent": "note.create", "text": "write a note"}) + "\n", encoding="utf-8")
    artifact_root = tmp_path / "neuro_lite"
    monkeypatch.setenv("ADAOS_NEURO_LITE_ARTIFACT_ROOT", str(artifact_root))

    first = bridge.sync_curated_examples(source)

    assert first["ok"] is True
    target = artifact_root / "examples_manifest.jsonl"
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert first["backup_examples_path"] is None

    source.write_text(json.dumps({"intent": "note.create", "text": "create a note"}) + "\n", encoding="utf-8")
    second = bridge.sync_curated_examples(source)

    assert second["ok"] is True
    assert Path(second["backup_examples_path"]).exists()
    assert "create a note" in target.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_training_refresh_updates_neuro_lite_and_marks_neural_rebuild_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import neuro_lite_service_bridge, training_refresh_runtime

    ctx = get_ctx()
    skill_root = Path(ctx.paths.skills_dir()) / "notebook_skill"
    skill_root.mkdir(parents=True, exist_ok=True)
    (skill_root / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "notebook_skill",
                "version": "0.1.0",
                "nlu": {
                    "intents": {
                        "notebook.create_note": {
                            "examples": ["Напишем заметку"],
                            "actions": [
                                {
                                    "type": "skillTool",
                                    "skill": "notebook_skill",
                                    "tool": "create_note",
                                    "target": "notebook_skill.create_note",
                                }
                            ],
                        }
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    async def fake_stage_enabled(_webspace_id: str | None, stage: str) -> bool:
        return stage in {"neuro_lite", "neural"}

    copied: dict[str, str] = {}

    def fake_sync_curated_examples(examples_path: str | Path) -> dict:
        copied["examples_path"] = str(examples_path)
        copied["manifest"] = Path(examples_path).read_text(encoding="utf-8")
        return {"ok": True, "active_examples_path": str(Path(examples_path))}

    async def fake_rebuild_active_model(**_kwargs) -> dict:
        return {"ok": True, "rebuild": {"ok": True, "model_id": "neuro-lite-unit"}}

    monkeypatch.setattr(training_refresh_runtime, "is_stage_enabled", fake_stage_enabled)
    monkeypatch.setattr(neuro_lite_service_bridge, "sync_curated_examples", fake_sync_curated_examples)
    monkeypatch.setattr(neuro_lite_service_bridge, "rebuild_active_model", fake_rebuild_active_model)

    summary = await training_refresh_runtime.refresh_from_curated_examples(
        webspace_id="desktop",
        payload={
            "webspace_id": "desktop",
            "dataset_item": {
                "request_id": "rid-note",
                "intent": "notebook.create_note",
                "examples": ["Напишем заметку"],
            },
        },
    )

    assert summary["ok"] is True
    assert summary["engines"]["neuro_lite"]["status"] == "refreshed"
    assert "Напишем заметку" in copied["manifest"]
    assert summary["engines"]["neural"]["status"] == "rebuild_required"
    assert "notebook.create_note" in summary["engines"]["neural"]["plan"]["changes"]["new_labels"]
