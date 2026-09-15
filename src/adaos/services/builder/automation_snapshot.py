"""Read the retained Automation UI with exact task identity, never DEV fallback."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from adaos.services.artifact_pipeline.storage import mutation_lock


def snapshot_lock_path(root: Path) -> Path:
    return root.parent / ".automation-snapshot.lock"


def read_automation_ui(root: Path, scenario_id: str, task_id: str | None) -> dict:
    with mutation_lock(snapshot_lock_path(root)):
        try:
            metadata = json.loads((root / "snapshot.json").read_text(encoding="utf-8-sig"))
            if (not isinstance(metadata, dict) or metadata.get("object_type") != "scenario" or metadata.get("object_id") != scenario_id
                    or not metadata.get("task_id") or (task_id and metadata.get("task_id") != task_id)):
                raise ValueError("Automation Preview requires the exact retained task revision")
            raw = (root / "webui.json").read_bytes()
            digests = metadata.get("file_digests")
            if isinstance(digests, dict) and digests.get("webui.json") != hashlib.sha256(raw).hexdigest():
                raise ValueError("Automation Preview snapshot content changed")
            content = json.loads(raw.decode("utf-8-sig"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Automation Preview snapshot is unavailable; no DEV fallback is allowed") from exc
        if (not isinstance(content, dict) or not isinstance(content.get("ui"), dict)
                or not isinstance(content["ui"].get("application"), dict)):
            raise ValueError("Automation Preview snapshot has no application UI")
        return content
