"""Runtime environment helpers for skill A/B deployments.

This module encapsulates the on-disk layout used by the new skill lifecycle
in AdaOS.  The layout is intentionally simple and filesystem friendly so that
it works on both Linux and Windows without relying on advanced features such
as hardlinks or POSIX specific flags.  The public API is intentionally small
so that higher level services (CLI/API) can orchestrate installations,
activations and rollbacks without duplicating path arithmetic.

The structure managed by :class:`SkillRuntimeEnvironment` matches the
requirements from the product brief:

```
skills/<name>/                    # immutable skill sources
skills/.runtime/<name>/<runtime-bucket>/       # v<major>.<minor>
    slots/
        A/
            src/
                skills/<name>/...
                    tests/
            vendor/
            runtime/
                logs/
                tmp/
            resolved.manifest.json
        B/ ...
    active                        # text file with current slot name
    previous                      # optional previous healthy slot
    meta.json                     # bucket metadata (slot versions, tests etc.)
    data/
        db/                       # persistent structured skill state
        files/                    # physical file artifacts/blobs
        internal/                 # schema-bound skill data for this bucket
current_version                   # active full semantic version
current_runtime.json              # active version/slot selection
previous_runtime.json             # previous version/slot selection for rollback
```

The module also provides a thin result object :class:`SkillSlotPaths` with
pre-computed paths that are convenient for callers.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable, Optional


_SLOT_NAMES: tuple[str, ...] = ("A", "B")
_SEMVER_COMPAT_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:[.\-+_].*)?$")
_RUNTIME_BUCKET_RE = re.compile(r"^v\d+\.\d+$")


def _version_sort_key(value: str) -> tuple[int, int, int, str]:
    parts = [0, 0, 0]
    raw_parts = str(value or "").strip().lstrip("vV").split(".")
    for idx, token in enumerate(raw_parts[:3]):
        digits = "".join(ch for ch in token if ch.isdigit())
        if digits:
            try:
                parts[idx] = int(digits)
            except ValueError:
                parts[idx] = 0
    return (parts[0], parts[1], parts[2], str(value or ""))


def _runtime_bucket_name(version: str) -> str:
    token = str(version or "").strip() or "0.0.0"
    match = _SEMVER_COMPAT_RE.match(token)
    if match:
        major = int(match.group(1))
        minor = int(match.group(2) or 0)
        return f"v{major}.{minor}"
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in token).strip("._-")
    return cleaned or "unversioned"


def _is_runtime_bucket_name(value: str) -> bool:
    return bool(_RUNTIME_BUCKET_RE.match(str(value or "").strip()))


@dataclass(frozen=True, slots=True)
class SkillSlotPaths:
    """Convenience wrapper with all runtime paths for a single slot."""

    skill_name: str
    version: str
    slot: str
    root: Path
    src_dir: Path
    vendor_dir: Path
    runtime_dir: Path
    tests_dir: Path
    logs_dir: Path
    tmp_dir: Path
    resolved_manifest: Path
    data_root: Path
    files_dir: Path
    internal_data_dir: Path

    @property
    def skill_env_path(self) -> Path:
        return self.data_root / "db" / "skill_env.json"

    @property
    def skill_memory_path(self) -> Path:
        return self.skill_env_path

    @property
    def legacy_skill_env_path(self) -> Path:
        return self.runtime_dir / ".skill_env.json"

    @property
    def legacy_skill_memory_path(self) -> Path:
        return self.runtime_dir / ".skill_memory.json"


class SkillRuntimeEnvironment:
    """Encapsulates filesystem layout for skill runtime deployments."""

    def __init__(self, *, skills_root: Path, skill_name: str):
        self._skills_root = skills_root
        self._skill_name = skill_name
        self._runtime_root = skills_root / ".runtime" / skill_name

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------
    @property
    def skill_name(self) -> str:
        return self._skill_name

    @property
    def runtime_root(self) -> Path:
        return self._runtime_root

    def runtime_bucket(self, version: str) -> str:
        return _runtime_bucket_name(version)

    def is_runtime_bucket_name(self, value: str) -> bool:
        return _is_runtime_bucket_name(value)

    def runtime_bucket_root(self, version: str) -> Path:
        return (self._runtime_root / self.runtime_bucket(version)).resolve()

    def version_root(self, version: str) -> Path:
        return self.runtime_bucket_root(version)

    def slots_root(self, version: str) -> Path:
        return self.version_root(version) / "slots"

    def slot_root(self, version: str, slot: str) -> Path:
        return self.slots_root(version) / slot

    def data_root(self, version: str | None = None) -> Path:
        target_version = version or self.resolve_active_version() or "0.0.0"
        return self.version_root(target_version) / "data"

    def files_dir(self, version: str | None = None) -> Path:
        return self.data_root(version) / "files"

    def db_dir(self, version: str | None = None) -> Path:
        return self.data_root(version) / "db"

    def internal_root(self, version: str | None = None) -> Path:
        return self.data_root(version) / "internal"

    def skill_env_store_path(self, version: str | None = None) -> Path:
        return self.db_dir(version) / "skill_env.json"

    def skill_memory_store_path(self, version: str | None = None) -> Path:
        return self.skill_env_store_path(version)

    def active_marker(self, version: str) -> Path:
        return self.version_root(version) / "active"

    def previous_marker(self, version: str) -> Path:
        return self.version_root(version) / "previous"

    def metadata_path(self, version: str) -> Path:
        return self.version_root(version) / "meta.json"

    def active_version_marker(self) -> Path:
        return self._runtime_root / "current_version"

    def current_selection_marker(self) -> Path:
        return self._runtime_root / "current_runtime.json"

    def previous_selection_marker(self) -> Path:
        return self._runtime_root / "previous_runtime.json"

    def deactivation_marker(self) -> Path:
        return self._runtime_root / "deactivated.json"

    def read_deactivation(self) -> dict:
        path = self.deactivation_marker()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_deactivation(self, payload: dict) -> None:
        path = self.deactivation_marker()
        tmp = path.with_suffix(".tmp")
        body = dict(payload)
        body.setdefault("updated_at", time.time())
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def clear_deactivation(self) -> None:
        try:
            self.deactivation_marker().unlink(missing_ok=True)
        except Exception:
            pass

    def read_runtime_selection(self, *, previous: bool = False) -> dict:
        path = self.previous_selection_marker() if previous else self.current_selection_marker()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def record_active_selection(
        self,
        version: str,
        slot: str,
        *,
        previous_version: str | None = None,
        previous_slot: str | None = None,
    ) -> None:
        if slot not in _SLOT_NAMES:
            raise ValueError(f"invalid slot '{slot}'")
        now = time.time()
        if previous_version and previous_slot in _SLOT_NAMES and (
            previous_version != version or previous_slot != slot
        ):
            previous_payload = {
                "version": previous_version,
                "slot": previous_slot,
                "runtime_bucket": self.runtime_bucket(previous_version),
                "updated_at": now,
            }
            self._write_metadata_path(self.previous_selection_marker(), previous_payload)
        current_payload = {
            "version": version,
            "slot": slot,
            "runtime_bucket": self.runtime_bucket(version),
            "updated_at": now,
        }
        self._write_metadata_path(self.current_selection_marker(), current_payload)

    def clear_runtime_selection(self) -> None:
        for path in (self.current_selection_marker(), self.previous_selection_marker()):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------
    def list_versions(self) -> list[str]:
        if not self._runtime_root.exists():
            return []
        versions: set[str] = set()
        marker = self.active_version_marker()
        if marker.exists():
            marker_version = marker.read_text(encoding="utf-8").strip()
            if marker_version:
                versions.add(marker_version)
        for child in self._runtime_root.iterdir():
            if not child.is_dir() or not self.is_runtime_bucket_name(child.name):
                continue
            metadata = self._read_metadata_path(child / "meta.json")
            if metadata:
                self._collect_metadata_versions(metadata, versions)
        return sorted(versions, key=_version_sort_key)

    def resolve_active_version(self) -> Optional[str]:
        marker = self.active_version_marker()
        if marker.exists():
            return marker.read_text(encoding="utf-8").strip() or None
        versions = self.list_versions()
        return versions[-1] if versions else None

    # ------------------------------------------------------------------
    # Creation helpers
    # ------------------------------------------------------------------
    def ensure_base(self) -> None:
        """Ensure that base runtime directories exist."""

        self._runtime_root.mkdir(parents=True, exist_ok=True)

    def ensure_data_dirs(self, version: str | None = None) -> None:
        for path in (
            self.data_root(version),
            self.db_dir(version),
            self.files_dir(version),
            self.internal_root(version),
        ):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_metadata_path(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_metadata_path(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _collect_metadata_versions(metadata: dict, versions: set[str]) -> None:
        for key in ("version", "active_version"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                versions.add(value.strip())
        history = metadata.get("history")
        if isinstance(history, dict):
            for key in ("last_install_version", "last_active_version", "previous_active_version"):
                value = history.get(key)
                if isinstance(value, str) and value.strip():
                    versions.add(value.strip())
        slots = metadata.get("slots")
        if isinstance(slots, dict):
            for slot_meta in slots.values():
                if isinstance(slot_meta, dict):
                    value = slot_meta.get("version")
                    if isinstance(value, str) and value.strip():
                        versions.add(value.strip())

    def prepare_version(self, version: str, *, activate_slot: Optional[str] = None) -> None:
        """Make sure that version layout exists.

        Args:
            version: Semantic version string.
            activate_slot: Optional slot to mark as active on first creation.
        """

        self.ensure_base()
        version_root = self.version_root(version)
        slots_root = self.slots_root(version)
        slots_root.mkdir(parents=True, exist_ok=True)
        self.ensure_data_dirs(version)
        for slot in _SLOT_NAMES:
            slot_root = self.slot_root(version, slot)
            self._ensure_slot(slot_root)

        marker = self.active_marker(version)
        if not marker.exists():
            selected = activate_slot or _SLOT_NAMES[0]
            marker.write_text(selected, encoding="utf-8")
            self._update_current_link(version, selected)
        current_marker = self.active_version_marker()
        if not current_marker.exists():
            current_marker.write_text(version, encoding="utf-8")
        else:
            # keep the active slot link in sync when prepare_version is reused
            selected = self.read_active_slot(version)
            self._update_current_link(version, selected)

    def _ensure_slot(self, slot_root: Path) -> None:
        slot_root.mkdir(parents=True, exist_ok=True)
        src_dir = slot_root / "src"
        vendor_dir = slot_root / "vendor"
        runtime_dir = slot_root / "runtime"
        logs_dir = runtime_dir / "logs"
        tmp_dir = runtime_dir / "tmp"

        for path in (src_dir, vendor_dir, runtime_dir, logs_dir, tmp_dir):
            path.mkdir(parents=True, exist_ok=True)

        keep = runtime_dir / ".keep"
        if not keep.exists():
            keep.write_text("managed by adaos", encoding="utf-8")

    def _update_current_link(self, version: str, slot: str) -> None:
        slots_root = self.slots_root(version)
        target = slots_root / slot
        current_link = slots_root / "current"
        if current_link.exists() or current_link.is_symlink():
            removed = False
            try:
                if current_link.is_symlink() or current_link.is_file():
                    current_link.unlink(missing_ok=True)
                    removed = True
                else:
                    current_link.rmdir()
                    removed = True
            except OSError:
                pass
            if not removed:
                self._remove_tree(current_link)
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            try:
                os.symlink(target, current_link, target_is_directory=True)  # type: ignore[arg-type]
            except OSError:
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(current_link), str(target)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        else:
            os.symlink(target, current_link, target_is_directory=True)

    def ensure_current_link(self, version: str) -> Path:
        slot = self.read_active_slot(version)
        self._update_current_link(version, slot)
        return self.slots_root(version) / "current"

    # ------------------------------------------------------------------
    # Slot helpers
    # ------------------------------------------------------------------
    def build_slot_paths(self, version: str, slot: str) -> SkillSlotPaths:
        slot_root = self.slot_root(version, slot)
        return SkillSlotPaths(
            skill_name=self._skill_name,
            version=version,
            slot=slot,
            root=slot_root,
            src_dir=slot_root / "src",
            vendor_dir=slot_root / "vendor",
            runtime_dir=slot_root / "runtime",
            tests_dir=slot_root / "src" / "skills" / self._skill_name / "tests",
            logs_dir=slot_root / "runtime" / "logs",
            tmp_dir=slot_root / "runtime" / "tmp",
            resolved_manifest=slot_root / "resolved.manifest.json",
            data_root=self.data_root(version),
            files_dir=self.files_dir(version),
            internal_data_dir=self.internal_root(version),
        )

    def read_active_slot(self, version: str) -> str:
        marker = self.active_marker(version)
        if marker.exists():
            value = marker.read_text(encoding="utf-8").strip().upper()
            if value in _SLOT_NAMES:
                return value
        return _SLOT_NAMES[0]

    def select_inactive_slot(self, version: str) -> str:
        active = self.read_active_slot(version)
        return "B" if active == "A" else "A"

    # ------------------------------------------------------------------
    # Activation helpers
    # ------------------------------------------------------------------
    def set_active_slot(self, version: str, slot: str) -> None:
        if slot not in _SLOT_NAMES:
            raise ValueError(f"invalid slot '{slot}'")
        marker = self.active_marker(version)
        previous = None
        if marker.exists():
            previous = marker.read_text(encoding="utf-8").strip()
        tmp_path = marker.with_suffix(".tmp")
        tmp_path.write_text(slot, encoding="utf-8")
        os.replace(tmp_path, marker)
        prev_marker = self.previous_marker(version)
        if previous and previous != slot:
            prev_marker.write_text(previous, encoding="utf-8")
        self._update_current_link(version, slot)

    def rollback_slot(self, version: str) -> str:
        current = self.read_active_slot(version)
        prev_marker = self.previous_marker(version)
        if not prev_marker.exists():
            raise RuntimeError("no previous slot recorded for rollback")
        previous = prev_marker.read_text(encoding="utf-8").strip()
        if previous not in _SLOT_NAMES:
            raise RuntimeError("previous slot marker is corrupted")
        if previous == current:
            raise RuntimeError("previous slot matches current; nothing to rollback")
        self.set_active_slot(version, previous)
        return previous

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------
    def cleanup_slot(self, version: str, slot: str) -> None:
        slot_root = self.slot_root(version, slot)
        if slot_root.exists():
            for child in sorted(slot_root.iterdir(), reverse=True):
                if child.is_file() or child.is_symlink():
                    try:
                        child.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    self._remove_tree(child)
            try:
                slot_root.rmdir()
            except OSError:
                # Keep directory if other processes keep files
                pass

    def _remove_tree(self, path: Path) -> None:
        for child in path.iterdir():
            if child.is_dir() and not child.is_symlink():
                self._remove_tree(child)
            else:
                try:
                    child.unlink()
                except FileNotFoundError:
                    pass
        try:
            path.rmdir()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------
    def write_version_metadata(self, version: str, payload: dict) -> None:
        path = self.metadata_path(version)
        self._write_metadata_path(path, payload)

    def read_version_metadata(self, version: str) -> dict:
        return self._read_metadata_path(self.metadata_path(version))

    def iter_slot_paths(self, version: str) -> Iterable[SkillSlotPaths]:
        for slot in _SLOT_NAMES:
            yield self.build_slot_paths(version, slot)

