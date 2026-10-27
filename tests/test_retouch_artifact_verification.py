import hashlib
import io
import json
import sqlite3

from PIL import Image, ImageChops
import pytest

from adaos.e2e.retouch_verification import verify_downloads, _verify_single_native_fixture_alpha


@pytest.mark.parametrize('fault', [None, 'unpaired', 'geometry', 'opaque', 'claim', 'source', 'raw_digest'])
def test_native_alpha_is_checked_against_the_same_placed_provider_reply(fault):
    import numpy as np
    original = np.full((24, 32, 3), 100, dtype=np.uint8)
    rgba = np.full((24, 32, 4), 255, dtype=np.uint8)
    rgba[:, 0, 3] = 0
    alpha = np.zeros((24, 32), dtype=np.uint8)
    alpha[3:15, 4:20] = rgba[:12, :16, 3]
    frame = {'x': 4, 'y': 3, 'width': 16, 'height': 12}
    plan = {'input_size': [32, 24], 'frame': frame,
            'source_hash': hashlib.sha256(original.tobytes()).hexdigest()}
    if fault == 'unpaired': alpha[5, 6] = 128
    if fault == 'geometry': plan['input_size'] = [31, 24]
    if fault == 'opaque': rgba[..., 3] = 255
    if fault == 'source': plan['source_hash'] = 'wrong-source'
    stream = io.BytesIO(); Image.fromarray(rgba).save(stream, format='PNG'); raw = stream.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    metadata = {'request_id': 'request.retouch', 'plan': plan}
    response = {'request_id': 'request.retouch', 'media': {'sha256': digest}}
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE layered_artifacts(id,series_id,metadata,body,digest)')
        db.execute('CREATE TABLE layered_job_claims(request_id,phase,status,response)')
        db.execute('INSERT INTO layered_artifacts VALUES(?,?,?,?,?)',
            ('reply', 'series', json.dumps(metadata), raw, 'wrong' if fault == 'raw_digest' else digest))
        phase = 'image:retouch-0:0'
        db.execute('INSERT INTO layered_job_claims VALUES(?,?,?,?)',
            ('request.retouch', 'image:background:0' if fault == 'claim' else phase, 'completed', json.dumps(response)))
        series = {'id': 'series', 'native_raw_receipts': {phase: 'reply'}}
        if fault:
            with pytest.raises(ValueError):
                _verify_single_native_fixture_alpha(db, series, {'group': 0}, original, alpha)
        else:
            assert _verify_single_native_fixture_alpha(db, series, {'group': 0}, original, alpha) == {
                'alpha_source': 'same_retouch_reply', 'retouch_request_id': 'request.retouch'}


@pytest.mark.parametrize('fault', [None, 'protected', 'overlay', 'download', 'escape','missing_owner'])
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
        # Match the real Store contract: run IDs and retained download ownership
        # remain available when a user has several completed iterations.
        db.execute('CREATE TABLE records(kind,id,body)'); db.execute('CREATE TABLE assets(digest,base)')
        db.execute('INSERT INTO records VALUES(?,?,?)', ('runs','run.checked', json.dumps(run)))
        for fmt in ('PNG','JPEG'):
            download={'filename':'result.'+fmt.lower(),'format':fmt,'run_id':'run.checked'}
            if fault=='missing_owner' and fmt=='PNG':download.pop('run_id')
            db.execute('INSERT INTO records VALUES(?,?,?)',('downloads',fmt,json.dumps(download)))
        db.execute('INSERT INTO assets VALUES(?,?)', ('input', encode(base, 'PNG')))
    report = {'downloads': [{'filename': 'result.'+row['format'].lower(),
                             'digest':'sha256:'+row['sha256']} for row in exports]}
    if fault == 'download': (output / 'result.png').write_bytes(encode(base, 'PNG'))
    if fault == 'escape': report['downloads'][0]['filename'] = '../result.png'
    if fault:
        with pytest.raises(ValueError): verify_downloads(owned_root=owned, output=output, report=report)
    else:
        assert verify_downloads(owned_root=owned, output=output, report=report)['changed_pixels'] == 1
