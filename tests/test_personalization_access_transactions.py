import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import pytest

from adaos.domain.personalization_access import Grant, ScopeRef, SubjectRef
from adaos.services.personalization_access import (
    PersonalizationAccessError, PersonalizationAccessService, PersonalizationAccessStore,
)


OWNER = SubjectRef("user", "owner")
READER = SubjectRef("user", "reader")
SCOPE = ScopeRef("skill", "sample")


def _grant(store):
    store.put_grant(Grant(grant_id="read", subject=READER, scope=SCOPE,
                          capabilities=("workspace.read",), issued_by=OWNER))


def test_stale_access_instance_cannot_restore_a_revoked_grant_by_writing_audit(tmp_path):
    path = tmp_path / "access.json"
    writer = PersonalizationAccessStore(path)
    _grant(writer)
    stale = PersonalizationAccessService(PersonalizationAccessStore(path), owner=OWNER)
    writer.update_grant("read", {"status": "revoked"})
    decision = stale.evaluate(actor=READER, action="workspace.read", scope=SCOPE)
    assert decision.decision == "deny"
    assert writer.get_grant("read")["status"] == "revoked"
    assert writer.list_audit(decision="deny")


def test_stale_access_instances_merge_distinct_writes_and_partial_updates(tmp_path):
    path = tmp_path / "access.json"
    first, second = PersonalizationAccessStore(path), PersonalizationAccessStore(path)
    _grant(first)
    second.put_user(READER)
    first.update_grant("read", {"status": "revoked"})
    second.update_grant("read", {"metadata": {"note": "not a regrant"}})
    assert first.get_user("reader")
    assert second.get_grant("read")["status"] == "revoked"
    assert first.get_grant("read")["metadata"]["note"] == "not a regrant"


@pytest.mark.parametrize("persisted", [False, True])
@pytest.mark.parametrize("caught_inside", [False, True])
def test_access_batch_rolls_back_failed_nested_operations(tmp_path, persisted, caught_inside):
    store = PersonalizationAccessStore(tmp_path / "access.json" if persisted else None)
    store.put_user(OWNER)
    before = store.snapshot()
    with pytest.raises((ValueError, PersonalizationAccessError)):
        with store.batch():
            store.put_user(READER)
            try:
                with store.batch():
                    store.put_user(SubjectRef("user", "another"))
                    raise ValueError("failed transaction")
            except ValueError:
                if not caught_inside:
                    raise
    assert store.snapshot() == before


def test_access_failed_persistence_does_not_leak_pending_facts(tmp_path, monkeypatch):
    store = PersonalizationAccessStore(tmp_path / "access.json")
    store.put_user(OWNER)
    before = store.snapshot()
    def fail():
        raise OSError("unavailable disk")
    monkeypatch.setattr(store, "_save_now", fail)
    with pytest.raises(OSError):
        store.put_user(READER)
    assert store.snapshot() == before


@pytest.mark.parametrize("contents", ["{", "[]", '{"grants": []}'])
def test_corrupt_access_facts_are_not_replaced_with_empty_state(tmp_path, contents):
    path = tmp_path / "access.json"
    store = PersonalizationAccessStore(path)
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(PersonalizationAccessError):
        store.put_user(OWNER)
    assert path.read_text(encoding="utf-8") == contents


def test_access_returns_detached_nested_records():
    store = PersonalizationAccessStore()
    returned = store.put_user(READER, metadata={"nested": {"value": 1}})
    returned["metadata"]["nested"]["value"] = 2
    snapshot = store.snapshot()
    snapshot["users"]["reader"]["metadata"]["nested"]["value"] = 3
    assert store.get_user("reader")["metadata"]["nested"]["value"] == 1


def test_access_serializes_threads_on_one_instance(tmp_path):
    store = PersonalizationAccessStore(tmp_path / "access.json")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: store.put_user(SubjectRef("user", f"user-{index}")), range(24)))
    assert len(store.snapshot()["users"]) == 24


def test_nested_factories_join_the_same_path_transaction(tmp_path):
    path = tmp_path / "access.json"
    outer = PersonalizationAccessStore(path)
    _grant(outer)
    with outer.batch():
        outer.put_user(OWNER)
        inner = PersonalizationAccessStore(path)
        assert inner.get_user("owner")
        inner.update_grant("read", {"status": "revoked"})
        assert outer.get_grant("read")["status"] == "revoked"
        outer.put_user(READER)
    result = PersonalizationAccessStore(path).snapshot()
    assert set(result["users"]) == {"owner", "reader"}
    assert result["grants"]["read"]["status"] == "revoked"


def test_nested_factory_failure_rolls_back_the_outer_transaction(tmp_path):
    path = tmp_path / "access.json"
    outer = PersonalizationAccessStore(path)
    outer.put_user(OWNER)
    with pytest.raises(PersonalizationAccessError, match="aborted"):
        with outer.batch():
            inner = PersonalizationAccessStore(path)
            try:
                with inner.batch():
                    inner.put_user(READER)
                    raise ValueError("nested operation failed")
            except ValueError:
                pass
    assert set(outer.snapshot()["users"]) == {"owner"}


def _process_writer(path, ready, start, prefix):
    store = PersonalizationAccessStore(path)
    ready.put(prefix)
    if not start.wait(30):
        raise RuntimeError("parent did not release the test barrier")
    for index in range(8):
        store.put_user(SubjectRef("user", f"{prefix}-{index}"))


def test_access_serializes_processes_without_restoring_revoked_facts(tmp_path):
    path = tmp_path / "access.json"
    parent = PersonalizationAccessStore(path)
    _grant(parent)
    context = multiprocessing.get_context("spawn")
    ready, start = context.Queue(), context.Event()
    children = [context.Process(target=_process_writer, args=(path, ready, start, prefix)) for prefix in ("a", "b")]
    try:
        for child in children:
            child.start()
        assert {ready.get(timeout=30), ready.get(timeout=30)} == {"a", "b"}
        parent.update_grant("read", {"status": "revoked"})
        start.set()
        for child in children:
            child.join(timeout=30)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        ready.close()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert len(persisted["users"]) == 16
    assert persisted["grants"]["read"]["status"] == "revoked"
