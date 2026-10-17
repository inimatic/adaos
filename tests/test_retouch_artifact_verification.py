import hashlib
import io
import json
import sqlite3

from PIL import Image, ImageChops
import pytest

from adaos.e2e.retouch_verification import verify_downloads


@pytest.mark.parametrize('fault', [None, 'protected', 'overlay', 'download', 'escape'])
def test_downloaded_bytes_are_checked_independently_of_application_qa(tmp_path, fault):
    owned, output = tmp_path / 'owned', tmp_path / 'artifacts'
    (owned / 'data').mkdir(parents=True); output.mkdir()
    base = Image.new('RGB', (320, 240), (100, 90, 80))
    result = base.copy(); result.putpixel((100, 100), (101, 90, 80))
    if fault == 'protected': result.putpixel((0, 0), (0, 0, 0))
    def encode(image, fmt):
        stream = io.BytesIO(); image.save(stream, format=fmt); return stream.getvalue()
    exports = []
    for fmt in ('PNG', 'JPEG'):
        raw = encode(result, fmt)
        (output / ('result.' + fmt.lower())).write_bytes(raw)
        exports.append({'format': fmt, 'sha256': hashlib.sha256(raw).hexdigest()})
    overlay = Image.new('RGBA', base.size, (0, 0, 0, 128))
    overlay.putpixel((100, 100), (0, 0, 0, 0))
    if fault == 'protected': overlay.putpixel((0, 0), (0, 0, 0, 0))
    if fault == 'overlay': overlay.putpixel((50, 50), (0, 0, 0, 0))
    overlay.save(output / 'difference-map.png')
    mask = {'permission_mode': 'rectangle', 'context_border_px': 24,
        'crop': {'x': 0, 'y': 0, 'width': 320, 'height': 240},
        'strokes': [{'kind': 'protect', 'points': [{'x': 50, 'y': 50}], 'radius': 8}]}
    run = {'status': 'completed', 'snapshot': {'photo_sha256': 'input', 'mask': mask}, 'exports': exports}
    with sqlite3.connect(owned / 'data/retouch.sqlite3') as db:
        db.execute('CREATE TABLE records(kind,body)'); db.execute('CREATE TABLE assets(digest,base)')
        db.execute('INSERT INTO records VALUES(?,?)', ('runs', json.dumps(run)))
        db.execute('INSERT INTO assets VALUES(?,?)', ('input', encode(base, 'PNG')))
    report = {'downloads': [{'filename': 'result.png'}, {'filename': 'result.jpeg'}]}
    if fault == 'download': (output / 'result.png').write_bytes(encode(base, 'PNG'))
    if fault == 'escape': report['downloads'][0]['filename'] = '../result.png'
    if fault:
        with pytest.raises(ValueError): verify_downloads(owned_root=owned, output=output, report=report)
    else:
        assert verify_downloads(owned_root=owned, output=output, report=report)['changed_pixels'] == 1
