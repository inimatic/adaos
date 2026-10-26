"""Independent assertions over downloaded artifacts; imports no Application code."""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3

from PIL import Image, ImageChops, ImageDraw


def verify_layered_downloads(*, owned_root: Path, output: Path, report: dict) -> dict:
    """Verify native bytes and protected support, importing no application code."""
    import numpy as np
    database = (owned_root / 'data/retouch.sqlite3').resolve()
    downloads = report.get('downloads', [])
    if len(downloads) != 2 or {row.get('format') for row in downloads} != {'PNG', 'JPEG'}:
        raise ValueError('Both native PNG and JPEG downloads are required')
    identities = {row.get('artifact_id') for row in downloads}
    if len(identities) != 1 or None in identities:
        raise ValueError('Downloads must bind to the same selected assembled artifact')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        artifact = db.execute('SELECT * FROM layered_artifacts WHERE id=?', (identities.pop(),)).fetchone()
        if artifact is None or artifact['role'] != 'assembled' or not json.loads(artifact['metadata']).get('exportable'):
            raise ValueError('Selected artifact is not an owned assembled export')
        def record(identity):
            row = db.execute('SELECT body FROM layered_records WHERE id=?', (identity,)).fetchone()
            return json.loads(row[0])
        photo = record(artifact['photo_id']); series = record(artifact['series_id'])
        if series['status'] != 'completed' or photo.get('final_artifact_id') != artifact['id']:
            raise ValueError('Downloads do not bind to the completed selected final')
        source = db.execute('SELECT base FROM assets WHERE digest=?', (photo['sha256'],)).fetchone()[0]
        original = np.asarray(Image.open(io.BytesIO(source)).convert('RGB'))
        variant = next((v for v in series['variants'] if v.get('assembled') == artifact['id']), None)
        if variant is None: raise ValueError('Artifact is absent from the retained series')
        def pixels(identity, mode='L'):
            body = db.execute('SELECT body FROM layered_artifacts WHERE id=?', (identity,)).fetchone()[0]
            return np.asarray(Image.open(io.BytesIO(body)).convert(mode))
        a0 = pixels(series['a0']); a1 = pixels(variant['alpha'])
        support = pixels(variant['repair_support'])
        protected = np.maximum(pixels(variant['protection']), pixels(variant['occluders'])) == 255
        retained = np.asarray(Image.open(io.BytesIO(artifact['body'])).convert('RGB'))
        allowed = ((a0 > 0) | (a1 > 0) | (support > 0)) & ~protected
        changed = np.any(original != retained, axis=2)
        if not changed.any() or np.any(changed & ~allowed):
            raise ValueError('Assembled pixels are unchanged or touch protected/outside support')
        raw = pixels(variant['raw'], 'RGBA')
        if raw[..., 3].min() == 255:
            raise ValueError('Synthetic RGBA response lost its independent alpha')
        adopted = [json.loads(row[0]) for row in db.execute("SELECT body FROM layered_records WHERE kind='photo'")
                   if json.loads(row[0]).get('parent_artifact_id') == artifact['id']]
        if len(adopted) != 1 or '(1)' not in adopted[0]['title']:
            raise ValueError('Adopted version is missing or duplicated')
        digest = hashlib.sha256(artifact['body']).hexdigest()
        if digest != artifact['digest']:
            raise ValueError('Owned assembled artifact digest mismatch')
    for item in downloads:
        name = item.get('path', '')
        if not name or Path(name).name != name:
            raise ValueError('Download path escapes owned output')
        raw = (output / name).read_bytes()
        image = Image.open(io.BytesIO(raw)); image.load()
        if image.format != item['format'] or image.size != (original.shape[1], original.shape[0]):
            raise ValueError('Download format or native geometry mismatch')
        if item['format'] == 'PNG' and raw != artifact['body']:
            raise ValueError('Downloaded PNG differs from selected retained bytes')
    return {'passed': True, 'changed_pixels': int(changed.sum()),
            'unchanged_outside_support': True, 'native_size': [original.shape[1], original.shape[0]],
            'artifact_digest': 'sha256:' + digest, 'independent_alpha': True,
            'provider_quality_claim': False}


def verify_downloads(*, owned_root: Path, output: Path, report: dict) -> dict:
    database = (owned_root / 'data/retouch.sqlite3').resolve()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT id,body FROM records WHERE kind='runs'").fetchall()
        completed = {identity: json.loads(body) for identity,body in rows
                     if json.loads(body).get('status') == 'completed'}
        downloads = [json.loads(row[0]) for row in db.execute("SELECT body FROM records WHERE kind='downloads'")]
        owners = set()
        selected_downloads = {}
        for item in report['downloads']:
            if Path(item['filename']).name != item['filename']:
                raise ValueError('Download filename escapes owned artifact directory')
            matches = [row for row in downloads if row.get('filename') == item['filename']]
            if len(matches) != 1 or not matches[0].get('run_id'):
                raise ValueError('Download must bind to exactly one retained run')
            owners.add(matches[0]['run_id'])
            selected_downloads[item['filename']] = matches[0]
        if len(owners) != 1 or not owners.issubset(completed):
            raise ValueError('Downloads must belong to one completed selected run')
        selected_run_id = owners.pop()
        original_id = (report.get('revision_probe') or {}).get('original_run_id')
        if original_id and original_id != selected_run_id:
            raise ValueError('History probe and downloaded run identities disagree')
        run = completed[selected_run_id]
        raw = db.execute("SELECT base FROM assets WHERE digest=?", (run["snapshot"]["photo_sha256"],)).fetchone()[0]
    base = Image.open(io.BytesIO(raw)).convert("RGB")
    downloaded = {}
    for item in report["downloads"]:
        path = output / item["filename"]
        if path.name != item["filename"]:
            raise ValueError("Download filename escapes owned artifact directory")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        image = Image.open(io.BytesIO(content))
        record = selected_downloads[item['filename']]
        if record.get('variant'):
            receipt = record.get('receipt') or {}
            if (record['variant'] != 'masked' or receipt.get('owner_ref') != 'skill:natural_retouch_skill'
                    or receipt.get('logical_name') != 'exports' or receipt.get('size_bytes') != len(content)
                    or receipt.get('media_type') != {'PNG': 'image/png', 'JPEG': 'image/jpeg'}.get(image.format)):
                raise ValueError('Masked download must bind to its owned artifact receipt')
            expected_digest = receipt.get('digest')
        else:
            export = next(row for row in run["exports"] if row["format"] == image.format)
            expected_digest = 'sha256:' + export['sha256']
        if (expected_digest != 'sha256:' + digest or image.size != base.size
                or item.get('digest') != 'sha256:' + digest):
            raise ValueError("Downloaded bytes or geometry disagree with the selected export")
        downloaded[image.format] = image.convert("RGB")
    if set(downloaded) != {"PNG", "JPEG"}:
        raise ValueError("Both PNG and JPEG downloads are required")
    red, green, blue = ImageChops.difference(base, downloaded["PNG"]).split()
    changed = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value else 0)
    # This fixture deliberately selects the whole image in rectangle mode and
    # paints one red circle. Validate its requested location with subpixel
    # pointer tolerance, then check EVERY protected pixel of the actual input.
    mask = run["snapshot"]["mask"]
    w, h = base.size
    strokes = mask.get("strokes", [])
    if (mask["permission_mode"] != "rectangle" or mask["context_border_px"] != 24
        or mask["crop"] != {"x": 0, "y": 0, "width": w, "height": h}
        or len(strokes) != 1 or set(strokes[0]) != {"kind", "points", "radius"}
        or strokes[0]["kind"] != "protect" or strokes[0]["radius"] != 8
        or len(strokes[0]["points"]) != 1 or set(strokes[0]["points"][0]) != {"x", "y"}):
        raise ValueError("Independent pixel checker does not cover this fixture mask")
    point = strokes[0]["points"][0]
    x, y = float(point["x"]), float(point["y"])
    if any(not math.isfinite(value) or abs(value-50) >= 0.05 for value in (x, y)):
        raise ValueError("Independent pixel checker does not cover this fixture mask")
    protected = Image.new('L', (w, h))
    paint = ImageDraw.Draw(protected)
    for bounds in [(0, 0, w-1, 23), (0, h-24, w-1, h-1), (0, 0, 23, h-1), (w-24, 0, w-1, h-1)]:
        paint.rectangle(bounds, fill=255)
    paint.ellipse((x-8, y-8, x+8, y+8), fill=255)
    if changed.getbbox() is None or ImageChops.multiply(changed, protected).getbbox() is not None:
        raise ValueError("Downloaded PNG is unchanged or modified protected pixels")
    raw_preview_verified = False
    if report.get('raw_preview'):
        preview = report['raw_preview']
        filename = preview.get('filename', '')
        if not filename or Path(filename).name != filename:
            raise ValueError('Raw preview filename escapes owned artifact directory')
        matches = [row for row in downloads if row.get('filename') == filename]
        if (len(matches) != 1 or matches[0].get('run_id') != selected_run_id
                or preview.get('run_id') != selected_run_id or matches[0].get('variant') != 'raw'):
            raise ValueError('Raw preview must bind to the same selected run and raw variant')
        content = (output / filename).read_bytes()
        receipt = matches[0].get('receipt') or {}
        sha = 'sha256:' + hashlib.sha256(content).hexdigest()
        if (preview.get('digest') != sha or receipt.get('digest') != sha
                or receipt.get('size_bytes') != len(content) or receipt.get('owner_ref') != 'skill:natural_retouch_skill'
                or receipt.get('logical_name') != 'exports' or receipt.get('media_type') != 'image/png'):
            raise ValueError('Raw preview does not match its owned artifact receipt')
        raw_image = Image.open(io.BytesIO(content)).convert('RGB')
        expected = base.copy()
        expected.putpixel((100, 100), (161, 101, 91))
        expected.putpixel((50, 50), (71, 181, 91))
        if raw_image.size != base.size or ImageChops.difference(raw_image, expected).getbbox() is not None:
            raise ValueError('Raw preview must expose the complete synthetic model response without mask clipping')
        raw_preview_verified = True
    overlay = Image.open(output / "difference-map.png").convert("RGBA")
    expected_alpha = changed.point(lambda value: 0 if value else 128)
    if overlay.size != base.size or ImageChops.difference(overlay.getchannel('A'), expected_alpha).getbbox() is not None:
        raise ValueError("Difference overlay does not match exact decoded PNG pixel differences")
    return {"passed": True, "selected_run_id": selected_run_id, "completed_run_count": len(completed),
            "dimensions": [w, h], "changed_pixels": changed.histogram()[255],
            "protected_pixels": protected.histogram()[255], "protected_pixels_equal": True,
            "raw_preview_verified": raw_preview_verified,
            "difference_overlay_exact": True, "downloads_match_export_digests": True,
            "scope": "Decoded lossless base vs downloaded PNG; JPEG geometry/digest only"}
