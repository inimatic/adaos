"""Wire-clock compatibility regressions for cross-client GC references."""

import pytest
import y_py as Y


def uint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def vector(doc):
    data = iter(Y.encode_state_vector(doc))

    def read():
        value, shift = 0, 0
        for byte in data:
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7
        raise AssertionError("truncated state vector")

    return {read(): read() for _ in range(read())}


@pytest.mark.parametrize("update, expected", [
    (bytes.fromhex("0201020081010401010100000a00"), {1: 10, 2: 1}),
    (bytes.fromhex("0201020081010401020100000a010101720100"), {1: 11, 2: 1}),
])
def test_cross_client_gc_reference_preserves_clocks(update, expected):
    for _ in range(4):
        doc = Y.YDoc()
        Y.apply_update(doc, update)
        assert vector(doc) == expected
        update = Y.encode_state_as_update(doc)


@pytest.mark.parametrize("length", [2, 10, 127, 4096])
@pytest.mark.parametrize("anchor", ["start", "middle", "end"])
@pytest.mark.parametrize("reference", ["origin", "right_origin"])
def test_gc_range_split_keeps_disjoint_clock_intervals(length, anchor, reference):
    pivot = {"start": 0, "middle": length // 2, "end": length - 1}[anchor]
    info = 129 if reference == "origin" else 65
    update = (bytes([2, 1, 2, 0, info, 1]) + uint(pivot) + bytes([1, 2, 1, 0, 0])
              + uint(length) + bytes([1, 1, 1, 114, 1, 0]))
    for _ in range(4):
        doc = Y.YDoc()
        Y.apply_update(doc, update)
        assert vector(doc) == {1: length + 1, 2: 1}
        update = Y.encode_state_as_update(doc)
