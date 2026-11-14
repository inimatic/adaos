"""Continuing, bounded metadata inventory. Never follows nested reparse points."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import time

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .storage import digest, now, read


class StorageInventory:
    def __init__(self, state):
        self.state = Path(state) / "inventories"

    def path(self, role):
        return self.state / f"{digest(role)}.json"

    def snapshot(self, role, root):
        value = read(self.path(role))
        if not value or value.get("root") != str(Path(root).resolve()):
            return {"role": role, "status": "not_measured", "bytes": None, "files": None,
                    "complete": False, "observed_at": None}
        return {k: v for k, v in value.items() if k != "pending"}

    def advance(self, role, root, *, max_entries=2000, max_seconds=.25, restart=False):
        root = Path(root).resolve()
        location = self.path(role)
        with mutation_lock(location.with_suffix(".lock"), timeout_s=.5):
            value = None if restart else read(location)
            if not value or value.get("root") != str(root):
                value = {"schema": "adaos.node.storage_inventory.v1", "role": role, "root": str(root),
                         "bytes": 0, "files": 0, "directories": 0, "skipped_links": 0, "errors": 0,
                         "started_at": now(), "observed_at": None, "complete": False, "status": "running",
                         "pending": [{"path": ".", "offset": 0}], "slices": 0}
            if value["complete"]:
                return self.snapshot(role, root)
            deadline = time.monotonic() + max(.01, min(float(max_seconds), 5))
            consumed = 0
            while value["pending"] and consumed < max_entries and time.monotonic() < deadline:
                frame = value["pending"].pop()
                directory = root / frame["path"]
                try:
                    info = directory.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        value["skipped_links"] += 1
                        continue
                    if not stat.S_ISDIR(info.st_mode):
                        value["errors"] += 1
                        continue
                    resume = None
                    children = []
                    with os.scandir(directory) as entries:
                        for index, entry in enumerate(entries):
                            if index < frame["offset"]:
                                if time.monotonic() >= deadline:
                                    resume = frame
                                    break
                                continue
                            if consumed >= max_entries or time.monotonic() >= deadline:
                                resume = {"path": frame["path"], "offset": index}
                                break
                            consumed += 1
                            try:
                                info = entry.stat(follow_symlinks=False)
                                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                                    value["skipped_links"] += 1
                                elif stat.S_ISDIR(info.st_mode):
                                    children.append({"path": str(Path(entry.path).relative_to(root)), "offset": 0})
                                    value["directories"] += 1
                                else:
                                    value["bytes"] += info.st_size
                                    value["files"] += 1
                            except OSError:
                                value["errors"] += 1
                    if resume:
                        value["pending"].append(resume)
                    value["pending"].extend(children)
                except FileNotFoundError:
                    value["errors"] += 1
                except OSError:
                    value["errors"] += 1
            value["slices"] += 1
            value["observed_at"] = now()
            value["complete"] = not value["pending"] and value["errors"] == 0
            value["status"] = "complete" if value["complete"] else ("incomplete" if not value["pending"] else "running")
            value["measurement"] = "logical_bytes_without_links; observation_interval_not_atomic"
            atomic_write_json(location, value)
            return {k: v for k, v in value.items() if k != "pending"}
