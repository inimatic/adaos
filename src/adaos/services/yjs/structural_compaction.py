from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import y_py as Y

from adaos.services.yjs.json_merge import clone_json_like
from adaos.services.yjs.store import ystore_path_for_webspace


STRUCTURAL_COMPACTION_SCHEMA = "adaos.yjs.structural_compaction.v1"
DEFAULT_ROOT_NAMES = ("ui", "data", "registry", "runtime", "devices")
OFFLINE_WITNESS = "api-stopped-and-webspace-quiesced"


class StructuralCompactionError(RuntimeError):
    """Raised when an offline Yjs snapshot cannot be rebuilt safely."""


def _digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _digest_json(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _digest_bytes(raw)


def _normalized_roots(root_names: Iterable[str]) -> tuple[str, ...]:
    roots = tuple(
        dict.fromkeys(str(item or "").strip() for item in root_names if str(item or "").strip())
    )
    if not roots:
        raise StructuralCompactionError("at least one Yjs root name is required")
    if any(len(item) > 100 for item in roots):
        raise StructuralCompactionError("Yjs root name exceeds 100 characters")
    return roots


def _decode_snapshot(payload: bytes) -> Y.YDoc:
    if not payload:
        raise StructuralCompactionError("Yjs snapshot is empty")
    doc = Y.YDoc()
    try:
        Y.apply_update(doc, payload)
    except Exception as exc:
        raise StructuralCompactionError(
            f"Yjs snapshot decode failed: {type(exc).__name__}: {exc}"
        ) from exc
    return doc


def _semantic_value(value: Any) -> Any:
    if isinstance(value, Y.YMap):
        return {str(key): _semantic_value(item) for key, item in value.items()}
    if isinstance(value, Y.YArray):
        return [_semantic_value(item) for item in value]
    if isinstance(value, Y.YText):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _semantic_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_semantic_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise StructuralCompactionError(
        f"unsupported shared Yjs value during structural compaction: {type(value).__name__}"
    )


def _type_shape(value: Any) -> Any:
    if isinstance(value, Y.YMap):
        return {
            "$type": "ymap",
            "items": {str(key): _type_shape(item) for key, item in value.items()},
        }
    if isinstance(value, Y.YArray):
        return {"$type": "yarray", "items": [_type_shape(item) for item in value]}
    if isinstance(value, Y.YText):
        return {"$type": "ytext"}
    if isinstance(value, dict):
        return {
            "$type": "json_object",
            "items": {str(key): _type_shape(item) for key, item in value.items()},
        }
    if isinstance(value, (list, tuple)):
        return {"$type": "json_array", "items": [_type_shape(item) for item in value]}
    if value is None:
        return {"$type": "null"}
    if isinstance(value, bool):
        return {"$type": "boolean"}
    if isinstance(value, (int, float)):
        return {"$type": "number"}
    if isinstance(value, str):
        return {"$type": "string"}
    raise StructuralCompactionError(
        f"unsupported shared Yjs value during shape verification: {type(value).__name__}"
    )


def _projection(doc: Y.YDoc, roots: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    semantic: dict[str, Any] = {}
    shape: dict[str, Any] = {}
    for root_name in roots:
        root = doc.get_map(root_name)
        semantic[root_name] = _semantic_value(root)
        shape[root_name] = _type_shape(root)
    return semantic, shape


def _copy_array(source: Y.YArray, target: Y.YArray, txn: Any) -> None:
    for value in source:
        if isinstance(value, Y.YMap):
            target.append(txn, Y.YMap({}))
            attached = list(target)[-1]
            _copy_map(value, attached, txn)
        elif isinstance(value, Y.YArray):
            target.append(txn, Y.YArray([]))
            attached = list(target)[-1]
            _copy_array(value, attached, txn)
        elif isinstance(value, Y.YText):
            target.append(txn, Y.YText(str(value)))
        else:
            target.append(txn, clone_json_like(value))


def _copy_map(source: Y.YMap, target: Y.YMap, txn: Any) -> None:
    for key, value in source.items():
        selected_key = str(key)
        if isinstance(value, Y.YMap):
            target.set(txn, selected_key, Y.YMap({}))
            _copy_map(value, target.get(selected_key), txn)
        elif isinstance(value, Y.YArray):
            target.set(txn, selected_key, Y.YArray([]))
            _copy_array(value, target.get(selected_key), txn)
        elif isinstance(value, Y.YText):
            target.set(txn, selected_key, Y.YText(str(value)))
        else:
            target.set(txn, selected_key, clone_json_like(value))


def _rebuild(source: Y.YDoc, roots: tuple[str, ...]) -> bytes:
    rebuilt = Y.YDoc()
    with rebuilt.begin_transaction() as txn:
        for root_name in roots:
            _copy_map(source.get_map(root_name), rebuilt.get_map(root_name), txn)
    return bytes(Y.encode_state_as_update(rebuilt))


def inspect_snapshot(
    snapshot_path: Path | str,
    *,
    root_names: Iterable[str] = DEFAULT_ROOT_NAMES,
) -> dict[str, Any]:
    """Return immutable source, semantic, and shared-type digests for a snapshot."""

    path = Path(snapshot_path).expanduser().resolve()
    if not path.is_file():
        raise StructuralCompactionError(f"Yjs snapshot does not exist: {path}")
    roots = _normalized_roots(root_names)
    payload = path.read_bytes()
    doc = _decode_snapshot(payload)
    semantic, shape = _projection(doc, roots)
    return {
        "schema": STRUCTURAL_COMPACTION_SCHEMA,
        "snapshot_path": str(path),
        "root_names": list(roots),
        "source_digest": _digest_bytes(payload),
        "source_bytes": len(payload),
        "semantic_digest": _digest_json(semantic),
        "shared_type_digest": _digest_json(shape),
    }


def compact_snapshot(
    snapshot_path: Path | str,
    *,
    expected_source_digest: str,
    offline_witness: str,
    root_names: Iterable[str] = DEFAULT_ROOT_NAMES,
    apply: bool = False,
) -> dict[str, Any]:
    """Rebuild an offline Yjs snapshot with backup and fail-closed verification.

    Structural compaction intentionally has no force/live switch. Applying it
    requires an exact source digest and an explicit witness that the API and
    Webspace writers are quiesced. The original backup is retained after both
    success and rollback.
    """

    path = Path(snapshot_path).expanduser().resolve()
    roots = _normalized_roots(root_names)
    if str(offline_witness or "").strip() != OFFLINE_WITNESS:
        raise StructuralCompactionError(
            f"offline witness must be exactly {OFFLINE_WITNESS!r}"
        )
    source = path.read_bytes() if path.is_file() else b""
    source_digest = _digest_bytes(source)
    if source_digest != str(expected_source_digest or "").strip():
        raise StructuralCompactionError("Yjs snapshot source digest changed")
    source_doc = _decode_snapshot(source)
    source_semantic, source_shape = _projection(source_doc, roots)
    compacted = _rebuild(source_doc, roots)
    rebuilt_doc = _decode_snapshot(compacted)
    rebuilt_semantic, rebuilt_shape = _projection(rebuilt_doc, roots)
    semantic_digest = _digest_json(source_semantic)
    type_digest = _digest_json(source_shape)
    if _digest_json(rebuilt_semantic) != semantic_digest:
        raise StructuralCompactionError("compacted Yjs snapshot changed semantic state")
    if _digest_json(rebuilt_shape) != type_digest:
        raise StructuralCompactionError("compacted Yjs snapshot changed shared-type topology")

    result = {
        "schema": STRUCTURAL_COMPACTION_SCHEMA,
        "snapshot_path": str(path),
        "root_names": list(roots),
        "source_digest": source_digest,
        "source_bytes": len(source),
        "compacted_digest": _digest_bytes(compacted),
        "compacted_bytes": len(compacted),
        "semantic_digest": semantic_digest,
        "shared_type_digest": type_digest,
        "applied": False,
        "backup_path": None,
        "rolled_back": False,
    }
    if not apply:
        return result

    # Recheck immediately before the transaction boundary. A writer changing
    # the snapshot after the initial read invalidates the operation.
    if _digest_bytes(path.read_bytes()) != source_digest:
        raise StructuralCompactionError("Yjs snapshot changed before compaction commit")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(
        f"{path.name}.structural-backup.{stamp}.{source_digest.removeprefix('sha256:')[:12]}"
    )
    shutil.copy2(path, backup)
    tmp_path: Path | None = None
    replaced = False
    try:
        fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".compact", dir=path.parent)
        tmp_path = Path(raw_tmp)
        with os.fdopen(fd, "wb") as stream:
            stream.write(compacted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
        replaced = True
        final = inspect_snapshot(path, root_names=roots)
        if final["source_digest"] != result["compacted_digest"]:
            raise StructuralCompactionError("compacted snapshot digest changed during commit")
        if final["semantic_digest"] != semantic_digest:
            raise StructuralCompactionError("committed snapshot failed semantic verification")
        if final["shared_type_digest"] != type_digest:
            raise StructuralCompactionError("committed snapshot failed shared-type verification")
    except Exception:
        if replaced:
            rollback_tmp = path.with_name(f".{path.name}.rollback")
            shutil.copy2(backup, rollback_tmp)
            os.replace(rollback_tmp, path)
            result["rolled_back"] = True
        raise
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    result["applied"] = True
    result["backup_path"] = str(backup)
    return result


def compact_webspace_snapshot(
    webspace_id: str,
    *,
    expected_source_digest: str,
    offline_witness: str,
    root_names: Iterable[str] = DEFAULT_ROOT_NAMES,
    apply: bool = False,
) -> dict[str, Any]:
    selected = str(webspace_id or "").strip()
    if not selected:
        raise StructuralCompactionError("webspace_id is required")
    # This detects unsafe in-process use. Cross-process callers must still
    # provide the explicit offline witness; there is deliberately no override.
    try:
        from adaos.services.yjs.gateway_ws import live_webspace_ids

        if selected in live_webspace_ids():
            raise StructuralCompactionError(
                f"cannot structurally compact a live Webspace: {selected}"
            )
    except ImportError:
        pass
    result = compact_snapshot(
        ystore_path_for_webspace(selected),
        expected_source_digest=expected_source_digest,
        offline_witness=offline_witness,
        root_names=root_names,
        apply=apply,
    )
    result["webspace_id"] = selected
    return result


__all__ = [
    "DEFAULT_ROOT_NAMES",
    "OFFLINE_WITNESS",
    "STRUCTURAL_COMPACTION_SCHEMA",
    "StructuralCompactionError",
    "compact_snapshot",
    "compact_webspace_snapshot",
    "inspect_snapshot",
]
