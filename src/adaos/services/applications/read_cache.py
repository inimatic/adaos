"""Disposable small projections of large durable JSON journals."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable
from adaos.services.artifact_pipeline.storage import atomic_write_json


def stat_bound_projection(source: Path, cache: Path, project: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    stat = source.stat()
    fingerprint = [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size]
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if isinstance(cached, dict) and cached.get("source_stat") == fingerprint and isinstance(cached.get("summary"), dict):
        return cached["summary"]
    summary = project()
    current = source.stat()
    if fingerprint == [current.st_mtime_ns, current.st_ctime_ns, current.st_size]:
        try:
            atomic_write_json(cache, {"source_stat": fingerprint, "summary": summary})
        except OSError:
            pass
    return summary
