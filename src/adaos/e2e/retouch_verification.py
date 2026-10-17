"""Independent assertions over downloaded artifacts; imports no Application code."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sqlite3

from PIL import Image, ImageChops, ImageDraw


def verify_downloads(*, owned_root: Path, output: Path, report: dict) -> dict:
    with sqlite3.connect(owned_root / "data/retouch.sqlite3") as db:
        rows = db.execute("SELECT body FROM records WHERE kind='runs'").fetchall()
        runs = [json.loads(row[0]) for row in rows]
        completed = [run for run in runs if run.get("status") == "completed"]
        if len(completed) != 1:
            raise ValueError("Expected exactly one completed fixture run")
        run = completed[0]
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
        export = next(row for row in run["exports"] if row["format"] == image.format)
        if digest != export["sha256"] or image.size != base.size:
            raise ValueError("Downloaded bytes or geometry disagree with the selected export")
        downloaded[image.format] = image.convert("RGB")
    if set(downloaded) != {"PNG", "JPEG"}:
        raise ValueError("Both PNG and JPEG downloads are required")
    red, green, blue = ImageChops.difference(base, downloaded["PNG"]).split()
    changed = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value else 0)
    # This fixture deliberately selects the whole image in rectangle mode and
    # paints one red circle. Assert the exact declared input before using it.
    mask = run["snapshot"]["mask"]
    w, h = base.size
    if (mask["permission_mode"] != "rectangle" or mask["context_border_px"] != 24
        or mask["crop"] != {"x": 0, "y": 0, "width": w, "height": h}
        or mask["strokes"] != [{"kind": "protect", "points": [{"x": 50, "y": 50}], "radius": 8}]):
        raise ValueError("Independent pixel checker does not cover this fixture mask")
    protected = Image.new('L', (w, h))
    paint = ImageDraw.Draw(protected)
    for bounds in [(0, 0, w-1, 23), (0, h-24, w-1, h-1), (0, 0, 23, h-1), (w-24, 0, w-1, h-1)]:
        paint.rectangle(bounds, fill=255)
    paint.ellipse((42, 42, 58, 58), fill=255)
    if changed.getbbox() is None or ImageChops.multiply(changed, protected).getbbox() is not None:
        raise ValueError("Downloaded PNG is unchanged or modified protected pixels")
    overlay = Image.open(output / "difference-map.png").convert("RGBA")
    expected_alpha = changed.point(lambda value: 0 if value else 128)
    if overlay.size != base.size or ImageChops.difference(overlay.getchannel('A'), expected_alpha).getbbox() is not None:
        raise ValueError("Difference overlay does not match exact decoded PNG pixel differences")
    return {"passed": True, "dimensions": [w, h], "changed_pixels": changed.histogram()[255],
            "protected_pixels": protected.histogram()[255], "protected_pixels_equal": True,
            "difference_overlay_exact": True, "downloads_match_export_digests": True,
            "scope": "Decoded lossless base vs downloaded PNG; JPEG geometry/digest only"}
