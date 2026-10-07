from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Tuple

from adaos.ports.skills_loader import SkillsLoaderPort
from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.activation_assessment import handler_may_subscribe
from adaos.services.skill.declarations import load_runtime_skill_declarations
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
from adaos.services.skill.validation import runtime_async_blocking_issues
from adaos.services.logging import configure_skill_module_logging
import yaml

_LOG = logging.getLogger("adaos.services.skills_loader")
_HANDLER_IMPORT_LOCK = threading.RLock()
_LOADED_HANDLER_SOURCES: dict[str, dict[str, Any]] = {}
_LOADED_HANDLER_PATH_INDEX: dict[str, str] = {}
_LOADED_HANDLER_MISS_CACHE: dict[str, tuple[tuple[int, int, int, int], int]] = {}
_RETIRED_HANDLER_SOURCES: list[dict[str, Any]] = []
_RETIRED_HANDLER_SOURCES_LIMIT = 128
_RETIRED_HANDLER_TOTAL = 0
_REACTIVATION_LOCKS_GUARD = threading.Lock()
_REACTIVATION_LOCKS: dict[str, threading.Lock] = {}


def _reactivation_lock(skill_name: str) -> threading.Lock:
    with _REACTIVATION_LOCKS_GUARD:
        return _REACTIVATION_LOCKS.setdefault(str(skill_name), threading.Lock())


def _handler_module_name(handler: Path) -> str:
    """Return a stable importable package name for one handler directory."""

    resolved = os.path.normcase(str(handler.parent.resolve()))
    slug = "".join(character if character.isalnum() else "_" for character in resolved)
    slug = slug.strip("_")[-96:] or "handler"
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    return f"adaos_skill_{slug}_{digest}"


def _source_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _active_runtime_source_digest(
    environment: SkillRuntimeEnvironment,
    version: str,
    slot: str,
) -> str:
    if not version or not slot:
        return ""
    metadata = environment.read_version_metadata(version)
    slots = metadata.get("slots") if isinstance(metadata.get("slots"), Mapping) else {}
    selected = slots.get(slot) if isinstance(slots, Mapping) else None
    return str(dict(selected or {}).get("source_manifest_digest") or "").strip()


def _capture_handler_generation(skill_name: str, handlers: Iterable[Path]) -> dict[str, Any]:
    target = str(skill_name or "").strip()
    candidate_modules = {_handler_module_name(Path(handler)) for handler in handlers}
    source_records = {
        name: copy.deepcopy(record)
        for name, record in _LOADED_HANDLER_SOURCES.items()
        if str(record.get("skill") or "").strip() == target or name in candidate_modules
    }
    family_roots = set(source_records) | candidate_modules
    modules = {
        name: module
        for name, module in tuple(sys.modules.items())
        if any(name == root or name.startswith(f"{root}.") for root in family_roots)
    }
    from adaos.services.skill.declarations import runtime_skill_declarations_snapshot

    return {
        "captured_at": time.time(),
        "skill": target,
        "candidate_modules": candidate_modules,
        "source_records": source_records,
        "modules": modules,
        "declaration": runtime_skill_declarations_snapshot(target) or None,
    }


def _restore_handler_generation(snapshot: Mapping[str, Any]) -> None:
    """Restore every process-local surface owned by one handler generation."""

    global _RETIRED_HANDLER_TOTAL
    target = str(snapshot.get("skill") or "").strip()
    source_records = {
        str(name): copy.deepcopy(record)
        for name, record in dict(snapshot.get("source_records") or {}).items()
    }
    candidate_modules = {str(name) for name in snapshot.get("candidate_modules") or ()}
    current_names = {
        name
        for name, record in tuple(_LOADED_HANDLER_SOURCES.items())
        if str(record.get("skill") or "").strip() == target or name in candidate_modules
    }
    family_roots = current_names | set(source_records) | candidate_modules
    for name in tuple(sys.modules):
        if any(name == root or name.startswith(f"{root}.") for root in family_roots):
            sys.modules.pop(name, None)
    sys.modules.update(dict(snapshot.get("modules") or {}))

    for name in current_names | candidate_modules:
        record = _LOADED_HANDLER_SOURCES.pop(name, None)
        if isinstance(record, Mapping):
            path_key = os.path.normcase(str(record.get("path") or ""))
            if _LOADED_HANDLER_PATH_INDEX.get(path_key) == name:
                _LOADED_HANDLER_PATH_INDEX.pop(path_key, None)
    for name, record in source_records.items():
        _LOADED_HANDLER_SOURCES[name] = copy.deepcopy(record)
        path_key = os.path.normcase(str(record.get("path") or ""))
        if path_key:
            _LOADED_HANDLER_PATH_INDEX[path_key] = name
    _LOADED_HANDLER_MISS_CACHE.clear()

    captured_at = float(snapshot.get("captured_at") or 0.0)
    retained: list[dict[str, Any]] = []
    removed = 0
    for item in _RETIRED_HANDLER_SOURCES:
        if (
            str(item.get("skill") or "").strip() == target
            and float(item.get("retired_at") or 0.0) >= captured_at
        ):
            removed += 1
            continue
        retained.append(item)
    if removed:
        _RETIRED_HANDLER_SOURCES[:] = retained
        _RETIRED_HANDLER_TOTAL = max(0, _RETIRED_HANDLER_TOTAL - removed)

    from adaos.services.skill.declarations import restore_runtime_skill_declaration

    restore_runtime_skill_declaration(target, snapshot.get("declaration"))


def _source_digest_reverify_interval_s() -> float:
    try:
        value = float(os.getenv("ADAOS_SKILL_HANDLER_DIGEST_REVERIFY_S", "30") or "30")
    except (TypeError, ValueError):
        value = 30.0
    return max(1.0, min(3600.0, value))


def _runtime_selection_from_handler(path: Path) -> dict[str, str]:
    parts = path.resolve().parts
    try:
        runtime_idx = parts.index(".runtime")
    except ValueError:
        return {}
    if len(parts) <= runtime_idx + 5 or parts[runtime_idx + 3] != "slots":
        return {}
    skills_root = Path(*parts[:runtime_idx])
    skill_name = str(parts[runtime_idx + 1])
    loaded_bucket = str(parts[runtime_idx + 2])
    loaded_slot = str(parts[runtime_idx + 4]).upper()
    try:
        env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=skill_name)
        loaded_versions = [
            version
            for version in env.list_versions()
            if env.runtime_bucket(version) == loaded_bucket
        ]
        loaded_version = loaded_versions[0] if len(loaded_versions) == 1 else ""
        loaded_source_manifest_digest = _active_runtime_source_digest(
            env,
            loaded_version,
            loaded_slot,
        )
        selected_version = str(env.resolve_active_version() or "").strip()
        selected_bucket = env.runtime_bucket(selected_version) if selected_version else ""
        selected_slot = env.read_active_slot(selected_version) if selected_version else ""
        selected_source_manifest_digest = _active_runtime_source_digest(
            env,
            selected_version,
            str(selected_slot or "").upper(),
        )
    except Exception:
        loaded_version = ""
        loaded_source_manifest_digest = ""
        selected_version = ""
        selected_bucket = ""
        selected_slot = ""
        selected_source_manifest_digest = ""
    return {
        "skill": skill_name,
        "loaded_bucket": loaded_bucket,
        "loaded_slot": loaded_slot,
        "loaded_version": loaded_version,
        "loaded_source_manifest_digest": loaded_source_manifest_digest,
        "selected_version": selected_version,
        "selected_bucket": selected_bucket,
        "selected_slot": str(selected_slot or "").upper(),
        "selected_source_manifest_digest": selected_source_manifest_digest,
    }


def _record_loaded_handler_source(module_name: str, path: Path) -> dict[str, Any]:
    global _RETIRED_HANDLER_TOTAL
    previous_same_module = _LOADED_HANDLER_SOURCES.get(str(module_name))
    resolved = path.resolve()
    path_key = os.path.normcase(str(resolved))
    stat = resolved.stat()
    loaded_at = time.time()
    loaded_digest = _source_digest(resolved)
    record: dict[str, Any] = {
        "module": str(module_name),
        "path": str(resolved),
        "loaded_at": loaded_at,
        "loaded_size": int(stat.st_size),
        "loaded_mtime_ns": int(stat.st_mtime_ns),
        "loaded_ctime_ns": int(stat.st_ctime_ns),
        "loaded_inode": int(stat.st_ino),
        "loaded_digest": loaded_digest,
        "_observed_size": int(stat.st_size),
        "_observed_mtime_ns": int(stat.st_mtime_ns),
        "_observed_ctime_ns": int(stat.st_ctime_ns),
        "_observed_inode": int(stat.st_ino),
        "_verified_digest": loaded_digest,
        "_digest_verified_at": loaded_at,
    }
    record.update(_runtime_selection_from_handler(resolved))
    superseded_modules: list[str] = []
    skill_name = str(record.get("skill") or "").strip()
    loaded_bucket = str(record.get("loaded_bucket") or "").strip()
    loaded_slot = str(record.get("loaded_slot") or "").strip()
    selected_bucket = str(record.get("selected_bucket") or "").strip()
    selected_slot = str(record.get("selected_slot") or "").strip()
    source_is_selected = bool(
        skill_name
        and loaded_slot
        and selected_slot
        and loaded_bucket == selected_bucket
        and loaded_slot == selected_slot
    )
    if source_is_selected:
        superseded_modules = [
            name
            for name, item in _LOADED_HANDLER_SOURCES.items()
            if name != str(module_name) and str(item.get("skill") or "").strip() == skill_name
        ]
        superseded_records: list[tuple[str, dict[str, Any]]] = [
            (name, dict(_LOADED_HANDLER_SOURCES[name]))
            for name in superseded_modules
        ]
        if (
            isinstance(previous_same_module, dict)
            and str(previous_same_module.get("skill") or "").strip() == skill_name
        ):
            superseded_records.append((str(module_name), dict(previous_same_module)))
        if superseded_records:
            from adaos.sdk.core.decorators import retire_module_declarations

            declarations = retire_module_declarations(superseded_modules)
            retired_at = time.time()
            for name in superseded_modules:
                retired = _LOADED_HANDLER_SOURCES.pop(name, None)
                if isinstance(retired, dict):
                    retired_path = os.path.normcase(str(retired.get("path") or ""))
                    if _LOADED_HANDLER_PATH_INDEX.get(retired_path) == name:
                        _LOADED_HANDLER_PATH_INDEX.pop(retired_path, None)
                sys.modules.pop(name, None)
            for name, previous in superseded_records:
                _RETIRED_HANDLER_SOURCES.append(
                    {
                        "module": name,
                        "skill": skill_name,
                        "path": previous.get("path"),
                        "loaded_at": previous.get("loaded_at"),
                        "loaded_slot": previous.get("loaded_slot"),
                        "loaded_digest": previous.get("loaded_digest"),
                        "retired_at": retired_at,
                        "retired_by_module": str(module_name),
                        "retired_by_slot": loaded_slot,
                    }
                )
            _RETIRED_HANDLER_TOTAL += len(superseded_records)
            del _RETIRED_HANDLER_SOURCES[:-_RETIRED_HANDLER_SOURCES_LIMIT]
            _LOG.info(
                "retired superseded skill handlers skill=%s modules=%s declarations=%s",
                skill_name,
                ",".join(sorted(name for name, _previous in superseded_records)),
                json.dumps(declarations, sort_keys=True, separators=(",", ":")),
            )
    if isinstance(previous_same_module, dict):
        previous_path = os.path.normcase(str(previous_same_module.get("path") or ""))
        if _LOADED_HANDLER_PATH_INDEX.get(previous_path) == str(module_name):
            _LOADED_HANDLER_PATH_INDEX.pop(previous_path, None)
    _LOADED_HANDLER_SOURCES[str(module_name)] = record
    _LOADED_HANDLER_PATH_INDEX[path_key] = str(module_name)
    _LOADED_HANDLER_MISS_CACHE.clear()
    return record


def _retire_loaded_skill_sources(skill_name: str, *, retired_by: str) -> dict[str, Any]:
    """Retire imported declarations when the selected runtime has no handler."""

    global _RETIRED_HANDLER_TOTAL
    target = str(skill_name or "").strip()
    if not target:
        return {"skill": "", "modules": [], "declarations": {}}
    with _HANDLER_IMPORT_LOCK:
        records = [
            (name, dict(item))
            for name, item in _LOADED_HANDLER_SOURCES.items()
            if str(item.get("skill") or "").strip() == target
        ]
        module_names = [name for name, _item in records]
        if not module_names:
            return {"skill": target, "modules": [], "declarations": {}}

        from adaos.sdk.core.decorators import retire_module_declarations

        declarations = retire_module_declarations(module_names)
        retired_at = time.time()
        for name, previous in records:
            _LOADED_HANDLER_SOURCES.pop(name, None)
            retired_path = os.path.normcase(str(previous.get("path") or ""))
            if _LOADED_HANDLER_PATH_INDEX.get(retired_path) == name:
                _LOADED_HANDLER_PATH_INDEX.pop(retired_path, None)
            sys.modules.pop(name, None)
            _RETIRED_HANDLER_SOURCES.append(
                {
                    "module": name,
                    "skill": target,
                    "path": previous.get("path"),
                    "loaded_at": previous.get("loaded_at"),
                    "loaded_slot": previous.get("loaded_slot"),
                    "loaded_digest": previous.get("loaded_digest"),
                    "retired_at": retired_at,
                    "retired_by": str(retired_by or "runtime_reload"),
                }
            )
        _RETIRED_HANDLER_TOTAL += len(records)
        del _RETIRED_HANDLER_SOURCES[:-_RETIRED_HANDLER_SOURCES_LIMIT]
        _LOADED_HANDLER_MISS_CACHE.clear()
    _LOG.info(
        "retired skill handlers skill=%s modules=%s declarations=%s reason=%s",
        target,
        ",".join(sorted(module_names)),
        json.dumps(declarations, sort_keys=True, separators=(",", ":")),
        retired_by,
    )
    return {"skill": target, "modules": sorted(module_names), "declarations": declarations}


def loaded_handler_module_for_path(path: Path) -> Any | None:
    """Return the fully imported handler module for an unchanged source file.

    Tool and lifecycle execution must act on the same module instance that owns
    subscription workers and caches.  The source registry is updated only after
    ``exec_module`` succeeds, so a matching record is also the import-complete
    marker.  A changed source intentionally falls back to the isolated runtime
    loader until the subscriptions loader performs an explicit reload.
    """

    try:
        resolved = Path(path).resolve()
        stat = resolved.stat()
    except OSError:
        return None
    path_key = os.path.normcase(str(resolved))
    fingerprint = (
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
        int(stat.st_ino),
    )
    with _HANDLER_IMPORT_LOCK:
        miss = _LOADED_HANDLER_MISS_CACHE.get(path_key)
        if miss == (fingerprint, len(_LOADED_HANDLER_SOURCES)):
            return None
        indexed_name = _LOADED_HANDLER_PATH_INDEX.get(path_key)
        indexed_record = _LOADED_HANDLER_SOURCES.get(indexed_name or "")
        records = [dict(indexed_record)] if isinstance(indexed_record, dict) else []
        if not records:
            if indexed_name:
                _LOADED_HANDLER_PATH_INDEX.pop(path_key, None)
            records = sorted(
                (dict(item) for item in _LOADED_HANDLER_SOURCES.values()),
                key=lambda item: float(item.get("loaded_at") or 0.0),
                reverse=True,
            )
        for record in records:
            try:
                record_path = Path(str(record.get("path") or "")).resolve()
            except OSError:
                continue
            if os.path.normcase(str(record_path)) != path_key:
                continue
            module_name = str(record.get("module") or "")
            if module_name:
                _LOADED_HANDLER_PATH_INDEX[path_key] = module_name
            fingerprint_matches = (
                int(record.get("loaded_size", -1)) == int(stat.st_size)
                and int(record.get("loaded_mtime_ns", -1)) == int(stat.st_mtime_ns)
                and int(record.get("loaded_ctime_ns", -1)) == int(stat.st_ctime_ns)
                and int(record.get("loaded_inode", -1)) == int(stat.st_ino)
            )
            if not fingerprint_matches:
                return None
            module = sys.modules.get(module_name)
            if module is None:
                return None
            try:
                module_path = Path(str(getattr(module, "__file__", "") or "")).resolve()
            except OSError:
                return None
            return module if os.path.normcase(str(module_path)) == path_key else None
        _LOADED_HANDLER_MISS_CACHE[path_key] = (
            fingerprint,
            len(_LOADED_HANDLER_SOURCES),
        )
    return None


def skill_handler_source_snapshot() -> dict[str, Any]:
    """Compare imported handler bytes and runtime selection with current disk state."""

    captured_at = time.time()
    reverify_interval_s = _source_digest_reverify_interval_s()
    with _HANDLER_IMPORT_LOCK:
        records = [dict(item) for item in _LOADED_HANDLER_SOURCES.values()]
        retired_sources = [dict(item) for item in _RETIRED_HANDLER_SOURCES[-20:]]
    items: list[dict[str, Any]] = []
    for record in records:
        path = Path(str(record.get("path") or ""))
        current_exists = path.is_file()
        current_size: int | None = None
        current_mtime_ns: int | None = None
        current_ctime_ns: int | None = None
        current_inode: int | None = None
        source_drift = not current_exists
        current_digest = ""
        raw_verified_at = record.get("_digest_verified_at")
        if raw_verified_at is None:
            raw_verified_at = record.get("loaded_at")
        digest_verified_at = float(raw_verified_at or 0.0)
        if current_exists:
            try:
                stat = path.stat()
                current_size = int(stat.st_size)
                current_mtime_ns = int(stat.st_mtime_ns)
                current_ctime_ns = int(stat.st_ctime_ns)
                current_inode = int(stat.st_ino)
                fingerprint_changed = (
                    current_size != int(record.get("_observed_size") or -1)
                    or current_mtime_ns != int(record.get("_observed_mtime_ns") or -1)
                    or current_ctime_ns != int(record.get("_observed_ctime_ns") or -1)
                    or current_inode != int(record.get("_observed_inode") or -1)
                )
                verification_due = captured_at - digest_verified_at >= reverify_interval_s
                if fingerprint_changed or verification_due:
                    current_digest = _source_digest(path)
                    digest_verified_at = captured_at
                    with _HANDLER_IMPORT_LOCK:
                        live_record = _LOADED_HANDLER_SOURCES.get(str(record.get("module") or ""))
                        if live_record is not None and live_record.get("loaded_at") == record.get("loaded_at"):
                            live_record.update(
                                {
                                    "_observed_size": current_size,
                                    "_observed_mtime_ns": current_mtime_ns,
                                    "_observed_ctime_ns": current_ctime_ns,
                                    "_observed_inode": current_inode,
                                    "_verified_digest": current_digest,
                                    "_digest_verified_at": digest_verified_at,
                                }
                            )
                else:
                    current_digest = str(record.get("_verified_digest") or record.get("loaded_digest") or "")
                source_drift = current_digest != str(record.get("loaded_digest") or "")
            except OSError:
                current_exists = False
                source_drift = True
        selection = _runtime_selection_from_handler(path) if current_exists else {}
        loaded_bucket = str(record.get("loaded_bucket") or "")
        loaded_slot = str(record.get("loaded_slot") or "")
        selected_bucket = str(selection.get("selected_bucket") or record.get("selected_bucket") or "")
        selected_slot = str(selection.get("selected_slot") or record.get("selected_slot") or "")
        selection_drift = bool(
            loaded_slot
            and selected_slot
            and (loaded_bucket != selected_bucket or loaded_slot != selected_slot)
        )
        public_record = {key: value for key, value in record.items() if not key.startswith("_")}
        item = {
            **public_record,
            **selection,
            "current_exists": current_exists,
            "current_size": current_size,
            "current_mtime_ns": current_mtime_ns,
            "current_ctime_ns": current_ctime_ns,
            "current_inode": current_inode,
            "current_digest": current_digest or None,
            "digest_verified_at": digest_verified_at or None,
            "source_drift": source_drift,
            "selection_drift": selection_drift,
            "drift": bool(source_drift or selection_drift),
        }
        items.append(item)
    items.sort(key=lambda item: (not bool(item.get("drift")), str(item.get("skill") or item.get("module") or "")))
    drift_items = [item for item in items if item.get("drift")]
    return {
        "schema": "adaos.skill_handler_sources.v1",
        "available": True,
        "ok": not drift_items,
        "captured_at": captured_at,
        "digest_reverify_interval_s": reverify_interval_s,
        "loaded_total": len(items),
        "drift_total": len(drift_items),
        "source_drift_total": sum(1 for item in items if item.get("source_drift")),
        "selection_drift_total": sum(1 for item in items if item.get("selection_drift")),
        "retired_total": _RETIRED_HANDLER_TOTAL,
        "recent_retirements": retired_sources,
        "items": items,
    }


class ImportlibSkillsLoader(SkillsLoaderPort):
    def __init__(self) -> None:
        # One loader pass used to parse every selected manifest up to three
        # times: service discovery, declaration loading, and activation
        # classification.  Cold Windows filesystems make those small reads
        # disproportionately expensive.  Keep a process-local cache only for
        # the lifetime of this loader instance; reload entry points explicitly
        # clear it so edited development manifests are never hidden.
        self._manifest_cache: dict[Path, dict[str, Any]] = {}

    async def import_all_handlers(self, skills_root: Any) -> None:
        root = Path(skills_root() if callable(skills_root) else skills_root)
        started_at = time.perf_counter()
        source_sync_enabled = self._runtime_source_sync_enabled()
        if source_sync_enabled:
            await asyncio.to_thread(self._sync_runtime_from_repo_workspace_if_missing, root)
            await asyncio.to_thread(self._sync_runtime_from_workspace, root)
        self._manifest_cache.clear()
        selected: set[str] = set()
        imported: set[str] = set()
        deferred: set[str] = set()
        loaded_declaration_manifests: set[Path] = set()
        runtime_safety_cache: dict[Path, list[dict[str, Any]]] = {}
        import_timings: list[dict[str, Any]] = []
        discovery_timings: dict[str, float] = {}
        deactivated_runtime_skills = await asyncio.to_thread(self._discover_deactivated_runtime_skills, root)
        if deactivated_runtime_skills:
            _LOG.warning(
                "quarantined skill handlers excluded from runtime bootstrap skills=%s",
                ",".join(sorted(deactivated_runtime_skills)),
            )

        discovery_started_at = time.perf_counter()
        runtime_handlers = await asyncio.to_thread(self._discover_runtime_handlers, root)
        runtime_handlers = [
            (handler, skill_name)
            for handler, skill_name in runtime_handlers
            if not skill_name or skill_name not in deactivated_runtime_skills
        ]
        discovery_timings["runtime"] = round((time.perf_counter() - discovery_started_at) * 1000.0, 3)
        import_timings.extend(
            await self._load_discovered_handlers(
                runtime_handlers,
                selected=selected,
                imported=imported,
                deferred=deferred,
                loaded_declaration_manifests=loaded_declaration_manifests,
                runtime_safety_cache=runtime_safety_cache,
                skills_root=root,
                source="runtime",
            )
        )

        # Dev/fast-path: load handlers straight from the workspace tree when a
        # skill does not have an installed runtime bundle under .runtime.
        discovery_started_at = time.perf_counter()
        excluded_skills = selected | deactivated_runtime_skills
        workspace_handlers = await asyncio.to_thread(self._discover_workspace_handlers, root, excluded_skills)
        discovery_timings["workspace"] = round((time.perf_counter() - discovery_started_at) * 1000.0, 3)
        import_timings.extend(
            await self._load_discovered_handlers(
                workspace_handlers,
                selected=selected,
                imported=imported,
                deferred=deferred,
                loaded_declaration_manifests=loaded_declaration_manifests,
                runtime_safety_cache=runtime_safety_cache,
                skills_root=root,
                source="workspace",
            )
        )

        # Repo-bundled workspace skills are a final fallback for builtin skills
        # when the node-local workspace tree does not contain the sources.
        discovery_started_at = time.perf_counter()
        excluded_skills = selected | deactivated_runtime_skills
        repo_handlers = await asyncio.to_thread(self._discover_repo_workspace_handlers, root, excluded_skills)
        discovery_timings["repo_workspace"] = round((time.perf_counter() - discovery_started_at) * 1000.0, 3)
        import_timings.extend(
            await self._load_discovered_handlers(
                repo_handlers,
                selected=selected,
                imported=imported,
                deferred=deferred,
                loaded_declaration_manifests=loaded_declaration_manifests,
                runtime_safety_cache=runtime_safety_cache,
                skills_root=root,
                source="repo_workspace",
            )
        )
        slowest_imports = sorted(import_timings, key=lambda item: float(item.get("elapsed_ms") or 0.0), reverse=True)[:5]
        _LOG.info(
            "skill handler import completed elapsed_s=%.3f selected_skills=%d imported_skills=%d deferred_skills=%d "
            "quarantined_skills=%d source_sync=%s candidate=%s "
            "discovery_ms=%s slowest_imports=%s",
            time.perf_counter() - started_at,
            len(selected),
            len(imported),
            len(deferred),
            len(deactivated_runtime_skills),
            source_sync_enabled,
            self._runtime_candidate_mode(),
            json.dumps(discovery_timings, sort_keys=True, separators=(",", ":")),
            json.dumps(slowest_imports, sort_keys=True, separators=(",", ":")),
        )

    async def reload_skill_handlers(
        self,
        skills_root: Any,
        skill_name: str,
        *,
        expected_version: str | None = None,
        expected_slot: str | None = None,
        expected_source_manifest_digest: str | None = None,
        drain_timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        """Reload one exact runtime generation under a per-skill process lock."""

        target = str(skill_name or "").strip()
        if not target:
            return {"ok": False, "reason": "skill_name_missing", "handlers": []}
        lock = _reactivation_lock(target)
        await asyncio.to_thread(lock.acquire)
        try:
            return await self._reload_skill_handlers_locked(
                skills_root,
                target,
                expected_version=expected_version,
                expected_slot=expected_slot,
                expected_source_manifest_digest=expected_source_manifest_digest,
                drain_timeout_s=drain_timeout_s,
            )
        finally:
            lock.release()

    async def _reload_skill_handlers_locked(
        self,
        skills_root: Any,
        skill_name: str,
        *,
        expected_version: str | None = None,
        expected_slot: str | None = None,
        expected_source_manifest_digest: str | None = None,
        drain_timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        root = Path(skills_root() if callable(skills_root) else skills_root)
        self._manifest_cache.clear()
        target = str(skill_name or "").strip()
        from adaos.sdk.core.decorators import (
            _registry_snapshot,
            _restore_registry_snapshot,
            deactivate_skill_subscriptions,
            register_subscriptions,
        )

        subscriptions = {"skills": [], "removed_handlers": 0, "preflight": True}
        environment = SkillRuntimeEnvironment(skills_root=root, skill_name=target)
        deactivation = await asyncio.to_thread(environment.read_deactivation)
        if bool(deactivation.get("deactivated")):
            return {
                "ok": False,
                "skipped": True,
                "reason": "skill_runtime_deactivated",
                "skill": target,
                "deactivation": deactivation,
                "subscriptions": subscriptions,
                "handlers": [],
            }
        active_version = str(await asyncio.to_thread(environment.resolve_active_version) or "").strip()
        active_slot = (
            str(await asyncio.to_thread(environment.read_active_slot, active_version) or "").strip().upper()
            if active_version
            else ""
        )
        selection = {"version": active_version, "slot": active_slot}
        active_source_digest = await asyncio.to_thread(
            _active_runtime_source_digest,
            environment,
            active_version,
            active_slot,
        )
        required_version = str(expected_version or "").strip()
        required_slot = str(expected_slot or "").strip().upper()
        required_source_digest = str(expected_source_manifest_digest or "").strip()
        if (required_version and active_version != required_version) or (
            required_slot and active_slot != required_slot
        ) or (
            required_source_digest and active_source_digest != required_source_digest
        ):
            return {
                "ok": False,
                "reason": (
                    "runtime_source_digest_mismatch"
                    if required_source_digest and active_source_digest != required_source_digest
                    else "runtime_selection_mismatch"
                ),
                "skill": target,
                "expected_selection": {"version": required_version, "slot": required_slot},
                "selection": selection,
                "expected_source_manifest_digest": required_source_digest or None,
                "source_manifest_digest": active_source_digest or None,
                "subscriptions": subscriptions,
                "handlers": [],
            }
        runtime_handlers = await asyncio.to_thread(self._discover_runtime_handlers, root)
        handlers = [
            handler
            for handler, name in runtime_handlers
            if name == target and self._handler_runs_in_process(handler)
        ]
        if not handlers:
            loaded: set[str] = set()
            workspace_handlers = await asyncio.to_thread(self._discover_workspace_handlers, root, loaded)
            handlers = [handler for handler, name in workspace_handlers if name == target]
        if not handlers:
            repo_handlers = await asyncio.to_thread(self._discover_repo_workspace_handlers, root, set())
            handlers = [handler for handler, name in repo_handlers if name == target]
        if not handlers:
            return {
                "ok": False,
                "skipped": True,
                "reason": "reactivation_preflight_no_in_process_handlers",
                "skill": target,
                "subscriptions": subscriptions,
                "preserved_previous_generation": True,
                "handlers": [],
            }

        # Validate every candidate before invalidating the working generation.
        # A quarantined candidate must not tear down the handlers that currently
        # provide service while the lifecycle owner decides how to recover.
        for handler in handlers:
            issues = await asyncio.to_thread(self._runtime_safety_issues, handler)
            if issues:
                quarantine = await asyncio.to_thread(
                    self._quarantine_runtime_safety_violation,
                    root,
                    target,
                    handler,
                    issues,
                    source="reload_preflight",
                )
                await self._emit_runtime_safety_quarantine(target, quarantine)
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "runtime_safety_validation_failed",
                    "skill": target,
                    "deactivation": quarantine,
                    "subscriptions": subscriptions,
                    "issues": issues,
                    "preserved_previous_generation": True,
                    "handlers": [],
                }

        from adaos.services.skill.subscription_execution import (
            begin_skill_drain,
            end_skill_drain,
            wait_for_skill_drain,
        )

        drain = begin_skill_drain(target, reason="exact_runtime_reactivation")
        drain_receipt: dict[str, Any]
        try:
            drain_receipt = await wait_for_skill_drain(
                target,
                str(drain["drain_id"]),
                timeout_s=max(0.0, min(float(drain_timeout_s), 3600.0)),
            )
            if not drain_receipt.get("drained"):
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": "reactivation_in_flight_drain_timeout",
                    "skill": target,
                    "drain": drain_receipt,
                    "subscriptions": subscriptions,
                    "preserved_previous_generation": True,
                    "handlers": [],
                }
            registry_before = _registry_snapshot()
            generation_before = _capture_handler_generation(target, handlers)
            subscriptions = deactivate_skill_subscriptions({target})
        finally:
            end_skill_drain(target, str(drain["drain_id"]))
        subscriptions["drain"] = drain_receipt

        # Fence the selection again after invalidating bus handlers and before
        # importing any candidate code. Concurrent activation must not make a
        # preflight receipt authorize a different version or slot.
        fenced_version = str(await asyncio.to_thread(environment.resolve_active_version) or "").strip()
        fenced_slot = (
            str(await asyncio.to_thread(environment.read_active_slot, fenced_version) or "")
            .strip()
            .upper()
            if fenced_version
            else ""
        )
        fenced_source_digest = await asyncio.to_thread(
            _active_runtime_source_digest,
            environment,
            fenced_version,
            fenced_slot,
        )
        if (
            fenced_version != active_version
            or fenced_slot != active_slot
            or fenced_source_digest != active_source_digest
        ):
            _restore_registry_snapshot(registry_before)
            _restore_handler_generation(generation_before)
            await register_subscriptions(skill_names={target}, force=True)
            return {
                "ok": False,
                "reason": "runtime_selection_changed_during_reactivation",
                "skill": target,
                "preflight_selection": selection,
                "selection": {"version": fenced_version, "slot": fenced_slot},
                "preflight_source_manifest_digest": active_source_digest or None,
                "source_manifest_digest": fenced_source_digest or None,
                "subscriptions": subscriptions,
                "preserved_previous_generation": True,
                "handlers": [],
            }
        loaded_declaration_manifests: set[Path] = set()
        loaded_handlers: list[str] = []
        try:
            for handler in handlers:
                import_started_at = time.perf_counter()
                await asyncio.to_thread(self._load_handler, handler, reload=True)
                import_ms = (time.perf_counter() - import_started_at) * 1000.0
                declaration_started_at = time.perf_counter()
                await asyncio.to_thread(
                    self._load_skill_declarations,
                    handler,
                    loaded_declaration_manifests,
                    skill_name=target,
                )
                declaration_ms = (time.perf_counter() - declaration_started_at) * 1000.0
                self._log_slow_handler_import(
                    self._handler_import_timing(
                        handler=handler,
                        skill_name=target,
                        source="reload",
                        elapsed_ms=declaration_ms + import_ms,
                        declaration_ms=declaration_ms,
                        import_ms=import_ms,
                        loaded=True,
                    )
                )
                loaded_handlers.append(str(handler))
                _LOG.info("reloaded skill handler skill=%s path=%s", target, handler)
        except Exception as exc:
            _restore_registry_snapshot(registry_before)
            _restore_handler_generation(generation_before)
            await register_subscriptions(skill_names={target}, force=True)
            return {
                "ok": False,
                "reason": "handler_import_failed",
                "skill": target,
                "error": f"{type(exc).__name__}: {exc}",
                "subscriptions": subscriptions,
                "preserved_previous_generation": True,
                "handlers": [],
            }
        post_version = str(await asyncio.to_thread(environment.resolve_active_version) or "").strip()
        post_slot = (
            str(await asyncio.to_thread(environment.read_active_slot, post_version) or "")
            .strip()
            .upper()
            if post_version
            else ""
        )
        post_source_digest = await asyncio.to_thread(
            _active_runtime_source_digest,
            environment,
            post_version,
            post_slot,
        )
        loaded_modules = {_handler_module_name(Path(handler)) for handler in handlers}
        source_snapshot = await asyncio.to_thread(skill_handler_source_snapshot)
        observed = {
            str(item.get("module") or ""): dict(item)
            for item in source_snapshot.get("items") or ()
            if isinstance(item, Mapping) and str(item.get("module") or "") in loaded_modules
        }
        postcheck = {
            "selection_matches": post_version == active_version and post_slot == active_slot,
            "source_digest_matches": post_source_digest == active_source_digest,
            "handler_count": len(observed),
            "expected_handler_count": len(loaded_modules),
            "handlers_current": len(observed) == len(loaded_modules)
            and all(not bool(item.get("drift")) for item in observed.values()),
        }
        postcheck["ok"] = all(
            bool(postcheck[key])
            for key in ("selection_matches", "source_digest_matches", "handlers_current")
        )
        if not postcheck["ok"]:
            _restore_registry_snapshot(registry_before)
            _restore_handler_generation(generation_before)
            await register_subscriptions(skill_names={target}, force=True)
            return {
                "ok": False,
                "reason": "reactivation_postcheck_failed",
                "skill": target,
                "selection": {"version": post_version, "slot": post_slot},
                "source_manifest_digest": post_source_digest or None,
                "subscriptions": subscriptions,
                "preserved_previous_generation": True,
                "postcheck": postcheck,
                "handlers": [],
            }
        if loaded_handlers:
            await register_subscriptions(skill_names={target}, force=True)
        return {
            "ok": bool(loaded_handlers),
            "skill": target,
            "selection": selection,
            "source_manifest_digest": active_source_digest or None,
            "subscriptions": subscriptions,
            "postcheck": postcheck,
            "handlers": loaded_handlers,
        }

    def _load_handler(self, handler: Path, *, reload: bool = False) -> None:
        with _HANDLER_IMPORT_LOCK:
            mod_name = _handler_module_name(handler)
            existing = sys.modules.get(mod_name)
            if existing is not None and not reload:
                _LOG.debug("reusing already imported skill handler module=%s path=%s", mod_name, handler)
                return
            registry_snapshot: dict[str, Any] | None = None
            existing_family = {
                name: module
                for name, module in tuple(sys.modules.items())
                if name == mod_name or name.startswith(f"{mod_name}.")
            }
            if existing is not None and reload:
                from adaos.sdk.core.decorators import _registry_snapshot, retire_module_declarations

                registry_snapshot = _registry_snapshot()
                retire_module_declarations(set(existing_family))
                for family_name in existing_family:
                    sys.modules.pop(family_name, None)
            spec = importlib.util.spec_from_file_location(
                mod_name,
                handler,
                submodule_search_locations=[str(handler.parent)],
            )
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            configure_skill_module_logging(mod_name)
            sys.modules[mod_name] = module
            try:
                spec.loader.exec_module(module)  # type: ignore[attr-defined]
                source = _record_loaded_handler_source(mod_name, handler)
            except Exception:
                for family_name in tuple(sys.modules):
                    if family_name == mod_name or family_name.startswith(f"{mod_name}."):
                        sys.modules.pop(family_name, None)
                if registry_snapshot is not None:
                    from adaos.sdk.core.decorators import _restore_registry_snapshot

                    _restore_registry_snapshot(registry_snapshot)
                sys.modules.update(existing_family)
                raise
            _LOG.info(
                "imported skill handler module=%s path=%s source_digest=%s loaded_slot=%s selected_slot=%s",
                mod_name,
                handler,
                source.get("loaded_digest"),
                source.get("loaded_slot") or "-",
                source.get("selected_slot") or "-",
            )

    def _try_load_handler(self, handler: Path, *, skill_name: str | None, source: str) -> bool:
        try:
            self._load_handler(handler)
            return True
        except Exception as exc:
            _LOG.warning(
                "skill handler import failed; skipping skill=%s source=%s path=%s error=%s: %s",
                skill_name or "",
                source,
                handler,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return False

    async def _try_load_handler_async(self, handler: Path, *, skill_name: str | None, source: str) -> bool:
        timing_started_at = time.perf_counter()
        loaded = await asyncio.to_thread(
            self._try_load_handler,
            handler,
            skill_name=skill_name,
            source=source,
        )
        timing = self._handler_import_timing(
            handler=handler,
            skill_name=skill_name,
            source=source,
            elapsed_ms=(time.perf_counter() - timing_started_at) * 1000.0,
            loaded=loaded,
        )
        self._log_slow_handler_import(timing)
        return loaded

    async def _load_discovered_handlers(
        self,
        handlers: Iterable[Tuple[Path, Optional[str]]],
        *,
        selected: set[str],
        imported: set[str],
        deferred: set[str],
        loaded_declaration_manifests: set[Path],
        runtime_safety_cache: dict[Path, list[dict[str, Any]]],
        skills_root: Path,
        source: str,
    ) -> list[dict[str, Any]]:
        timings: list[dict[str, Any]] = []
        source_label = {
            "runtime": "skill handler",
            "workspace": "workspace skill handler",
            "repo_workspace": "repo workspace skill handler",
        }.get(source, f"{source} skill handler")
        for handler, skill_name in handlers:
            handler_started_at = time.perf_counter()
            manifest_path = self._find_skill_manifest(handler)
            if manifest_path is not None and self._is_service_manifest(
                manifest_path
            ) and not self._service_allows_in_process_events(manifest_path):
                if skill_name:
                    # Claim the selected runtime source and retain its compact
                    # activation declaration even though the service handler
                    # itself must not execute in the API process.
                    selected.add(skill_name)
                declaration_started_at = time.perf_counter()
                await asyncio.to_thread(
                    self._load_skill_declarations,
                    handler,
                    loaded_declaration_manifests,
                    skill_name=skill_name,
                )
                declaration_ms = (
                    time.perf_counter() - declaration_started_at
                ) * 1000.0
                timings.append(
                    self._handler_import_timing(
                        handler=handler,
                        skill_name=skill_name,
                        source=source,
                        elapsed_ms=(time.perf_counter() - handler_started_at) * 1000.0,
                        declaration_ms=declaration_ms,
                        loaded=False,
                    )
                )
                continue
            if skill_name:
                # Claim the selected runtime source before validation/import so
                # a broken or intentionally deferred installed handler cannot
                # fall through to an older workspace copy.
                selected.add(skill_name)
            declaration_started_at = time.perf_counter()
            await asyncio.to_thread(
                self._load_skill_declarations,
                handler,
                loaded_declaration_manifests,
                skill_name=skill_name,
            )
            declaration_ms = (time.perf_counter() - declaration_started_at) * 1000.0
            boot_import = await asyncio.to_thread(self._requires_boot_import, handler)
            if boot_import is False:
                if skill_name:
                    deferred.add(skill_name)
                timing = self._handler_import_timing(
                    handler=handler,
                    skill_name=skill_name,
                    source=source,
                    elapsed_ms=(time.perf_counter() - handler_started_at) * 1000.0,
                    declaration_ms=declaration_ms,
                    import_ms=0.0,
                    loaded=False,
                )
                timing["deferred"] = True
                timing["defer_reason"] = "tool_invocation_imports_on_demand"
                timings.append(timing)
                _LOG.info(
                    "deferred tool-only skill handler until invocation skill=%s source=%s path=%s",
                    skill_name or "",
                    source,
                    handler,
                )
                await asyncio.sleep(self._handler_import_yield_sec())
                continue
            safety_root = self._skill_source_root(handler)
            issues = runtime_safety_cache.get(safety_root)
            if issues is None:
                issues = await asyncio.to_thread(self._runtime_safety_issues, handler)
                runtime_safety_cache[safety_root] = issues
            if issues:
                if skill_name:
                    await asyncio.to_thread(
                        self._quarantine_runtime_safety_violation,
                        skills_root,
                        skill_name,
                        handler,
                        issues,
                        source=source,
                    )
                timings.append(
                    self._handler_import_timing(
                        handler=handler,
                        skill_name=skill_name,
                        source=source,
                        elapsed_ms=(time.perf_counter() - handler_started_at) * 1000.0,
                        loaded=False,
                    )
                )
                await asyncio.sleep(self._handler_import_yield_sec())
                continue
            import_started_at = time.perf_counter()
            loaded_ok = await self._try_load_handler_async(
                handler,
                skill_name=skill_name,
                source=source,
            )
            import_ms = (time.perf_counter() - import_started_at) * 1000.0
            timings.append(
                self._handler_import_timing(
                    handler=handler,
                    skill_name=skill_name,
                    source=source,
                    elapsed_ms=(time.perf_counter() - handler_started_at) * 1000.0,
                    declaration_ms=declaration_ms,
                    import_ms=import_ms,
                    loaded=loaded_ok,
                )
            )
            if loaded_ok:
                if skill_name:
                    imported.add(skill_name)
                    _LOG.info("imported %s skill=%s path=%s", source_label, skill_name, handler)
                else:
                    _LOG.info("imported %s path=%s", source_label, handler)
            await asyncio.sleep(self._handler_import_yield_sec())
        return timings

    def _requires_boot_import(self, handler: Path) -> bool | None:
        """Return whether a handler must be imported before event delivery.

        Explicit eager/startup policy and manifest event declarations remain
        authoritative. The source check covers legacy manifests whose event
        inventory is incomplete. Unknown inputs preserve the old boot import.
        """

        manifest_path = self._find_skill_manifest(handler)
        if manifest_path is None:
            return None
        manifest = self._read_manifest(manifest_path)
        if not isinstance(manifest, dict):
            return None
        runtime = manifest.get("runtime")
        activation = runtime.get("activation") if isinstance(runtime, dict) else None
        if isinstance(activation, dict) and (
            str(activation.get("mode") or "").strip() == "eager"
            or activation.get("startup_allowed") is True
        ):
            return True
        events = manifest.get("events")
        subscriptions = events.get("subscribe") if isinstance(events, dict) else None
        if isinstance(subscriptions, list) and any(
            isinstance(item, str) and item.strip() for item in subscriptions
        ):
            return True
        may_subscribe = handler_may_subscribe(handler)
        if may_subscribe is None:
            return None
        return bool(may_subscribe)

    @staticmethod
    def _skill_source_root(handler: Path) -> Path:
        path = Path(handler)
        if path.parent.name == "handlers":
            return path.parent.parent
        manifest = ImportlibSkillsLoader._find_skill_manifest(path)
        return manifest.parent if manifest is not None else path.parent

    @classmethod
    def _runtime_safety_issues(cls, handler: Path) -> list[dict[str, Any]]:
        root = cls._skill_source_root(handler)
        return [
            {
                "level": issue.level,
                "code": issue.code,
                "message": issue.message,
                "where": issue.where,
            }
            for issue in runtime_async_blocking_issues(root)
        ]

    @staticmethod
    def _quarantine_runtime_safety_violation(
        skills_root: Path,
        skill_name: str,
        handler: Path,
        issues: list[dict[str, Any]],
        *,
        source: str,
    ) -> dict[str, Any]:
        payload = {
            "deactivated": True,
            "reason": "runtime_safety_validation_failed",
            "failure_kind": "async_blocking_call",
            "failed_stage": "handler_import_preflight",
            "source": str(source or "runtime"),
            "handler": str(handler),
            "issues": list(issues),
            "updated_at": time.time(),
        }
        environment = SkillRuntimeEnvironment(
            skills_root=Path(skills_root),
            skill_name=str(skill_name),
        )
        environment.ensure_base()
        environment.write_deactivation(payload)
        _LOG.error(
            "skill runtime quarantined before handler import skill=%s source=%s path=%s issues=%s",
            skill_name,
            source,
            handler,
            json.dumps(issues, sort_keys=True, separators=(",", ":")),
        )
        return payload

    @staticmethod
    async def _emit_runtime_safety_quarantine(skill_name: str, payload: dict[str, Any]) -> None:
        try:
            await get_ctx().bus.emit(
                "skills.deactivated",
                {
                    "name": str(skill_name),
                    "skill_name": str(skill_name),
                    "reason": "runtime_safety_validation_failed",
                    "deactivation": dict(payload),
                },
                source="skills.loader.safety",
                actor="system",
            )
        except Exception:
            _LOG.debug(
                "failed to emit skill runtime safety quarantine skill=%s",
                skill_name,
                exc_info=True,
            )

    @staticmethod
    def _handler_import_timing(
        *,
        handler: Path,
        skill_name: str | None,
        source: str,
        elapsed_ms: float,
        declaration_ms: float | None = None,
        import_ms: float | None = None,
        loaded: bool,
    ) -> dict[str, Any]:
        timing = {
            "skill": str(skill_name or "<unknown>"),
            "source": str(source or "unknown"),
            "elapsed_ms": round(max(0.0, float(elapsed_ms)), 3),
            "loaded": bool(loaded),
            "path": str(handler),
        }
        if declaration_ms is not None:
            timing["declaration_ms"] = round(max(0.0, float(declaration_ms)), 3)
        if import_ms is not None:
            timing["import_ms"] = round(max(0.0, float(import_ms)), 3)
        return timing

    @staticmethod
    def _handler_import_yield_sec() -> float:
        try:
            return min(
                0.25,
                max(0.001, float(os.getenv("ADAOS_SKILL_HANDLER_IMPORT_YIELD_SEC", "0.01") or 0.01)),
            )
        except Exception:
            return 0.01

    @staticmethod
    def _log_slow_handler_import(timing: dict[str, Any]) -> None:
        try:
            threshold_ms = max(10.0, float(os.getenv("ADAOS_SKILL_HANDLER_IMPORT_WARN_MS", "250") or 250.0))
        except Exception:
            threshold_ms = 250.0
        elapsed_ms = float(timing.get("elapsed_ms") or 0.0)
        if elapsed_ms < threshold_ms:
            return
        _LOG.warning(
            "slow skill handler import skill=%s source=%s elapsed_ms=%.3f loaded=%s path=%s threshold_ms=%.1f",
            timing.get("skill"),
            timing.get("source"),
            elapsed_ms,
            timing.get("loaded"),
            timing.get("path"),
            threshold_ms,
        )

    def _load_skill_declarations(
        self,
        handler: Path,
        loaded: set[Path],
        *,
        skill_name: str | None,
    ) -> None:
        manifest_path = self._find_skill_manifest(handler)
        if manifest_path is None:
            return
        try:
            resolved = manifest_path.resolve()
        except OSError:
            resolved = manifest_path
        if resolved in loaded:
            return
        loaded.add(resolved)
        payload = self._read_manifest(manifest_path)
        if not isinstance(payload, dict):
            return
        declaration_name = str(skill_name or payload.get("name") or "").strip()
        entries = payload.get("data_projections") or []
        try:
            projections = get_ctx().projections
            replace_skill_manifest = getattr(projections, "replace_skill_manifest", None)
            if declaration_name and callable(replace_skill_manifest):
                replace_skill_manifest(declaration_name, payload)
            elif isinstance(entries, list) and entries:
                load_manifest = getattr(projections, "load_manifest", None)
                if callable(load_manifest):
                    load_manifest(payload)
                else:
                    projections.load_entries(entries)
            if isinstance(entries, list) and entries:
                _LOG.info("loaded skill data_projections path=%s entries=%d", manifest_path, len(entries))
        except Exception:
            _LOG.debug("failed to load skill data_projections path=%s", manifest_path, exc_info=True)
        if declaration_name:
            try:
                load_runtime_skill_declarations(
                    declaration_name,
                    payload,
                    artifact_root=manifest_path.parent,
                )
            except Exception:
                _LOG.debug("failed to cache skill runtime declarations path=%s", manifest_path, exc_info=True)

    @staticmethod
    def _find_skill_manifest(handler: Path) -> Optional[Path]:
        for parent in handler.parents:
            for name in ("skill.yaml", "resolved.manifest.json"):
                candidate = parent / name
                if candidate.exists():
                    return candidate
        return None

    def _discover_runtime_handlers(self, root: Path) -> Iterable[Tuple[Path, Optional[str]]]:
        runtime_root = root / ".runtime"
        if not runtime_root.exists():
            return []

        handlers: list[Tuple[Path, Optional[str]]] = []
        for skill_dir in runtime_root.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            version = self._read_text(skill_dir / "current_version")
            if not version:
                continue
            env = SkillRuntimeEnvironment(skills_root=root, skill_name=skill_name)
            version_dir = env.version_root(version)
            slot_dir = self._resolve_slot(version_dir)
            if not slot_dir:
                continue
            # Handlers live under slots/<slot>/src; avoid scanning vendor/runtime trees.
            src_root = slot_dir / "src"
            if not src_root.exists():
                continue
            direct_handler = src_root / "skills" / skill_name / "handlers" / "main.py"
            if direct_handler.exists():
                handlers.append((direct_handler, skill_name))
                continue
            fallback_handlers = {
                *src_root.glob("handlers/main.py"),
                *src_root.glob("*/handlers/main.py"),
                *src_root.glob("*/*/handlers/main.py"),
            }
            for handler in sorted(fallback_handlers):
                handlers.append((handler, skill_name))
        return handlers

    @staticmethod
    def _discover_deactivated_runtime_skills(root: Path) -> set[str]:
        runtime_root = root / ".runtime"
        if not runtime_root.exists():
            return set()
        deactivated: set[str] = set()
        for skill_dir in runtime_root.iterdir():
            if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
                continue
            payload = SkillRuntimeEnvironment(
                skills_root=root,
                skill_name=skill_dir.name,
            ).read_deactivation()
            if bool(payload.get("deactivated")):
                deactivated.add(skill_dir.name)
        return deactivated

    def _discover_workspace_handlers(self, root: Path, loaded: set[str]) -> Iterable[Tuple[Path, Optional[str]]]:
        if not root.exists():
            return []
        handlers: list[Tuple[Path, Optional[str]]] = []
        for skill_dir in root.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith((".", "_")):
                continue
            # Runtime selection is authoritative.  Check it before touching a
            # workspace manifest; on an installed node this avoids dozens of
            # redundant cold reads while preserving the existing precedence.
            if skill_dir.name in loaded:
                continue
            # Skip service skills by default; see runtime.in_process_events.
            manifest_path = skill_dir / "skill.yaml"
            if self._is_service_manifest(manifest_path) and not self._service_allows_in_process_events(manifest_path):
                continue
            handler = skill_dir / "handlers" / "main.py"
            if handler.exists():
                handlers.append((handler, skill_dir.name))
        return handlers

    def _discover_repo_workspace_handlers(self, root: Path, loaded: set[str]) -> Iterable[Tuple[Path, Optional[str]]]:
        repo_root = self._repo_workspace_skills_root()
        if repo_root is None or not repo_root.exists():
            return []

        try:
            ctx = get_ctx()
            ws_root = ctx.paths.skills_workspace_dir()
            ws_root = Path(ws_root() if callable(ws_root) else ws_root)
        except Exception:
            ws_root = root

        handlers: list[Tuple[Path, Optional[str]]] = []
        for skill_dir in repo_root.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith((".", "_")):
                continue
            if skill_dir.name in loaded:
                continue
            # A real node-local workspace copy takes precedence over repo fallback.
            if (ws_root / skill_dir.name).exists():
                continue
            manifest_path = skill_dir / "skill.yaml"
            if self._is_service_manifest(manifest_path) and not self._service_allows_in_process_events(manifest_path):
                continue
            handler = skill_dir / "handlers" / "main.py"
            if handler.exists():
                handlers.append((handler, skill_dir.name))
        return handlers

    def _read_manifest(self, path: Path) -> dict[str, Any]:
        try:
            cache_key = path.resolve()
        except OSError:
            cache_key = path
        cached = self._manifest_cache.get(cache_key)
        if cached is not None:
            return cached
        if not path.exists():
            return {}
        try:
            content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        manifest = content if isinstance(content, dict) else {}
        self._manifest_cache[cache_key] = manifest
        return manifest

    def _is_service_manifest(self, path: Path) -> bool:
        content = self._read_manifest(path)
        runtime = content.get("runtime") or {}
        if isinstance(runtime, dict) and runtime.get("kind") == "service":
            return True
        return False

    def _service_allows_in_process_events(self, path: Path) -> bool:
        content = self._read_manifest(path)
        runtime = content.get("runtime") or {}
        return bool(isinstance(runtime, dict) and runtime.get("in_process_events") is True)

    def _handler_runs_in_process(self, handler: Path) -> bool:
        manifest_path = self._find_skill_manifest(handler)
        return bool(
            manifest_path is None
            or not self._is_service_manifest(manifest_path)
            or self._service_allows_in_process_events(manifest_path)
        )

    @staticmethod
    def _resolve_slot(version_dir: Path) -> Optional[Path]:
        current = version_dir / "slots" / "current"
        if current.exists():
            try:
                resolved = current.resolve()
                if resolved.exists():
                    return resolved
            except OSError:
                pass
        active_file = version_dir / "active"
        active = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else ""
        if not active:
            return None
        slot_dir = version_dir / "slots" / active
        return slot_dir if slot_dir.exists() else None

    @staticmethod
    def _read_text(path: Path) -> str | None:
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Explicit development workspace/runtime sync helpers
    # ------------------------------------------------------------------
    def _sync_runtime_from_workspace(self, runtime_root: Path) -> None:
        try:
            ctx = get_ctx()
            ws_root = ctx.paths.skills_workspace_dir()
            ws_root = Path(ws_root() if callable(ws_root) else ws_root)
        except Exception:
            return
        if not ws_root.exists():
            return
        mgr = self._build_skill_manager(ctx)

        for entry in ws_root.iterdir():
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            try:
                name = entry.name
                result = mgr.runtime_update(name, space="workspace")
            except Exception as exc:
                _LOG.debug("runtime_update failed for %s: %s", name, exc)
                continue
            if not result.get("ok"):
                continue
            files = result.get("files") or []
            tools = result.get("tools_added") or []
            if files or tools:
                _LOG.info(
                    "runtime_update applied for workspace skill '%s' (files=%d, tools_added=%d)",
                    name,
                    len(files),
                    len(tools),
                )

    @staticmethod
    def _runtime_candidate_mode() -> bool:
        return str(os.getenv("ADAOS_RUNTIME_TRANSITION_ROLE") or "active").strip().lower() == "candidate"

    @classmethod
    def _runtime_source_sync_enabled(cls) -> bool:
        if cls._runtime_candidate_mode():
            return False
        raw = str(os.getenv("ADAOS_SKILL_RUNTIME_SOURCE_SYNC") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _sync_runtime_from_repo_workspace_if_missing(self, runtime_root: Path) -> None:
        repo_ws_root = self._repo_workspace_skills_root()
        if repo_ws_root is None or not repo_ws_root.exists():
            return

        try:
            ctx = get_ctx()
            ws_root = ctx.paths.skills_workspace_dir()
            ws_root = Path(ws_root() if callable(ws_root) else ws_root)
        except Exception:
            return

        runtime_state_root = runtime_root / ".runtime"
        if not runtime_state_root.exists():
            return

        mgr = self._build_skill_manager(ctx)
        for runtime_skill_root in runtime_state_root.iterdir():
            if not runtime_skill_root.is_dir():
                continue
            name = runtime_skill_root.name
            if name.startswith((".", "_")):
                continue
            if (ws_root / name).exists():
                continue
            if not (repo_ws_root / name).exists():
                continue
            try:
                result = mgr.runtime_update(name, space="workspace")
            except Exception as exc:
                _LOG.debug("repo workspace runtime_update failed for %s: %s", name, exc)
                continue
            if not result.get("ok"):
                continue
            files = result.get("files") or []
            tools = result.get("tools_added") or []
            if files or tools:
                _LOG.info(
                    "runtime_update applied from repo workspace for skill '%s' (files=%d, tools_added=%d)",
                    name,
                    len(files),
                    len(tools),
                )

    @staticmethod
    def _repo_workspace_skills_root() -> Optional[Path]:
        try:
            ctx = get_ctx()
            repo_root_attr = getattr(ctx.paths, "repo_root", None)
            repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
            if not repo_root:
                return None
            candidate = Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "skills"
            if candidate.exists():
                return candidate
        except Exception:
            return None
        return None

    @staticmethod
    def _build_skill_manager(ctx: Any) -> SkillManager:
        from adaos.adapters.db import SqliteSkillRegistry  # pylint: disable=import-outside-toplevel

        return SkillManager(
            repo=ctx.skills_repo,
            registry=SqliteSkillRegistry(ctx.sql),
            git=ctx.git,
            paths=ctx.paths,
            bus=getattr(ctx, "bus", None),
            caps=ctx.caps,
            settings=ctx.settings,
        )
