import pytest
import y_py as Y

from adaos.services.yjs.sync_protocol import decode_state_vector, encode_server_sync_update
from adaos.services.yjs.sync_protocol import SyncReplayBudget


def test_generation_mismatch_full_replay_is_bounded_per_connection():
    budget = SyncReplayBudget()
    assert budget.admit("foreign_generation_full")
    assert budget.admit("diff")
    assert budget.admit("sanitized_diff")
    assert not budget.admit("foreign_generation_full")
    assert not budget.admit("invalid_vector_full")


@pytest.mark.asyncio
async def test_adapter_records_peer_and_server_close_causes():
    from types import SimpleNamespace
    from adaos.services.yjs.gateway_ws import FastAPIWebsocketAdapter

    async def receive():
        return {"type": "websocket.disconnect", "code": 1001, "reason": "test_navigation"}

    closed = []

    async def close(**kwargs):
        closed.append(kwargs)

    adapter = FastAPIWebsocketAdapter(SimpleNamespace(receive=receive, close=close), "/yws/isolated")
    with pytest.raises(RuntimeError, match="disconnected"):
        await adapter.recv()
    assert (adapter.close_code, adapter.close_initiator, adapter.close_cause) == (1001, "peer", "disconnect_frame")
    await adapter.close(1013, "yws_guard_projection_generation_mismatch")
    assert adapter.close_initiator == "server"
    assert closed == [{"code": 1013, "reason": "yws_guard_projection_generation_mismatch"}]


def test_same_generation_resync_is_small_and_delivers_deletions():
    server = Y.YDoc()
    with server.begin_transaction() as txn:
        server.get_map("data").set(txn, "large", "x" * 200_000)
    browser = Y.YDoc()
    Y.apply_update(browser, Y.encode_state_as_update(server))
    with server.begin_transaction() as txn:
        server.get_map("data").pop(txn, "large")
    update, mode = encode_server_sync_update(server, Y.encode_state_vector(browser))
    assert mode == "diff"
    assert len(update) < 100
    Y.apply_update(browser, update)
    assert browser.get_map("data").get("large") is None


def test_foreign_compacted_room_vector_never_reaches_native_diff_encoder():
    server, old_room = Y.YDoc(), Y.YDoc()
    with old_room.begin_transaction() as txn:
        old_room.get_map("ui").set(txn, "scenario", "old")
    with server.begin_transaction() as txn:
        server.get_map("ui").set(txn, "scenario", "current")
    update, mode = encode_server_sync_update(server, Y.encode_state_vector(old_room))
    assert mode == "sanitized_diff"
    browser = Y.YDoc()
    Y.apply_update(browser, update)
    assert browser.get_map("ui").get("scenario") == "current"


def test_retained_foreign_clocks_do_not_force_periodic_full_replays():
    server, browser = Y.YDoc(), Y.YDoc()
    with server.begin_transaction() as txn:
        server.get_map("data").set(txn, "large", "x" * 200_000)
    with browser.begin_transaction() as txn:
        browser.get_map("local").set(txn, "old", True)
    Y.apply_update(browser, Y.encode_state_as_update(server))
    for _ in range(3):
        update, mode = encode_server_sync_update(server, Y.encode_state_vector(browser))
        assert mode == "sanitized_diff"
        assert len(update) < 100
        Y.apply_update(browser, update)


@pytest.mark.parametrize("value", [b"", b"\x80", b"\x01\x01", b"\x00\x00", b"\xff" * 20])
def test_malformed_state_vectors_are_rejected(value):
    with pytest.raises(ValueError):
        decode_state_vector(value)


@pytest.mark.asyncio
async def test_candidate_refresh_reads_final_checkpoint_without_overwriting_it(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from adaos.services.yjs import gateway_ws as gateway
    from adaos.services.yjs import store as stores

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "0")
    monkeypatch.setattr(stores, "_YSTORE_CACHE", {})
    monkeypatch.setattr(gateway, "y_server", SimpleNamespace(rooms={}))
    path = tmp_path / "desktop.ysnap"
    monkeypatch.setattr(stores, "ystore_path_for_webspace", lambda _key: path)

    async def no_backup_job(_key):
        return False

    monkeypatch.setattr(gateway, "_delete_ystore_backup_job", no_backup_job)
    active = Y.YDoc()
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "retired", True)
        active.get_map("data").set(txn, "value", "prewarm")
    path.write_bytes(Y.encode_state_as_update(active))
    candidate_store = stores.get_ystore_for_webspace("desktop")
    stale = Y.YDoc()
    await candidate_store.apply_updates(stale)

    # The old active keeps serving while its candidate warms up. Shutdown
    # persists these final edits and a deletion after the candidate's read.
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "value", "final")
        active.get_map("data").pop(txn, "retired")
    final_checkpoint = bytes(Y.encode_state_as_update(active))
    path.write_bytes(final_checkpoint)
    browser_vector = Y.encode_state_vector(active)
    assert encode_server_sync_update(stale, browser_vector)[1] == "foreign_generation_full"

    receipt = await gateway.refresh_candidate_yjs_checkpoints()

    refreshed_store = stores.get_ystore_for_webspace("desktop")
    assert refreshed_store is not candidate_store
    assert path.read_bytes() == final_checkpoint
    assert receipt["ok"] and receipt["refreshed_total"] == 1
    refreshed = Y.YDoc()
    await refreshed_store.apply_updates(refreshed)
    assert refreshed.get_map("data").get("value") == "final"
    assert refreshed.get_map("data").get("retired") is None
    assert encode_server_sync_update(refreshed, browser_vector)[1] == "diff"
    assert candidate_store.runtime_snapshot()["update_log_entries"] == 0


@pytest.mark.asyncio
async def test_candidate_refresh_rejects_active_writer(monkeypatch):
    from adaos.services.yjs import gateway_ws as gateway

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "1")
    with pytest.raises(RuntimeError, match="requires_passive_runtime"):
        await gateway.refresh_candidate_yjs_checkpoints()


@pytest.mark.asyncio
@pytest.mark.parametrize("debounce_sec", [0.05, 1.0])
async def test_prewarm_backup_cannot_delete_final_checkpoint_after_promotion(monkeypatch, tmp_path, debounce_sec):
    import asyncio
    from types import SimpleNamespace
    from adaos.services.yjs import gateway_ws as gateway
    from adaos.services.yjs import store as stores

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "0")
    monkeypatch.setattr(stores, "_YSTORE_CACHE", {})
    monkeypatch.setattr(gateway, "y_server", SimpleNamespace(rooms={}))
    path = tmp_path / "desktop.ysnap"
    monkeypatch.setattr(stores, "ystore_path_for_webspace", lambda _key: path)

    async def no_backup_job(_key):
        return False

    monkeypatch.setattr(gateway, "_delete_ystore_backup_job", no_backup_job)
    active = Y.YDoc()
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "value", "prewarm")
    path.write_bytes(Y.encode_state_as_update(active))
    candidate = stores.get_ystore_for_webspace("desktop")
    await candidate.current_state_vector()
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "value", "final")
    final_checkpoint = bytes(Y.encode_state_as_update(active))
    path.write_bytes(final_checkpoint)
    assert candidate._schedule_auto_backup(reason="prewarm_test", debounce_sec=debounce_sec)
    await gateway.refresh_candidate_yjs_checkpoints()
    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "active")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "1")
    await asyncio.sleep(debounce_sec + 0.15)
    assert path.read_bytes() == final_checkpoint


@pytest.mark.asyncio
async def test_passive_candidate_backup_does_not_overwrite_active_checkpoint(monkeypatch, tmp_path):
    from adaos.services.yjs import store as stores

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "0")
    path = tmp_path / "desktop.ysnap"
    monkeypatch.setattr(stores, "ystore_path_for_webspace", lambda _key: path)
    active = Y.YDoc()
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "value", "prewarm")
    path.write_bytes(Y.encode_state_as_update(active))
    candidate = stores.AdaosMemoryYStore("desktop")
    await candidate.current_state_vector()
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "value", "active")
    checkpoint = bytes(Y.encode_state_as_update(active))
    path.write_bytes(checkpoint)

    await candidate.backup_to_disk()

    assert path.read_bytes() == checkpoint
    assert candidate.runtime_snapshot()["last_backup_skip_reason"] == "passive_runtime"


@pytest.mark.asyncio
async def test_candidate_refresh_releases_prewarmed_room_that_has_not_started(monkeypatch, tmp_path):
    import logging
    from types import SimpleNamespace
    from adaos.services.yjs import gateway_ws as gateway
    from adaos.services.yjs import store as stores

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "0")
    monkeypatch.setattr(stores, "_YSTORE_CACHE", {})
    path = tmp_path / "desktop.ysnap"
    monkeypatch.setattr(stores, "ystore_path_for_webspace", lambda _key: path)
    active = Y.YDoc()
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "value", "prewarm")
    path.write_bytes(Y.encode_state_as_update(active))
    store = stores.get_ystore_for_webspace("desktop")
    room = gateway.DiagnosticYRoom(ystore=store, log=logging.getLogger("test.prewarm"))
    assert room._task_group is None
    await store.apply_updates(room.ydoc)
    monkeypatch.setattr(gateway, "y_server", SimpleNamespace(rooms={"desktop": room}))

    async def no_backup_job(_key):
        return False

    monkeypatch.setattr(gateway, "_delete_ystore_backup_job", no_backup_job)
    with active.begin_transaction() as txn:
        active.get_map("data").set(txn, "value", "final")
    checkpoint = bytes(Y.encode_state_as_update(active))
    path.write_bytes(checkpoint)

    receipt = await gateway.refresh_candidate_yjs_checkpoints()

    assert receipt["ok"] is True
    assert not gateway.y_server.rooms
    assert path.read_bytes() == checkpoint
    assert room.ydoc is None


@pytest.mark.asyncio
async def test_candidate_refresh_still_rejects_failure_to_stop_running_room(monkeypatch):
    from types import SimpleNamespace
    from adaos.services.yjs import gateway_ws as gateway
    from adaos.services.yjs import store as stores

    monkeypatch.setenv("ADAOS_RUNTIME_TRANSITION_ROLE", "candidate")
    monkeypatch.setenv("ADAOS_RUNTIME_SHARED_STATE_WRITE_AUTHORIZED", "0")
    monkeypatch.setattr(stores, "_YSTORE_CACHE", {})
    monkeypatch.setattr(gateway, "y_server", SimpleNamespace(rooms={"desktop": SimpleNamespace(_task_group=object())}))

    async def failed_stop(*_args, **_kwargs):
        return {"room_stopped": False, "room_refs_released": True}

    monkeypatch.setattr(gateway, "reset_live_webspace_room", failed_stop)
    with pytest.raises(RuntimeError, match="candidate_yjs_room_release_failed"):
        await gateway.refresh_candidate_yjs_checkpoints()
