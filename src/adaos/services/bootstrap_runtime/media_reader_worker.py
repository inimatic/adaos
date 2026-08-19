from __future__ import annotations

import os
import struct
import sys


MAX_PATH_BYTES = 64 * 1024
MAX_READ_BYTES = 4 * 1024 * 1024


def _read_exact(size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max(0, int(size))
    while remaining:
        chunk = sys.stdin.buffer.read(remaining)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write(payload: bytes) -> None:
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        path_size = struct.unpack("!I", _read_exact(4))[0]
        if path_size <= 0 or path_size > MAX_PATH_BYTES:
            return 2
        path = os.fsdecode(_read_exact(path_size))
        handle = open(path, "rb", buffering=0)
    except Exception:
        try:
            _write(struct.pack("!BQ", 1, 0))
        except Exception:
            pass
        return 3

    try:
        size = int(os.fstat(handle.fileno()).st_size)
        _write(struct.pack("!BQ", 0, size))
        while True:
            try:
                command = _read_exact(12)
            except EOFError:
                break
            offset, requested = struct.unpack("!QI", command)
            if requested > MAX_READ_BYTES:
                detail = b"read_too_large"
                _write(struct.pack("!BI", 1, len(detail)) + detail)
                continue
            try:
                handle.seek(int(offset))
                payload = handle.read(int(requested))
                _write(struct.pack("!BI", 0, len(payload)) + payload)
            except Exception as exc:
                detail = type(exc).__name__.encode("ascii", errors="replace")[:160]
                _write(struct.pack("!BI", 1, len(detail)) + detail)
    finally:
        handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
