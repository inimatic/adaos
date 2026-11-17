from __future__ import annotations

import y_py as Y
import pytest
from adaos.services.yjs.sync_protocol import decode_state_vector


def test_cross_client_origin_inside_gc_preserves_clocks_on_replay():
    # A deleted item from client 2 refers to the middle of client 1's GC block.
    update = bytes.fromhex("0201020081010401010100000a00")
    expected = {1: 10, 2: 1}
    for _ in range(4):
        doc = Y.YDoc()
        Y.apply_update(doc, update)
        assert decode_state_vector(Y.encode_state_vector(doc)) == expected
        update = Y.encode_state_as_update(doc)


def test_gc_split_before_tombstone_preserves_snapshot_clock_ranges():
    # A trailing tombstone exposes the overlapping range in the encoded snapshot.
    update = bytes.fromhex("0201020081010401020100000a010101720100")
    expected = {1: 11, 2: 1}
    for _ in range(4):
        doc = Y.YDoc()
        Y.apply_update(doc, update)
        assert decode_state_vector(Y.encode_state_vector(doc)) == expected
        update = Y.encode_state_as_update(doc)


@pytest.mark.parametrize("length", [2, 10, 127, 4096])
@pytest.mark.parametrize("anchor", ["start", "middle", "end"])
@pytest.mark.parametrize("reference", ["origin", "right_origin"])
def test_gc_reference_replay_converges_without_manufacturing_future_clocks(length, anchor, reference):
    from adaos.services.yjs.sync_protocol import encode_server_sync_update

    def uint(value):
        out = bytearray()
        while value > 127:
            out.append((value & 127) | 128)
            value >>= 7
        out.append(value)
        return bytes(out)

    pivot = {"start": 0, "middle": length // 2, "end": length - 1}[anchor]
    info = 129 if reference == "origin" else 65
    update = (bytes([2, 1, 2, 0, info, 1]) + uint(pivot) + bytes([1, 2, 1, 0, 0])
              + uint(length) + bytes([1, 1, 1, 114, 1, 0]))
    expected = {1: length + 1, 2: 1}
    for _ in range(4):
        server = Y.YDoc()
        Y.apply_update(server, update)
        assert decode_state_vector(Y.encode_state_vector(server)) == expected
        browser = Y.YDoc()
        Y.apply_update(browser, Y.encode_state_as_update(server))
        vector = Y.encode_state_vector(browser)
        assert decode_state_vector(vector) == expected
        delta, mode = encode_server_sync_update(server, vector)
        assert mode == "diff"
        assert len(delta) < 64
        update = Y.encode_state_as_update(browser)
