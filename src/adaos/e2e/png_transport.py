"""Lossless PNG wire compression; never resize or redraw qualification images."""
import struct
import zlib


def compact_png(data: bytes, *, max_decoded_bytes: int = 128 * 1024 * 1024) -> bytes:
    signature = b'\x89PNG\r\n\x1a\n'
    if not data.startswith(signature):
        raise ValueError('PNG signature required')
    chunks, compressed, position = [], [], len(signature)
    while position < len(data):
        start = position
        if position + 12 > len(data):
            raise ValueError('Truncated PNG chunk')
        length = struct.unpack('>I', data[position:position + 4])[0]
        kind = data[position + 4:position + 8]
        end = position + 12 + length
        if end > len(data):
            raise ValueError('Truncated PNG payload')
        payload = data[position + 8:end - 4]
        crc = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(kind + payload) != crc:
            raise ValueError('PNG checksum mismatch')
        chunks.append((kind, data[start:end]))
        if kind == b'IDAT':
            compressed.append(payload)
        position = end
        if kind == b'IEND':
            break
    if not compressed or not chunks or chunks[-1][0] != b'IEND' or position != len(data):
        raise ValueError('Complete PNG with image data required')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(b''.join(compressed), max_decoded_bytes + 1)
    if len(raw) > max_decoded_bytes or not decoder.eof or decoder.unused_data:
        raise ValueError('PNG scanline stream invalid or exceeds safe bound')
    encoded = zlib.compress(raw, level=9)
    # Same filtered scanline stream, same IHDR/palette/color/metadata chunks.
    if zlib.decompress(encoded) != raw:
        raise ValueError('Lossless PNG transport invariant violated')
    replacement = struct.pack('>I', len(encoded)) + b'IDAT' + encoded + struct.pack('>I', zlib.crc32(b'IDAT' + encoded))
    result, emitted = [signature], False
    for kind, original in chunks:
        if kind == b'IDAT':
            if not emitted:
                result.append(replacement)
                emitted = True
        else:
            result.append(original)
    optimized = b''.join(result)
    return optimized if len(optimized) < len(data) else data
