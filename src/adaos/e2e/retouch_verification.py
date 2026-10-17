"""Independent assertions over downloaded artifacts; imports no Application code."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sqlite3

import numpy as np
from PIL import Image


def verify_downloads(*, owned_root: Path, output: Path, report: dict) -> dict:
    with sqlite3.connect(owned_root / "data/retouch.sqlite3") as db:
        rows = db.execute("SELECT body FROM records WHERE kind='runs'").fetchall()
        runs = [json.loads(row[0]) for row in rows]
        completed = [run for run in runs if run.get("status") == "completed"]
        if len(completed) != 1:
            raise ValueError("Expected exactly one completed fixture run")
        run = completed[0]
        raw = db.execute("SELECT base FROM assets WHERE digest=?", (run["snapshot"]["photo_sha256"],)).fetchone()[0]
    base = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
    downloaded = {}
    for item in report["downloads"]:
        path = output / item["filename"]
        if path.name != item["filename"]:
            raise ValueError("Download filename escapes owned artifact directory")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        image = Image.open(io.BytesIO(content))
        export = next(row for row in run["exports"] if row["format"] == image.format)
        if digest != export["sha256"] or image.size != (base.shape[1], base.shape[0]):
            raise ValueError("Downloaded bytes or geometry disagree with the selected export")
        downloaded[image.format] = np.asarray(image.convert("RGB"))
    if set(downloaded) != {"PNG", "JPEG"}:
        raise ValueError("Both PNG and JPEG downloads are required")
    changed = np.any(base != downloaded["PNG"], axis=2)
    # This fixture deliberately selects the whole image in rectangle mode and
    # paints one red circle. Assert the exact declared input before using it.
    mask = run["snapshot"]["mask"]
    h, w = changed.shape
    if (mask["permission_mode"] != "rectangle" or mask["context_border_px"] != 24
        or mask["crop"] != {"x": 0, "y": 0, "width": w, "height": h}
        or mask["strokes"] != [{"kind": "protect", "points": [{"x": 50, "y": 50}], "radius": 8}]):
        raise ValueError("Independent pixel checker does not cover this fixture mask")
    protected = np.zeros((h, w), dtype=bool)
    protected[:24] = protected[-24:] = True
    protected[:, :24] = protected[:, -24:] = True
    y, x = np.ogrid[:h, :w]
    protected |= (x - 50)**2 + (y - 50)**2 <= 8**2
    if not changed.any() or changed[protected].any():
        raise ValueError("Downloaded PNG is unchanged or modified protected pixels")
    overlay = np.asarray(Image.open(output / "difference-map.png").convert("RGBA"))
    if overlay.shape[:2] != changed.shape or not np.array_equal(overlay[:, :, 3], np.where(changed, 0, 128)):
        raise ValueError("Difference overlay does not match exact decoded PNG pixel differences")
    return {"passed": True, "dimensions": [w, h], "changed_pixels": int(changed.sum()),
            "protected_pixels": int(protected.sum()), "protected_pixels_equal": True,
            "difference_overlay_exact": True, "downloads_match_export_digests": True,
            "scope": "Decoded lossless base vs downloaded PNG; JPEG geometry/digest only"}
