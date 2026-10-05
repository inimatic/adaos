"""Revisioned declarative capability index and short-lived live context handles."""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from copy import deepcopy
from typing import Any, Callable, Mapping

from .ledger import digest, new_id
from .policy import EXPERIMENTAL_NLU, require_enabled


def normalized(value: Any) -> str:
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")))


def texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for v in value.values() for text in texts(v)]
    if isinstance(value, (list, tuple)):
        return [text for v in value for text in texts(v)]
    return []


class CapabilityIndex:
    def __init__(self, loader: Callable[[], list[dict[str, Any]]], *, ttl: float = 300):
        self.loader, self.ttl = loader, ttl
        self.lock = threading.RLock()
        self.rows: dict[str, dict[str, Any]] = {}
        self.terms: dict[str, list[str]] = {}
        self.revision = ""
        self.generation = 0
        self.loaded_at = 0.0
        self.compile_ms = 0.0

    def invalidate(self) -> None:
        with self.lock:
            self.loaded_at = 0
            self.generation += 1

    def load(self) -> bool:
        with self.lock:
            if self.loaded_at and time.monotonic()-self.loaded_at < self.ttl:
                return True
            started = time.perf_counter()
            rows = self.loader()
            by_ref = {}
            for raw in rows:
                row = deepcopy(raw)
                ref = row["ref"]
                row.setdefault("schema", "adaos.companion.interaction_contract.v1")
                row.setdefault("input_schema", {"type": "object", "properties": {}, "additionalProperties": False})
                row.setdefault("output_schema", {"type": "object"})
                row.setdefault("confirmation", "none")
                row.setdefault("cancellation", False)
                row.setdefault("idempotent", row.get("effect") == "read_only")
                row.setdefault("cost", "runtime")
                row.setdefault("expected_observation", "executor_receipt")
                row.setdefault("scope", "subnet")
                row.setdefault("admitted", False)
                row["descriptor_digest"] = digest(row)
                if ref in by_ref and by_ref[ref] != row:
                    raise ValueError(f"duplicate_capability_ref:{ref}")
                by_ref[ref] = row
            revision = digest([by_ref[k] for k in sorted(by_ref)])
            if revision != self.revision:
                self.generation += 1
            self.rows, self.revision = by_ref, revision
            self.terms = {ref: [normalized(s) for s in texts([row.get("title"), row.get("aliases"), row.get("labels"), ref, row.get("description")]) if normalized(s)] for ref, row in by_ref.items()}
            self.loaded_at = time.monotonic()
            self.compile_ms = (time.perf_counter()-started)*1000
            return False

    def search(self, query: str = "", *, kind: str | None = None, offset: int = 0, limit: int = 12,
               catalog_digest: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        with self.lock:
            hit = self.load()
            if catalog_digest and catalog_digest != self.revision:
                raise ValueError("catalog_revision_changed")
            needle = normalized(query)
            matches = []
            for ref, row in self.rows.items():
                if kind and row["kind"] != kind:
                    continue
                terms = self.terms[ref]
                score = 3 if needle in terms else 2 if needle and any(needle in s for s in terms) else 1 if needle and all(any(t in s.split() for s in terms) for t in needle.split()) else 0
                if not needle or score:
                    matches.append((-score, ref, row))
            matches.sort(key=lambda v: (v[0], v[1]))
            offset, limit = max(0, offset), min(50, max(1, limit))
            page = [{k: deepcopy(v) for k, v in row.items() if k in {"ref", "kind", "title", "owner", "effect", "admitted", "reason", "descriptor_digest"}} for _, _, row in matches[offset:offset+limit]]
            return {"items": page, "count": len(page), "total": len(matches), "catalog_total": len(self.rows),
                    "next_offset": offset+limit if offset+limit < len(matches) else None,
                    "catalog_digest": self.revision, "generation": self.generation, "cache_hit": hit,
                    "duration_ms": (time.perf_counter()-started)*1000,
                    "outcome": "matches" if page else "search_miss",
                    "hint": None if page else "Search alternate terms or query an empty string with kind and paging; a miss does not prove absence."}

    def describe(self, ref: str) -> dict[str, Any]:
        with self.lock:
            self.load()
            if ref not in self.rows:
                raise KeyError("capability_not_found")
            return {**deepcopy(self.rows[ref]), "catalog_digest": self.revision}


class ContextHandles:
    def __init__(self, index: CapabilityIndex, reader: Callable[[], dict[str, Any]], *, ttl: float = 30):
        self.index, self.reader, self.ttl = index, reader, ttl
        self.lock = threading.RLock()
        self.handles: dict[str, dict[str, Any]] = {}

    def read(self) -> dict[str, Any]:
        started = time.perf_counter()
        cache_hit = self.index.load()
        state = self.reader()
        if state.get("read_error"):
            raise RuntimeError("context_source_unavailable:"+str(state["read_error"]))
        now = time.time()
        value = {"context_handle": new_id("context"), "context_digest": digest(state), "state": state,
                 "catalog_digest": self.index.revision, "generation": self.index.generation,
                 "observed_at": now, "expires_at": now+self.ttl, "cache_hit": cache_hit,
                 "duration_ms": (time.perf_counter()-started)*1000}
        with self.lock:
            self.handles = {k: v for k,v in self.handles.items() if v["expires_at"] > now}
            if len(self.handles) >= 256:
                self.handles.pop(next(iter(self.handles)))
            self.handles[value["context_handle"]] = value
        return deepcopy(value)

    def resolve(self, handle: str, *, fresh: bool = True) -> dict[str, Any]:
        with self.lock:
            value = deepcopy(self.handles.get(handle))
        self.index.load()
        if not value or value["expires_at"] <= time.time():
            raise ValueError("context_handle_expired")
        if value["catalog_digest"] != self.index.revision or value["generation"] != self.index.generation:
            raise ValueError("context_catalog_stale")
        if fresh:
            state = self.reader()
            if state.get("read_error") or digest(state) != value["context_digest"]:
                raise ValueError("context_stale")
        return value


def runtime_delta(webspace: str) -> dict[str, Any]:
    from adaos.services.nlu.teacher_read_model import _read_yjs_context_snapshot
    from .observation import live_ui

    snapshot = _read_yjs_context_snapshot(webspace)
    ui, data = snapshot.get("ui") or {}, snapshot.get("data") or {}
    return {"webspace_id": webspace, "current_scenario": ui.get("current_scenario"),
            "active_view": ui.get("active_view"), "active_modal": ui.get("active_modal") or ui.get("modal"),
            "selected_target": ui.get("selected_target"), "pending_interaction": ui.get("pending_interaction"),
            "pinned_apps": (data.get("installed") or {}).get("apps", []),
            "read_error": snapshot.get("read_error"), **live_ui(webspace)}


def runtime_catalog(webspace: str) -> list[dict[str, Any]]:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu import teacher_read_model as source
    from adaos.services.root_mcp.companion_plane import _OPERATIONS

    ctx = get_ctx()
    snapshot = source._read_yjs_context_snapshot(webspace)
    if snapshot.get("read_error"):
        raise RuntimeError(str(snapshot["read_error"]))
    rows = [{"ref": f"operation:{op['id']}", "kind": "ui" if op["id"].startswith("ui.") else "observation",
             "title": op["title"], "effect": op["effect"], "owner": "core:companion", "admitted": True,
             "operation": op["id"], "required_parameters": op["required"]} for op in _OPERATIONS]
    for collection, kind, operation, argument in (("apps", "ui", "ui.scenario.open", "scenario_id"), ("widgets", "ui", "ui.widget.focus", "widget_id")):
        catalog = (snapshot.get("data", {}).get("catalog") or {}).get(collection) or []
        if isinstance(catalog, Mapping):
            catalog = [{"id": k, **v} for k,v in catalog.items() if isinstance(v, Mapping)]
        for item in catalog:
            identity = item.get("scenario_id") or item.get("id")
            if not identity:
                continue
            target = str(identity).removeprefix("scenario:") if argument == "scenario_id" else str(identity)
            modal = item.get("launchModal")
            rows.append({"ref": f"ui:{collection}:{identity}", "kind": kind,
                         "title": item.get("title") or item.get("label") or str(identity),
                         "aliases": texts([item.get("name"), item.get("aliases"), identity]),
                         "owner": "catalog:"+str(identity), "effect": "ui_navigation", "admitted": True,
                         "operation": "ui.modal.open" if modal else operation,
                         "bound_params": {"modal_id": modal} if modal else {argument: target}})
    runtime = source._runtime_state_from_snapshot(snapshot, lookup_payload={})
    voice = source._voice_surface_rows(runtime_state=runtime, limit=1000000)
    for collection in ("voice_affordances", "voice_capabilities"):
        for item in voice.get(collection, []):
            owner = item.get("owner") or {}
            rows.append({"ref": f"ui:affordance:{owner.get('type')}:{owner.get('id')}:{item['id']}",
                         "kind": "ui", "title": item.get("title") or item["id"], "aliases": item.get("aliases"),
                         "labels": item.get("labels"), "effect": item.get("side_effect_class"),
                         "owner": owner, "admitted": bool(item.get("activation")),
                         "operation": "ui.affordance.activate", "bound_params": {"affordance_id": item["id"]},
                         "voice_affordance": {k:v for k,v in item.items() if k != "source_path"}})
    seen_skills = set()
    for root in source._skill_roots(ctx):
        if not root.exists():
            continue
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or directory.name.startswith(".") or directory.name in seen_skills or directory.name in EXPERIMENTAL_NLU:
                continue
            seen_skills.add(directory.name)
            manifest, _ = source._read_skill_manifest(directory.name, ctx=ctx)
            for tool in (manifest or {}).get("tools", []):
                if not isinstance(tool, Mapping) or not tool.get("name"):
                    continue
                effect = tool.get("side_effect_class") or tool.get("side_effects") or "unspecified"
                admitted = effect in {"none", "read_only"} and directory.name not in {"nlu_teacher_skill", "builder_skill", "infra_access_skill", "conversation_companions"}
                rows.append({"ref": f"skill:{directory.name}:{tool['name']}", "kind": "skill",
                             "title": tool.get("description") or tool["name"], "owner": "skill:"+directory.name,
                             "aliases": [directory.name, tool["name"]], "effect": effect, "admitted": admitted,
                             "reason": None if admitted else "not_admitted_to_companion_execution",
                             "skill": directory.name, "tool": tool["name"], "input_schema": tool.get("input_schema") or {"type": "object"}})
    for op, title in (("runtime.version.read", "Версия AdaOS runtime version"), ("profile.self.read", "Мой профиль имя пользователя my profile name")):
        rows.append({"ref": "operation:"+op, "kind": "observation", "title": title, "owner": "core:companion",
                     "effect": "read_only", "admitted": True, "operation": op})
    # Stable identical duplicates may originate in both local and packaged registries.
    return list({row["ref"]: row for row in rows}.values())


_INDEXES: dict[tuple[str, str], tuple[CapabilityIndex, ContextHandles]] = {}
_INDEX_LOCK = threading.RLock()


def runtime(webspace: str) -> tuple[CapabilityIndex, ContextHandles]:
    require_enabled()
    from adaos.services.agent_context import get_ctx

    ctx = get_ctx()
    key = (str(ctx.paths.root_mcp_state_dir()), webspace)
    from .observation import bind

    bind(ctx.bus)
    with _INDEX_LOCK:
        if key not in _INDEXES:
            index = CapabilityIndex(lambda: runtime_catalog(webspace))
            handles = ContextHandles(index, lambda: runtime_delta(webspace))
            _INDEXES[key] = (index, handles)
            def invalidate(_event):
                index.invalidate()
            for topic in ("skill.installed", "skill.updated", "skill.uninstalled", "scenario.materialized", "application.activated", "catalog.updated"):
                ctx.bus.subscribe(topic, invalidate)
        return _INDEXES[key]
