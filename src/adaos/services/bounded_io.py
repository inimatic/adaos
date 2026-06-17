from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip() or str(default))
    except Exception:
        value = default
    if value < minimum:
        value = minimum
    return value


def bounded_text_tail_lines(
    path: Path,
    *,
    limit: int = 20,
    max_bytes: int = 256 * 1024,
    max_line_chars: int = 4096,
    encoding: str = "utf-8",
) -> list[str]:
    line_count = max(0, int(limit or 0))
    if line_count <= 0:
        return []
    read_limit = max(4096, int(max_bytes or 0))
    line_limit = max(256, int(max_line_chars or 0))
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            start = max(0, int(size) - read_limit)
            handle.seek(start)
            if start:
                handle.readline()
            data = handle.read(read_limit)
    except Exception:
        return []
    lines = data.decode(encoding, errors="replace").splitlines()[-line_count:]
    result: list[str] = []
    for line in lines:
        if len(line) > line_limit:
            result.append(f"{line[:line_limit]}...<truncated chars={len(line)}>")
        else:
            result.append(line)
    return result


def bounded_jsonl_tail(
    path: Path,
    *,
    limit: int = 20,
    max_bytes: int = 256 * 1024,
    max_line_chars: int = 64 * 1024,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in bounded_text_tail_lines(
        path,
        limit=limit,
        max_bytes=max_bytes,
        max_line_chars=max_line_chars,
    ):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def rotate_file_if_needed(path: Path, *, max_bytes: int, backup_count: int = 5) -> bool:
    if int(max_bytes or 0) <= 0:
        return False
    try:
        if path.stat().st_size <= int(max_bytes):
            return False
    except FileNotFoundError:
        return False
    except Exception:
        return False

    backups = max(0, int(backup_count or 0))
    try:
        if backups <= 0:
            path.unlink(missing_ok=True)
            return True
        backup_prefix = f"{path.name}."
        for existing in path.parent.glob(f"{path.name}.*"):
            suffix = existing.name[len(backup_prefix) :] if existing.name.startswith(backup_prefix) else ""
            if suffix.isdigit() and int(suffix) >= backups:
                existing.unlink(missing_ok=True)
        for index in range(backups - 1, 0, -1):
            src = path.with_name(f"{path.name}.{index}")
            if src.exists():
                src.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        return True
    except Exception:
        return False


def path_size_snapshot(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        try:
            stat = path.stat()
            rows.append(
                {
                    "path": str(path),
                    "exists": True,
                    "size_bytes": int(stat.st_size),
                    "mtime": float(stat.st_mtime),
                }
            )
        except FileNotFoundError:
            rows.append({"path": str(path), "exists": False})
        except Exception as exc:
            rows.append({"path": str(path), "exists": None, "error": f"{type(exc).__name__}: {exc}"})
    return rows
