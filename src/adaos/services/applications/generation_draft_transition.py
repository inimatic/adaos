"""Carry durable SDK jobs across an admitted, fenced local Beta upgrade.

The caller resolves both immutable Application runtimes. No submission, lookup,
credential, arbitrary runtime directory or provider network operation is used.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json

_HEX = re.compile(r"[0-9a-f]{64}")
_SERVICES = ("content-generation", "image-generation")


def _inventory(root: Path, private: Path) -> dict[str, dict]:
    if root.absolute() != root.resolve() or not root.resolve().is_relative_to(private.resolve()):
        raise ValueError("Generation draft root escaped its private runtime")
    entries = {}
    total = 0
    if not root.exists():
        return entries
    for path in sorted(root.rglob("*")):
        if path.absolute() != path.resolve():
            raise ValueError("Linked generation drafts cannot be migrated")
        if path.is_dir():
            continue
        relative = path.relative_to(root)
        if len(relative.parts) != 2 or not _HEX.fullmatch(relative.parts[0]) or not _HEX.fullmatch(path.stem):
            raise ValueError("Unsupported generation draft layout")
        if path.suffix == ".lock":
            continue
        if path.suffix not in {".json", ".png", ".jpg", ".webp"}:
            raise ValueError("Unsupported generation draft file")
        size = path.stat().st_size
        total += size
        if size > 64 * 1024 * 1024 or total > 512 * 1024 * 1024 or len(entries) >= 10000:
            raise ValueError("Generation draft cutover exceeds local limits")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if path.suffix == ".json":
            record = json.loads(raw)
            request_id = record.get("request_id")
            if not isinstance(request_id, str) or hashlib.sha256(request_id.encode()).hexdigest() != path.stem:
                raise ValueError("Generation draft request identity mismatch")
        elif digest != path.stem:
            raise ValueError("Generation asset digest mismatch")
        entries[relative.as_posix()] = {"sha256": digest, "bytes": size}
    return entries


def preserve_generation_drafts(*, source_runtime: Path, target_runtime: Path,
                               recovery: Path, private: Path, operation_key: str) -> dict:
    """Snapshot and install only existing jobs; run under the Application fence."""
    receipt_path = recovery / "generation-drafts.json"
    source = source_runtime / ".runtime/state"
    target = target_runtime / ".runtime/state"
    entries = {name: _inventory(source / name, private) for name in _SERVICES}
    identity = {"operation_key": operation_key, "source": str(source), "target": str(target), "entries": entries}
    if receipt_path.exists():
        saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        if saved != identity:
            raise ValueError("Generation draft upgrade source changed during recovery")
    else:
        for name, files in entries.items():
            for relative in files:
                atomic_write_bytes(recovery / name / relative, (source / name / relative).read_bytes())
        atomic_write_json(receipt_path, identity)
    for name, files in entries.items():
        existing = _inventory(target / name, private)
        if any(relative not in files or files[relative] != metadata for relative, metadata in existing.items()):
            raise ValueError("Target generation drafts conflict with the upgrade source")
        for relative, metadata in files.items():
            raw = (recovery / name / relative).read_bytes()
            if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
                raise ValueError("Retained generation draft snapshot changed")
            atomic_write_bytes(target / name / relative, raw)
        if _inventory(target / name, private) != files:
            raise ValueError("Generation draft installation verification failed")
    return {"ok": True, "services": {name: len(files) for name, files in entries.items()},
            "submissions": 0, "mode": "retain_owned_job_identities"}
