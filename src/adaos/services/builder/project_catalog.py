from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.services.agent_context import get_ctx
from adaos.services.builder.workbench import safe_source_webspace_id
from adaos.services.runtime_paths import current_state_dir


_MANIFEST_NAMES = {
    "project": ("project.yaml",),
    "scenario": ("scenario.yaml",),
    "skill": ("skill.yaml",),
}
_CATALOG_MANIFEST_FIELDS = {
    "catalog",
    "components",
    "depends",
    "description",
    "entrypoints",
    "id",
    "kind",
    "name",
    "profiles",
    "title",
    "version",
}
_CATALOG_MANIFEST_BOUNDARIES = {
    "conversation",
    "data_projections",
    "data_routes",
    "events",
    "routes",
    "runtime",
    "tools",
    "ui",
}
_MANIFEST_HEADER_CACHE_LIMIT = 1024
_MANIFEST_HEADER_CACHE: OrderedDict[str, tuple[tuple[int, int], dict[str, Any]]] = OrderedDict()
_MANIFEST_HEADER_CACHE_LOCK = threading.RLock()
def _kind(value: Any) -> str:
    token = str(value or "").strip().lower().rstrip("s")
    if token not in {"", "project", "scenario", "skill"}:
        raise ValueError("kind must be project, scenario, or skill")
    return token


def _read_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _catalog_header_complete(value: Mapping[str, Any]) -> bool:
    return bool(value.get("id") or value.get("name")) and bool(value.get("version")) and "description" in value


def _read_yaml_catalog_header(path: Path) -> dict[str, Any]:
    try:
        events = yaml.parse(path.read_text(encoding="utf-8-sig"))
        result: dict[str, Any] = {}
        depth = 0
        root_key: str | None = None
        collecting_depends = False
        for event in events:
            if isinstance(event, yaml.events.MappingStartEvent):
                if depth == 0:
                    depth = 1
                    continue
                if depth == 1 and root_key is not None:
                    if root_key in _CATALOG_MANIFEST_BOUNDARIES and _catalog_header_complete(result):
                        break
                    root_key = None
                depth += 1
                continue
            if isinstance(event, yaml.events.SequenceStartEvent):
                if depth == 1 and root_key is not None:
                    if root_key == "depends":
                        result["depends"] = []
                        collecting_depends = True
                    elif root_key in _CATALOG_MANIFEST_BOUNDARIES and _catalog_header_complete(result):
                        break
                    root_key = None
                depth += 1
                continue
            if isinstance(event, yaml.events.MappingEndEvent):
                depth -= 1
                continue
            if isinstance(event, yaml.events.SequenceEndEvent):
                if collecting_depends and depth == 2:
                    collecting_depends = False
                depth -= 1
                continue
            if not isinstance(event, yaml.events.ScalarEvent):
                continue
            if collecting_depends and depth == 2:
                result["depends"].append(event.value)
                continue
            if depth != 1:
                continue
            if root_key is None:
                root_key = event.value
                continue
            if root_key in _CATALOG_MANIFEST_FIELDS:
                result[root_key] = event.value
            elif root_key in _CATALOG_MANIFEST_BOUNDARIES and _catalog_header_complete(result):
                break
            root_key = None
        return result
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _read_catalog_manifest(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {}
    identity = (int(stat.st_mtime_ns), int(stat.st_size))
    cache_key = str(path)
    with _MANIFEST_HEADER_CACHE_LOCK:
        cached = _MANIFEST_HEADER_CACHE.get(cache_key)
        if cached is not None and cached[0] == identity:
            _MANIFEST_HEADER_CACHE.move_to_end(cache_key)
            return dict(cached[1])

    if path.name == "project.yaml":
        value = {
            key: item
            for key, item in _read_mapping(path).items()
            if key in _CATALOG_MANIFEST_FIELDS
        }
    elif path.suffix.lower() != ".json":
        value = _read_yaml_catalog_header(path)
    else:
        value = {key: item for key, item in _read_mapping(path).items() if key in _CATALOG_MANIFEST_FIELDS}
    with _MANIFEST_HEADER_CACHE_LOCK:
        _MANIFEST_HEADER_CACHE[cache_key] = (identity, dict(value))
        _MANIFEST_HEADER_CACHE.move_to_end(cache_key)
        while len(_MANIFEST_HEADER_CACHE) > _MANIFEST_HEADER_CACHE_LIMIT:
            _MANIFEST_HEADER_CACHE.popitem(last=False)
    return value


def _manifest(root: Path, kind: str) -> tuple[Path | None, dict[str, Any]]:
    for name in _MANIFEST_NAMES[kind]:
        path = root / name
        if path.is_file():
            return path, _read_catalog_manifest(path)
    return None, {}


def _prompt_summary(root: Path) -> dict[str, Any]:
    state = _read_mapping(root / "prompt_state.json")
    return {
        "archived": bool(state.get("archived")),
        "updated_at": str(state.get("updated_at") or "").strip() or None,
        "builder_llm_model": state.get("builder_llm_model"),
    }


@dataclass(slots=True)
class BuilderProjectCatalogService:
    skills_root: Path
    scenarios_root: Path
    state_dir: Path
    projects_root: Path | None = None

    @classmethod
    def from_context(cls) -> "BuilderProjectCatalogService":
        ctx = get_ctx()
        projects_root = None
        try:
            projects_root = Path(ctx.paths.dev_projects_dir()).resolve()
        except Exception:
            projects_root = Path(ctx.paths.dev_dir()).resolve() / "projects"
        return cls(
            skills_root=Path(ctx.paths.dev_skills_dir()).resolve(),
            scenarios_root=Path(ctx.paths.dev_scenarios_dir()).resolve(),
            state_dir=current_state_dir(),
            projects_root=projects_root,
        )

    def _preview_webspace_id(self, source_webspace_id: str | None) -> str:
        binding = _read_mapping(self._binding_path(source_webspace_id))
        return str(binding.get("preview_webspace_id") or binding.get("dev_webspace_id") or "").strip()

    def _binding_path(self, source_webspace_id: str | None) -> Path:
        source = safe_source_webspace_id(source_webspace_id)
        return self.state_dir / "builder" / "workbench" / "bindings" / f"{source}.json"

    def list_projects(
        self,
        *,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 200,
        selected_object_type: str | None = None,
        selected_object_id: str | None = None,
        webspace_id: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        requested_kind = _kind(kind)
        kinds = [requested_kind] if requested_kind else ["project", "scenario", "skill"]
        bounded_limit = max(1, min(int(limit), 5000))
        needle = str(query or "").strip().casefold()
        selected_kind = _kind(selected_object_type)
        selected_id = str(selected_object_id or "").strip()

        preview_id = self._preview_webspace_id(webspace_id)

        items: list[dict[str, Any]] = []
        for current_kind in kinds:
            if current_kind == "project":
                parent = self.projects_root
            elif current_kind == "scenario":
                parent = self.scenarios_root
            else:
                parent = self.skills_root
            if parent is None:
                continue
            if not parent.is_dir():
                continue
            roots = sorted(
                (entry for entry in parent.iterdir() if entry.is_dir() and not entry.name.startswith((".", "_"))),
                key=lambda entry: entry.name.casefold(),
            )
            for root in roots:
                manifest_path, manifest = _manifest(root, current_kind)
                project_id = str(manifest.get("id") or manifest.get("name") or root.name).strip()
                if not project_id or project_id.startswith((".", "_")):
                    continue
                catalog = manifest.get("catalog") if isinstance(manifest.get("catalog"), Mapping) else {}
                title = str(
                    catalog.get("title")
                    or manifest.get("title")
                    or manifest.get("name")
                    or project_id
                ).strip() or project_id
                description = str(
                    catalog.get("description") or manifest.get("description") or ""
                ).strip()
                if needle and needle not in f"{project_id} {title} {description}".casefold():
                    continue
                state = _prompt_summary(root)
                current = current_kind == selected_kind and project_id == selected_id
                archived = bool(state["archived"])
                if archived and not include_archived:
                    continue
                version = str(manifest.get("version") or "DEV")
                components = manifest.get("components") if isinstance(manifest.get("components"), Mapping) else {}
                owned = [
                    dict(item)
                    for item in components.get("owned") or []
                    if isinstance(item, Mapping)
                ]
                dependencies = [
                    dict(item)
                    for item in components.get("dependencies") or []
                    if isinstance(item, Mapping)
                ]
                primary = next(
                    (item for item in owned if str(item.get("role") or "") == "primary"),
                    owned[0] if owned else None,
                )
                dependency_refs = [
                    str(item.get("ref") or "").strip()
                    for item in dependencies
                    if str(item.get("ref") or "").strip()
                ]
                if current_kind != "project":
                    dependency_refs = list(manifest.get("depends") or [])
                items.append(
                    {
                        "kind": current_kind,
                        "id": f"{current_kind}:{project_id}",
                        "object_type": current_kind,
                        "object_id": project_id,
                        "name": str(manifest.get("name") or project_id),
                        "title": title,
                        "description": description,
                        "subtitle": description or f"{current_kind} - {version}",
                        "type": (
                            "Project"
                            if current_kind == "project"
                            else "Scenario"
                            if current_kind == "scenario"
                            else "Skill"
                        ),
                        "type_i18n": {"key": f"builder.project_type.{current_kind}"},
                        "stage": "Archive" if archived else "Prototype",
                        "stage_i18n": {
                            "key": "builder.project_stage.archive" if archived else "builder.project_stage.prototype"
                        },
                        "version": version,
                        "stable": version or "-",
                        "space": preview_id,
                        "sync": "Current" if current else "Available in DEV",
                        "sync_i18n": {
                            "key": "builder.project_sync.current" if current else "builder.project_sync.available_dev"
                        },
                        "updated": state["updated_at"] or "DEV",
                        "current": current,
                        "archived": archived,
                        "builder_llm_model": state["builder_llm_model"],
                        "depends": dependency_refs,
                        "manifest": manifest_path.name if manifest_path else None,
                        "profiles": list(manifest.get("profiles") or []) if current_kind == "project" else [],
                        "primary_ref": (
                            str(primary.get("ref") or "").strip()
                            if isinstance(primary, Mapping)
                            else None
                        ),
                        "component_refs": [
                            str(item.get("ref") or "").strip()
                            for item in owned
                            if str(item.get("ref") or "").strip()
                        ],
                        "entrypoints": [
                            dict(item)
                            for item in manifest.get("entrypoints") or []
                            if isinstance(item, Mapping)
                        ]
                        if current_kind == "project"
                        else [],
                    }
                )
                if len(items) >= bounded_limit:
                    break
            if len(items) >= bounded_limit:
                break

        return items


__all__ = ["BuilderProjectCatalogService"]
