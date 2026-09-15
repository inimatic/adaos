import hashlib
import json

import pytest

from adaos.services.builder.automation_snapshot import read_automation_ui


@pytest.fixture
def snapshot(tmp_path):
    ui = {"ui": {"application": {"desktop": {"pageSchema": {"id": "example"}}}}}
    raw = json.dumps(ui).encode("utf-8")
    (tmp_path / "webui.json").write_bytes(raw)
    (tmp_path / "snapshot.json").write_text(json.dumps({"object_type": "scenario", "object_id": "example",
        "task_id": "task.current", "file_digests": {"webui.json": hashlib.sha256(raw).hexdigest()}}), encoding="utf-8")
    return tmp_path, ui


def test_exact_task_snapshot(snapshot):
    root, ui = snapshot
    assert read_automation_ui(root, "example", "task.current") == ui
    for scenario, task in [("another", "task.current"), ("example", "task.previous")]:
        with pytest.raises(ValueError, match="exact retained"):
            read_automation_ui(root, scenario, task)


def test_missing_snapshot_never_uses_live_source(snapshot):
    root, _ = snapshot
    (root / "snapshot.json").unlink()
    with pytest.raises(ValueError, match="no DEV fallback"):
        read_automation_ui(root, "example", "task.current")


def test_changed_snapshot_never_receives_old_task_label(snapshot):
    root, _ = snapshot
    (root / "webui.json").write_text('{}', encoding="utf-8")
    with pytest.raises(ValueError, match="content changed"):
        read_automation_ui(root, "example", "task.current")
