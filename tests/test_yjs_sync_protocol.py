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
