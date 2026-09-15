"""Explicit, compare-and-swap modal presentation edits in existing DEV source only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from adaos.sdk.developer import projects
from adaos.sdk.core._ctx import require_ctx
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


def _modal(payload: Any, identifier: str) -> dict[str, Any]:
    matches = []
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            modals = value.get("modals")
            if isinstance(modals, dict):
                for key, item in modals.items():
                    if isinstance(item, dict) and (key == identifier or (item.get("schema") or {}).get("id") == identifier):
                        matches.append(item)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(payload)
    if len(matches) != 1:
        raise ValueError("Modal must have one unambiguous declaration in DEV source")
    return matches[0]


def read(kind: str, object_id: str, modal_id: str) -> dict[str, Any]:
    root = projects.resolve_root(kind, object_id)
    _, path = projects._file(root, "webui.json")
    raw = path.read_bytes()
    node = _modal(json.loads(raw.decode("utf-8-sig")), modal_id)
    return {"kind": kind, "object_id": object_id, "modal_id": modal_id, "path": str(path),
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(), "presentation": node.get("presentation") or {}}


def write(kind: str, object_id: str, modal_id: str, *, width: float, height: float,
          expected_digest: str) -> dict[str, Any]:
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 25 <= value <= 100 for value in (width, height)):
        raise ValueError("Modal dimensions must be viewport percentages in 25..100")
    root = projects.resolve_root(kind, object_id)
    _, path = projects._file(root, "webui.json")
    lock_id = hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()
    lock = Path(require_ctx("sdk.developer.modal_settings").paths.state_dir()) / "developer" / "document-locks" / lock_id
    with mutation_lock(lock):
        before = read(kind, object_id, modal_id)
        if before["digest"] != expected_digest:
            raise ValueError("DEV source changed; review the destination again")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        node = _modal(payload, modal_id)
        node["presentation"] = {**(node.get("presentation") or {}), "kind": "modal", "size": {"width": width, "height": height}}
        atomic_write_json(path, payload)
    projects._publish_content_changed(kind, object_id, reason="modal_settings_changed", changed_paths=["webui.json"])
    return {"ok": True, **read(kind, object_id, modal_id)}
