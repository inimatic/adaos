from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import y_py as Y

from adaos.services.nlu.teacher_events import (
    rebuild_teacher_derived_views,
    teacher_projection_needs_compaction,
)
from adaos.services.nlu.ycoerce import coerce_dict
from adaos.services.yjs.structural_compaction import (
    StructuralCompactionError,
    _decode_snapshot,
    _digest_json,
    _projection,
    _rebuild,
)


RUNTIME_PROJECTION_PREPARE_SCHEMA = "adaos.yjs.runtime_projection_prepare.v1"
RUNTIME_PROJECTION_ROOTS = ("ui", "data", "registry", "runtime", "devices", "state", "webio")


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def prepare_runtime_snapshot(
    snapshot_path: Path | str,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Bound server-owned projections before a cold snapshot reaches the API loop."""

    path = Path(snapshot_path).expanduser().resolve()
    if not path.is_file():
        return {
            "schema": RUNTIME_PROJECTION_PREPARE_SCHEMA,
            "snapshot_path": str(path),
            "changed": False,
            "applied": False,
            "reason": "missing",
        }

    source = path.read_bytes()
    source_digest = _digest_bytes(source)
    source_doc = _decode_snapshot(source)
    data_map = source_doc.get_map("data")
    teacher = coerce_dict(data_map.get("nlu_teacher"))
    before_teacher_bytes = _json_bytes(teacher)
    if not teacher or not teacher_projection_needs_compaction(teacher):
        return {
            "schema": RUNTIME_PROJECTION_PREPARE_SCHEMA,
            "snapshot_path": str(path),
            "source_digest": source_digest,
            "source_bytes": len(source),
            "teacher_before_bytes": before_teacher_bytes,
            "teacher_after_bytes": before_teacher_bytes,
            "changed": False,
            "applied": False,
            "reason": "projection_within_budget",
        }

    bounded_teacher = json.loads(json.dumps(teacher, ensure_ascii=False, default=str))
    rebuild_teacher_derived_views(bounded_teacher)
    after_teacher_bytes = _json_bytes(bounded_teacher)
    if bounded_teacher == teacher:
        return {
            "schema": RUNTIME_PROJECTION_PREPARE_SCHEMA,
            "snapshot_path": str(path),
            "source_digest": source_digest,
            "source_bytes": len(source),
            "teacher_before_bytes": before_teacher_bytes,
            "teacher_after_bytes": after_teacher_bytes,
            "changed": False,
            "applied": False,
            "reason": "projection_unchanged",
        }

    with source_doc.begin_transaction() as txn:
        data_map.set(txn, "nlu_teacher", bounded_teacher)
    expected_semantic, expected_shape = _projection(source_doc, RUNTIME_PROJECTION_ROOTS)
    compacted = _rebuild(source_doc, RUNTIME_PROJECTION_ROOTS)
    rebuilt_doc = _decode_snapshot(compacted)
    rebuilt_semantic, rebuilt_shape = _projection(rebuilt_doc, RUNTIME_PROJECTION_ROOTS)
    if _digest_json(rebuilt_semantic) != _digest_json(expected_semantic):
        raise StructuralCompactionError("runtime projection preparation changed semantic state")
    if _digest_json(rebuilt_shape) != _digest_json(expected_shape):
        raise StructuralCompactionError("runtime projection preparation changed shared-type topology")

    result = {
        "schema": RUNTIME_PROJECTION_PREPARE_SCHEMA,
        "snapshot_path": str(path),
        "source_digest": source_digest,
        "source_bytes": len(source),
        "compacted_digest": _digest_bytes(compacted),
        "compacted_bytes": len(compacted),
        "teacher_before_bytes": before_teacher_bytes,
        "teacher_after_bytes": after_teacher_bytes,
        "changed": True,
        "applied": False,
        "reason": "nlu_teacher_projection_bounded",
    }
    if not apply:
        return result
    if _digest_bytes(path.read_bytes()) != source_digest:
        raise StructuralCompactionError("Yjs snapshot changed before runtime projection commit")

    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".projection", dir=path.parent)
    tmp_path = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(compacted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)
    if _digest_bytes(path.read_bytes()) != result["compacted_digest"]:
        raise StructuralCompactionError("prepared Yjs snapshot digest changed during commit")
    result["applied"] = True
    return result


__all__ = [
    "RUNTIME_PROJECTION_PREPARE_SCHEMA",
    "RUNTIME_PROJECTION_ROOTS",
    "prepare_runtime_snapshot",
]
