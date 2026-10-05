import hashlib
from types import SimpleNamespace

import pytest

from adaos.sdk.data import blob


def test_read_owned_bytes_is_bounded_and_verified(tmp_path, monkeypatch):
    content = b"verified image bytes"
    ref = "sha256:" + hashlib.sha256(content).hexdigest()
    path = tmp_path / "blob.bin"
    path.write_bytes(content)
    calls = []
    def store(name):
        calls.append(name)
        return SimpleNamespace(materialize_digest=lambda digest: path)
    monkeypatch.setattr(blob, "store", store)
    assert blob.read_bytes(ref, max_bytes=len(content)) == content
    assert calls == ["attachments"]
    with pytest.raises(ValueError, match="size limit"):
        blob.read_bytes(ref, max_bytes=len(content) - 1)
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="digest verification"):
        blob.read_bytes(ref)


@pytest.mark.parametrize("ref,limit", [("/private/file", 10), ("sha256:" + "0" * 64, True),
    ("sha256:" + "0" * 64, 0), ("sha256:" + "0" * 64, 10485761)])
def test_invalid_byte_read_never_touches_storage(monkeypatch, ref, limit):
    monkeypatch.setattr(blob, "store", lambda *args: pytest.fail("invalid read reached storage"))
    with pytest.raises(ValueError):
        blob.read_bytes(ref, max_bytes=limit)


def test_missing_owned_object_does_not_fall_back(monkeypatch):
    def missing(ref):
        raise FileNotFoundError("blob object is unavailable")
    monkeypatch.setattr(blob, "store", lambda name: SimpleNamespace(materialize_digest=missing))
    with pytest.raises(FileNotFoundError):
        blob.read_bytes("sha256:" + "0" * 64)
