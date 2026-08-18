from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from adaos.services.media_core import media_reference_db_path, validate_media_reference_id


def unregister_media_references(
    resource_ids: Iterable[str],
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Remove exact reference records while leaving original media files untouched."""

    normalized: list[str] = []
    seen: set[str] = set()
    for resource_id in resource_ids:
        token = validate_media_reference_id(str(resource_id or ""))
        if token not in seen:
            normalized.append(token)
            seen.add(token)
    if not normalized:
        return {
            "ok": True,
            "requested_count": 0,
            "deleted_count": 0,
            "missing_count": 0,
            "resource_ids": [],
        }

    path = Path(db_path) if db_path is not None else media_reference_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    placeholders = ",".join("?" for _ in normalized)
    with sqlite3.connect(str(path), timeout=30) as connection:
        rows = connection.execute(
            f"SELECT resource_id FROM media_references WHERE resource_id IN ({placeholders})",
            tuple(normalized),
        ).fetchall()
        existing = {str(row[0]) for row in rows}
        connection.execute(
            f"DELETE FROM media_references WHERE resource_id IN ({placeholders})",
            tuple(normalized),
        )
        connection.commit()
    return {
        "ok": True,
        "requested_count": len(normalized),
        "deleted_count": len(existing),
        "missing_count": len(normalized) - len(existing),
        "resource_ids": normalized,
    }


__all__ = ["unregister_media_references"]
