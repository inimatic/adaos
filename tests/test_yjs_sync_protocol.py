import pytest
import y_py as Y

from adaos.services.yjs.sync_protocol import decode_state_vector, encode_server_sync_update


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
