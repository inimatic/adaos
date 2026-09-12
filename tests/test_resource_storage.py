from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import pytest

from adaos.services.resources.storage import ResourceStorage


def test_legacy_state_import_is_once_atomic_and_preserves_original(tmp_path):
    path = tmp_path / "registry.json"
    legacy = {"resources": {"prototype.one": {"project_ref": "project:one", "webui_digest": "v1", "records": [{"id": "one"}]}}}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    original = path.read_bytes()
    store = ResourceStorage(tmp_path)
    store.import_json(path)
    assert store.get("prototype.one") == legacy["resources"]["prototype.one"]
    updated = {**store.get("prototype.one"), "records": [{"id": "two"}]}
    store.put("prototype.one", updated)
    assert path.read_bytes() == original
    ResourceStorage(tmp_path).import_json(path)
    assert ResourceStorage(tmp_path).get("prototype.one") == updated


def test_corrupt_import_rolls_back_and_can_be_retried(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"resources":{"valid":{},"invalid":1}}', encoding="utf-8")
    store = ResourceStorage(tmp_path)
    with pytest.raises(ValueError, match="legacy registry"):
        store.import_json(path)
    assert store.states() == {}
    path.write_text('{"resources":{"valid":{}}}', encoding="utf-8")
    store.import_json(path)
    assert store.get("valid") == {}


def test_state_reads_are_addressed_scoped_and_do_not_leak_mutation(tmp_path):
    store = ResourceStorage(tmp_path)
    for key, project, revision in (("a", "one", "v1"), ("b", "one", "v2"), ("c", "two", "v1")):
        store.put(key, {"project_ref": project, "webui_digest": revision, "records": [{"id": key}]})
    assert list(store.states(project_ref="one", ui_revision="v1")) == ["a"]
    # An unrelated corrupt payload cannot break a keyed read by forcing a catalog scan.
    with store._connect() as connection, connection:
        connection.execute("UPDATE resource_states SET body='not json' WHERE resource_type='c'")
    item = store.get("a")
    item["records"].clear()
    assert store.get("a")["records"] == [{"id": "a"}]
    assert store.get("missing") is None


def test_read_observes_other_process_write_without_ttl(tmp_path):
    store = ResourceStorage(tmp_path)
    store.put("one", {"revision": 1})
    subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; from adaos.services.resources.storage import ResourceStorage; ResourceStorage(Path(sys.argv[1])).put('one', {'revision': 2})", str(tmp_path)], check=True)
    assert store.get("one") == {"revision": 2}


def test_journal_import_retention_stream_isolation_and_unicode(tmp_path):
    path = tmp_path / "traces.json"
    history = [{"id": index} for index in range(1002)]
    path.write_text(json.dumps({"items": history}), encoding="utf-8")
    store = ResourceStorage(tmp_path)
    store.import_json(path, stream="traces")
    entry = {"id": "new", "text": "\u041e\u0441\u043c\u043e\u0442\u0440"}
    store.append("traces", entry)
    store.append("events", {"id": "event"})
    store.import_json(path, stream="traces")
    assert store.journal("traces") == [*history[3:], entry]
    assert store.journal("events") == [{"id": "event"}]
    assert json.loads(path.read_text(encoding="utf-8"))["items"] == history


def test_concurrent_first_import_and_append_has_no_duplicates_or_lost_rows(tmp_path):
    path = tmp_path / "traces.json"
    path.write_text('{"items":[{"id":"legacy"}]}', encoding="utf-8")

    def append(index):
        store = ResourceStorage(tmp_path)
        store.import_json(path, stream="traces")
        store.append("traces", {"id": index})

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append, range(20)))
    rows = ResourceStorage(tmp_path).journal("traces")
    assert len(rows) == 21
    assert {row["id"] for row in rows} == {"legacy", *range(20)}
