"""Private, checksum-pinned local blob staging for Application cutover."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


MAX_BLOB_OBJECT_BYTES = 512 * 1024 * 1024
MAX_BLOB_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_BLOB_OBJECTS = 100_000


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _tree_digest(entries: list[dict[str, object]]) -> str:
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class BlobDataTransition:
    """Snapshot and install Core-owned immutable local blob objects.

    Only the filesystem provider's content-addressed layout is admitted. Remote
    providers need their own provider-native version/copy adapter.
    """

    def __init__(self, private_root: Path):
        self.root = private_root.resolve()

    def _path(self, path: Path) -> Path:
        absolute = path.absolute()
        resolved = path.resolve()
        if absolute != resolved or not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("Blob transition path escaped its private owner root or used a link")
        return resolved

    @staticmethod
    def _object_identity(relative: Path) -> tuple[str, str]:
        parts = relative.parts
        if len(parts) != 4 or parts[1] != "objects":
            raise ValueError("Blob data contains an object outside the owned content-addressed layout")
        logical, prefix, filename = parts[0], parts[2], parts[3]
        if (not logical or len(logical) > 64
                or not logical.replace("_", "").replace("-", "").isalnum()
                or len(prefix) != 2 or any(character not in "0123456789abcdef" for character in prefix)):
            raise ValueError("Blob data contains an invalid logical binding or digest prefix")
        digest, dot, suffix = filename.partition(".")
        if (len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
                or prefix != digest[:2] or (dot and (not suffix.isalnum() or len(suffix) > 15))):
            raise ValueError("Blob data contains an invalid content-addressed object name")
        return logical, digest

    def inventory(self, root: Path | None) -> dict[str, object]:
        if root is None or not root.exists():
            entries: list[dict[str, object]] = []
            return {"digest": _tree_digest(entries), "bytes": 0, "objects": 0, "entries": entries}
        root = self._path(root)
        if not root.is_dir():
            raise ValueError("Blob binding root must be a directory")
        entries = []
        total = 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("Linked blob data requires an explicit storage adapter")
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("Blob object escaped its owner root")
            relative = path.relative_to(root)
            _logical, expected = self._object_identity(relative)
            size = path.stat().st_size
            if size > MAX_BLOB_OBJECT_BYTES:
                raise ValueError("Blob object exceeds the local cutover limit")
            total += size
            if total > MAX_BLOB_TOTAL_BYTES or len(entries) >= MAX_BLOB_OBJECTS:
                raise ValueError("Blob collection exceeds the bounded local cutover limits")
            digest = _file_digest(path)
            if digest != f"sha256:{expected}":
                raise ValueError("Blob object bytes do not match its content-addressed path")
            entries.append({"path": relative.as_posix(), "digest": digest, "bytes": size})
        return {"digest": _tree_digest(entries), "bytes": total, "objects": len(entries), "entries": entries}

    def snapshot(self, source: Path | None, destination: Path, *, operation_key: str) -> dict[str, object]:
        destination = self._path(destination)
        source_path = self._path(source) if source is not None and source.exists() else None
        identity = {
            "kind": "blob_snapshot",
            "operation_key": operation_key,
            "source": str(source_path.relative_to(self.root)) if source_path is not None else None,
        }
        return self._build(source_path, destination, identity)

    def _build(self, source: Path | None, destination: Path, identity: dict[str, object]) -> dict[str, object]:
        if not identity.get("operation_key") or len(str(identity["operation_key"])) > 256:
            raise ValueError("A bounded blob operation key is required")
        metadata = destination.parent / f"{destination.name}.receipt.json"
        pending = destination.parent / f"{destination.name}.pending.json"
        with mutation_lock(destination.parent / f"{destination.name}.lock"):
            if metadata.exists():
                receipt = json.loads(metadata.read_text(encoding="utf-8"))
                current = self.inventory(destination)
                if receipt["identity"] != identity or current["digest"] != receipt["digest"]:
                    raise ValueError("Retained blob staging evidence changed")
                return receipt
            if pending.exists():
                saved = json.loads(pending.read_text(encoding="utf-8"))
                receipt = saved["receipt"]
                if receipt["identity"] != identity:
                    raise ValueError("Pending blob staging intent changed")
                temporary = self._path(destination.parent / saved["temporary"])
                candidate = destination if destination.exists() else temporary
                if self.inventory(candidate)["digest"] != receipt["digest"]:
                    raise ValueError("Pending blob staging content changed")
                if not destination.exists():
                    os.replace(temporary, destination)
                atomic_write_json(metadata, receipt)
                pending.unlink()
                return receipt
            if destination.exists():
                raise ValueError("Unrecognized blob destination requires explicit recovery")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path(destination.parent / f".{destination.name}.{uuid.uuid4().hex}.staging")
            started = time.monotonic()
            try:
                temporary.mkdir()
                if source is not None:
                    for entry in self.inventory(source)["entries"]:
                        relative = Path(str(entry["path"]))
                        target = temporary / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source / relative, target)
                staged = self.inventory(temporary)
                receipt = {
                    "ok": True,
                    "identity": identity,
                    "digest": staged["digest"],
                    "bytes": staged["bytes"],
                    "objects": staged["objects"],
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                }
                atomic_write_json(pending, {"temporary": temporary.name, "receipt": receipt})
                os.replace(temporary, destination)
                atomic_write_json(metadata, receipt)
                pending.unlink()
                return receipt
            finally:
                if temporary.exists() and not pending.exists():
                    shutil.rmtree(temporary)

    def install(self, staged: Path, target: Path, *, staged_digest: str) -> dict[str, object]:
        staged, target = self._path(staged), self._path(target)
        source = self.inventory(staged)
        if staged == target or source["digest"] != staged_digest:
            raise ValueError("Staged blob identity mismatch")
        target.mkdir(parents=True, exist_ok=True)
        before = self.inventory(target)
        for entry in source["entries"]:
            relative = Path(str(entry["path"]))
            destination = target / relative
            if destination.exists():
                if _file_digest(destination) != entry["digest"]:
                    raise ValueError("Target blob path contains different bytes")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.staging")
            try:
                shutil.copyfile(staged / relative, temporary)
                if _file_digest(temporary) != entry["digest"]:
                    raise ValueError("Copied blob bytes changed during installation")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        after = self.inventory(target)
        return {
            "ok": True,
            "staged_digest": staged_digest,
            "objects_before": before["objects"],
            "objects_after": after["objects"],
        }
