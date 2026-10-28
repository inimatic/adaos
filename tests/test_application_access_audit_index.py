from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path

import pytest

from adaos.services.applications.store import ApplicationStore, ApplicationStoreError
from adaos.services.applications import access_audit_index, store as store_module


@pytest.fixture(autouse=True)
def capture_index_observations(caplog, monkeypatch):
    # Application logging normally has its own JSON handler, without root
    # propagation. Let pytest observe those same records in these tests.
    # The production queue-only addHandler guard deliberately redirects direct
    # handlers. This test-local attribute replacement bypasses that guard only
    # for pytest's capture handler and is restored by monkeypatch.
    monkeypatch.setattr(access_audit_index._log, "handlers", [*access_audit_index._log.handlers, caplog.handler])


def journal(tmp_path, count=60):
    store = ApplicationStore(tmp_path)
    events = store.append_application_access_audits({
        "application_id": f"app_{index % 3}", "subject_ref": f"user:{index % 2}",
        "action": "runtime_observation", "permission_id": "workspace.read",
    } for index in range(count))
    return store, events


@pytest.mark.parametrize("application_id", [None, "app_0", "absent"])
@pytest.mark.parametrize("subject_ref", [None, "user:1"])
def test_index_matches_source_filter_order_and_limit(tmp_path, application_id, subject_ref):
    store, events = journal(tmp_path)
    expected = tuple(x for x in reversed(events)
                     if (application_id is None or x["application_id"] == application_id)
                     and (subject_ref is None or x["subject_ref"] == subject_ref))[:7]
    assert store.list_application_access_audit(application_id, subject_ref=subject_ref, limit=7) == expected


def test_indexed_reads_do_not_enumerate_or_read_other_applications(tmp_path, monkeypatch, caplog):
    store, _ = journal(tmp_path)
    store.list_application_access_audit("app_0", limit=4)
    reads = []
    original = store_module._read

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(store_module, "_read", read)
    monkeypatch.setattr(access_audit_index, "_source_snapshot", lambda *a: pytest.fail("warm directory scan"))
    with caplog.at_level("DEBUG", logger="adaos.applications.audit_index"):
        result = ApplicationStore(tmp_path).list_application_access_audit("app_0", limit=4)
    assert len(result) == len(reads) == 4
    assert "'source_files_scanned': 0" in caplog.text
    assert "'mode': 'indexed'" in caplog.text


def test_index_catches_append_even_if_derived_sequence_cursor_is_torn(tmp_path, caplog):
    store, _ = journal(tmp_path)
    store.list_application_access_audit("app_0")
    event = store.append_application_access_audit({"application_id": "app_0", "subject_ref": "user:new"})
    (store.root / "application_access_audit/sequence.json").write_text("torn", encoding="utf-8")
    with caplog.at_level("INFO", logger="adaos.applications.audit_index"):
        result = ApplicationStore(tmp_path).list_application_access_audit("app_0", limit=1)
    assert result == (event,)
    assert "'mode': 'incremental'" in caplog.text
    assert "'source_files_scanned': 1" in caplog.text


def test_missing_index_is_rebuilt_and_corrupt_index_is_quarantined(tmp_path, caplog):
    store, events = journal(tmp_path)
    store.list_application_access_audit(limit=4)
    database = store.root / "read_models/access_audit.v1.sqlite3"
    database.unlink()  # derived-only state, never source events
    assert store.list_application_access_audit(limit=4) == tuple(reversed(events))[:4]
    database.write_bytes(b"not sqlite")
    with caplog.at_level("INFO", logger="adaos.applications.audit_index"):
        assert store.list_application_access_audit(limit=4) == tuple(reversed(events))[:4]
    assert len(list(database.parent.glob("*.invalid.*"))) == 1
    assert "index corrupt" in caplog.text
    assert "'mode': 'cold_build'" in caplog.text


def test_corrupt_source_is_not_hidden_by_derived_index(tmp_path):
    store, events = journal(tmp_path)
    store.list_application_access_audit()
    path = store.root / "application_access_audit" / f"{events[-1]['sequence']:020d}.json"
    path.write_text("torn", encoding="utf-8")
    with pytest.raises(ApplicationStoreError, match="cannot read Application record"):
        store.list_application_access_audit(limit=1)


def test_changed_source_selector_fails_closed_instead_of_serving_cached_record(tmp_path):
    store, events = journal(tmp_path)
    store.list_application_access_audit()
    event = {**events[-1], "application_id": "changed"}
    path = store.root / "application_access_audit" / f"{event['sequence']:020d}.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ApplicationStoreError, match="source changed after indexing"):
        store.list_application_access_audit(limit=1)


def test_parallel_cold_readers_build_one_index_without_losing_append(tmp_path):
    store, events = journal(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ApplicationStore(tmp_path).list_application_access_audit(limit=5), range(8)))
    assert all(result == tuple(reversed(events))[:5] for result in results)
    new = store.append_application_access_audit({"application_id": "app_new", "subject_ref": "user:owner"})
    assert ApplicationStore(tmp_path).list_application_access_audit("app_new") == (new,)


def test_bad_cold_source_does_not_commit_a_partial_watermark(tmp_path):
    store, events = journal(tmp_path)
    path = store.root / "application_access_audit/00000000000000000002.json"
    original = path.read_bytes()
    path.write_text("torn", encoding="utf-8")
    with pytest.raises(ApplicationStoreError):
        store.list_application_access_audit()
    path.write_bytes(original)
    assert store.list_application_access_audit(limit=100) == tuple(reversed(events))


def test_snapshot_never_acquires_source_lock_inside_index_lock(tmp_path, monkeypatch):
    store, _ = journal(tmp_path)
    original = access_audit_index.mutation_lock
    held = []

    @contextmanager
    def lock(path, **kwargs):
        if Path(path).name == ".mutation.lock":
            assert "access_audit.index.lock" not in held
        with original(path, **kwargs):
            held.append(Path(path).name)
            try:
                yield
            finally:
                held.pop()

    monkeypatch.setattr(access_audit_index, "mutation_lock", lock)
    assert len(store.list_application_access_audit(limit=1)) == 1


def test_index_does_not_cache_returned_payload(tmp_path):
    store, events = journal(tmp_path)
    store.list_application_access_audit(limit=1)
    event = {**events[-1], "decision": "deny"}
    path = store.root / "application_access_audit" / f"{event['sequence']:020d}.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    assert store.list_application_access_audit(limit=1)[0]["decision"] == "deny"
