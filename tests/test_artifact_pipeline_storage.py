from __future__ import annotations

import json
import os
from pathlib import Path

import adaos.services.artifact_pipeline.storage as storage


def test_atomic_json_replace_is_visible_after_durable_switch(tmp_path: Path) -> None:
    target = tmp_path / "state" / "record.json"

    storage.atomic_write_json(target, {"revision": 1})
    storage.atomic_write_json(target, {"revision": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"revision": 2}
    assert not list(target.parent.glob(f".{target.name}.*"))


def test_replace_syncs_source_and_target_directory_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source = source_root / "record.json"
    target = target_root / "record.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    synced: list[Path] = []

    monkeypatch.setattr(storage, "_replace_once", os.replace)
    monkeypatch.setattr(
        storage,
        "sync_directory",
        lambda path: synced.append(Path(path).resolve()) or True,
    )

    storage.replace_with_retry(source, target)

    assert target.read_text(encoding="utf-8") == "new"
    assert not source.exists()
    assert synced == [target_root.resolve(), source_root.resolve()]


def test_mutation_lock_is_reentrant_for_the_owning_thread(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / ".mutation.lock"

    with storage.mutation_lock(lock_path, timeout_s=0.1):
        with storage.mutation_lock(lock_path, timeout_s=0.1):
            assert lock_path.is_file()
