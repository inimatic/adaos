import struct
import zlib

import pytest

from adaos.e2e.png_transport import compact_png


def chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))


def fixture():
    raw = (b'\x00' + b'\x18\x21\x32' * 20) * 20
    encoded = zlib.compress(raw, 0)
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 20, 20, 8, 2, 0, 0, 0))
    metadata = chunk(b'tEXt', b'Source\x00Unchanged screenshot')
    png = b'\x89PNG\r\n\x1a\n' + ihdr + metadata + chunk(b'IDAT', encoded[:20]) + chunk(b'IDAT', encoded[20:]) + chunk(b'IEND', b'')
    return png, raw, ihdr, metadata


def test_compression_preserves_scanlines_dimensions_and_metadata():
    png, raw, ihdr, metadata = fixture()
    result = compact_png(png)
    assert len(result) < len(png)
    assert ihdr in result and metadata in result
    position = result.index(b'IDAT')
    size = struct.unpack('>I', result[position - 4:position])[0]
    assert zlib.decompress(result[position + 4:position + 4 + size]) == raw
    assert compact_png(result) == result


@pytest.mark.parametrize('invalid', [b'not png', b'\x89PNG\r\n\x1a\n', fixture()[0][:-2], fixture()[0] + b'trailing'])
def test_invalid_png_is_rejected_before_transport(invalid):
    with pytest.raises(ValueError):
        compact_png(invalid)


def test_checksum_corruption_is_not_silently_reencoded():
    corrupted = bytearray(fixture()[0])
    corrupted[16] ^= 1
    with pytest.raises(ValueError, match='checksum'):
        compact_png(bytes(corrupted))


def test_decompression_is_bounded():
    with pytest.raises(ValueError, match='safe bound'):
        compact_png(fixture()[0], max_decoded_bytes=10)
