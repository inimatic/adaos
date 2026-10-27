"""Bounded Yjs handshake responses without trusting foreign room clocks."""

from __future__ import annotations

from typing import Any


class SyncReplayBudget:
    """A mismatched connection gets one full replay, never a replay loop."""

    def __init__(self) -> None:
        self.full_replays = 0

    def admit(self, mode: str) -> bool:
        if not mode.endswith("_full"):
            return True
        self.full_replays += 1
        return self.full_replays <= 1


def decode_state_vector(value: bytes) -> dict[int, int]:
    offset = 0

    def uint() -> int:
        nonlocal offset
        result = 0
        for shift in range(0, 56, 7):
            if offset >= len(value):
                raise ValueError("truncated_state_vector")
            byte = value[offset]
            offset += 1
            result |= (byte & 127) << shift
            if not byte & 128:
                return result
        raise ValueError("oversized_state_vector_integer")

    count = uint()
    if count > 100_000 or count * 2 > len(value):
        raise ValueError("oversized_state_vector")
    result: dict[int, int] = {}
    for _ in range(count):
        client, clock = uint(), uint()
        if client in result:
            raise ValueError("duplicate_state_vector_client")
        result[client] = clock
    if offset != len(value):
        raise ValueError("trailing_state_vector_bytes")
    return result


def encode_server_sync_update(ydoc: Any, client_vector: bytes) -> tuple[bytes, str]:
    """Use a diff only for clocks belonging to the current server document.

    Foreign or future clocks can panic older yrs encoders after compaction.
    Sanitize foreign entries before native encoding: an old client may retain
    them indefinitely, so their presence must not force repeated full replays.
    Equal vectors still need the delete set:
    deletions do not advance a Yjs state vector, so an empty fabricated update
    would lose deletes. Let yrs encode that small diff as well.
    """
    import y_py as Y

    server_vector = Y.encode_state_vector(ydoc)
    try:
        client = decode_state_vector(client_vector)
        server = decode_state_vector(server_vector)
    except ValueError:
        return Y.encode_state_as_update(ydoc), "invalid_vector_full"
    if any(clock > server[client_id] for client_id, clock in client.items() if client_id in server):
        return Y.encode_state_as_update(ydoc), "foreign_generation_full"
    if any(client_id not in server for client_id in client):
        known = {client_id: clock for client_id, clock in client.items() if client_id in server}
        encoded = bytearray()

        def uint(number: int) -> None:
            while number > 127:
                encoded.append((number & 127) | 128)
                number >>= 7
            encoded.append(number)

        uint(len(known))
        for client_id, clock in known.items():
            uint(client_id)
            uint(clock)
        return Y.encode_state_as_update(ydoc, bytes(encoded)), "sanitized_diff"
    return Y.encode_state_as_update(ydoc, client_vector), "diff"
