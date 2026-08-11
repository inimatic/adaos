from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _token(value: Any) -> str:
    return str(value or "").strip()


def _safe_token(value: Any, *, fallback: str) -> str:
    token = _SAFE_TOKEN_RE.sub("_", _token(value)).strip("._-")
    return token or fallback


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _referenced_modal_ids(value: Any) -> set[str]:
    modal_ids: set[str] = set()
    for item in _walk(value):
        launch_modal = _token(item.get("launchModal"))
        if launch_modal and not launch_modal.startswith("$"):
            modal_ids.add(launch_modal)
        if _token(item.get("type")) == "openModal":
            params = item.get("params") if isinstance(item.get("params"), Mapping) else {}
            modal_id = _token(params.get("modalId") or params.get("modal_id"))
            if modal_id and not modal_id.startswith("$"):
                modal_ids.add(modal_id)
        shorthand = _token(item.get("openModal"))
        if shorthand and not shorthand.startswith("$"):
            modal_ids.add(shorthand)
    return modal_ids


def _modal_view(modal: Mapping[str, Any]) -> str:
    implements = modal.get("implements") if isinstance(modal.get("implements"), list) else []
    schema = modal.get("schema") if isinstance(modal.get("schema"), Mapping) else {}
    interface = schema.get("interface") if isinstance(schema.get("interface"), Mapping) else {}
    routes = interface.get("routes") if isinstance(interface.get("routes"), Mapping) else {}
    route_views = {
        _token(route.get("view"))
        for route in routes.values()
        if isinstance(route, Mapping) and _token(route.get("view"))
    }
    for raw in implements:
        view_id = _token(raw)
        if view_id and view_id in route_views:
            return view_id
    default_route = _token(interface.get("defaultRoute"))
    default_spec = routes.get(default_route) if default_route else None
    if isinstance(default_spec, Mapping):
        return _token(default_spec.get("view"))
    return ""


@dataclass
class MigrationDocument:
    path: Path
    root: dict[str, Any]
    owner: str
    modals: dict[str, Any]
    interface: dict[str, Any]
    changed: bool = False

    @classmethod
    def load(cls, path: Path) -> "MigrationDocument":
        root = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        if not isinstance(root, dict):
            raise ValueError(f"{path}: JSON root must be an object")
        schema_ref = _token(root.get("$schema")).lower()
        is_skill = path.parent.parent.name == "skills" or "webui.v1.schema.json" in schema_ref
        bundled_skill_name = path.name.removesuffix(".webui.json")
        owner = _safe_token(
            path.parent.name if path.parent.parent.name == "skills" else bundled_skill_name
            if is_skill
            else root.get("id") or path.parent.name,
            fallback="ui",
        )
        if is_skill:
            registry = root.setdefault("registry", {})
            modals = registry.setdefault("modals", {}) if isinstance(registry, dict) else {}
            interface = root.setdefault("interface", {})
        else:
            ui = root.setdefault("ui", {})
            application = ui.setdefault("application", {}) if isinstance(ui, dict) else {}
            modals = application.setdefault("modals", {}) if isinstance(application, dict) else {}
            interfaces = application.setdefault("interfaces", {}) if isinstance(application, dict) else {}
            interface = interfaces.setdefault(owner, {}) if isinstance(interfaces, dict) else {}
        if not isinstance(modals, dict) or not isinstance(interface, dict):
            raise ValueError(f"{path}: unsupported WebUI modal/interface structure")
        return cls(path=path, root=root, owner=owner, modals=modals, interface=interface)

    def ensure_modal_view(self, modal_id: str) -> str:
        modal = self.modals.get(modal_id)
        if not isinstance(modal, dict):
            return ""
        existing = _modal_view(modal)
        if existing:
            return existing

        view_id = f"{self.owner}.{_safe_token(modal_id, fallback='modal')}"
        self.interface.setdefault("schema", "adaos.ui.skill_interface.v1")
        views = self.interface.setdefault("views", {})
        if not isinstance(views, dict):
            raise ValueError(f"{self.path}: interface.views must be an object")
        views.setdefault(
            view_id,
            {
                "title": _token(modal.get("title")) or modal_id,
                "surfaces": ["modal"],
                "params": {},
            },
        )
        self.interface.setdefault("defaultView", view_id)

        implements = modal.setdefault("implements", [])
        if not isinstance(implements, list):
            raise ValueError(f"{self.path}: modal {modal_id} implements must be an array")
        if view_id not in implements:
            implements.append(view_id)

        schema = modal.setdefault("schema", {})
        if not isinstance(schema, dict):
            raise ValueError(f"{self.path}: modal {modal_id} schema must be an object")
        modal_interface = schema.setdefault("interface", {})
        if not isinstance(modal_interface, dict):
            raise ValueError(f"{self.path}: modal {modal_id} schema.interface must be an object")
        modal_interface.setdefault("schema", "adaos.ui.modal.interface.v1")
        routes = modal_interface.setdefault("routes", {})
        if not isinstance(routes, dict):
            raise ValueError(f"{self.path}: modal {modal_id} interface.routes must be an object")
        route_id = _safe_token(modal_id.removesuffix("_modal"), fallback="open")
        if route_id in routes and _token((routes.get(route_id) or {}).get("view")) != view_id:
            route_id = f"{route_id}.open"
        routes.setdefault(route_id, {"view": view_id})
        modal_interface.setdefault("defaultRoute", route_id)
        self.changed = True
        return view_id

    def migrate_actions(
        self,
        modal_views: Mapping[str, str],
        *,
        local_modal_views: Mapping[str, str] | None = None,
    ) -> int:
        changed = 0
        local_views = local_modal_views or {}
        for item in _walk(self.root):
            if _token(item.get("type")) == "openModal":
                params = item.get("params") if isinstance(item.get("params"), dict) else {}
                modal_id = _token(params.get("modalId") or params.get("modal_id"))
                if modal_id.startswith("$"):
                    if modal_id == "$event.action.openModal":
                        item["type"] = "navigate"
                        item["params"] = {
                            "to": "$event.action.navigate",
                            "surface": "modal",
                            "modalId": "$event.launchModal",
                        }
                        changed += 1
                    continue
                view_id = _token(local_views.get(modal_id) or modal_views.get(modal_id))
                if not view_id:
                    continue
                item["type"] = "navigate"
                item["params"] = {
                    "to": view_id,
                    "surface": "modal",
                    **params,
                }
                changed += 1

            shorthand = _token(item.get("openModal"))
            view_id = (
                _token(local_views.get(shorthand) or modal_views.get(shorthand))
                if shorthand and not shorthand.startswith("$")
                else ""
            )
            if view_id:
                item.pop("openModal", None)
                item.setdefault("navigate", view_id)
                changed += 1

            launch_modal = _token(item.get("launchModal"))
            launch_view = (
                _token(local_views.get(launch_modal) or modal_views.get(launch_modal))
                if launch_modal and not launch_modal.startswith("$")
                else ""
            )
            action = item.get("action")
            if launch_view and not isinstance(action, Mapping):
                item["action"] = {"navigate": launch_view}
                changed += 1
        if changed:
            self.changed = True
        return changed

    def dump(self) -> str:
        first_indented = next(
            (line for line in self.path.read_text(encoding="utf-8-sig").splitlines()[1:] if line.strip()),
            "  ",
        )
        indent = 4 if first_indented.startswith("    ") else 2
        return json.dumps(self.root, ensure_ascii=False, indent=indent) + "\n"


def migrate_paths(paths: Iterable[Path], *, write: bool = False) -> dict[str, Any]:
    documents = [MigrationDocument.load(Path(path)) for path in paths]
    referenced = set().union(*(_referenced_modal_ids(document.root) for document in documents))
    modal_views: dict[str, str] = {}
    local_modal_views: dict[Path, dict[str, str]] = {}
    ambiguous: set[str] = set()
    for document in documents:
        for modal_id in sorted(referenced.intersection(document.modals)):
            view_id = document.ensure_modal_view(modal_id)
            if view_id:
                local_modal_views.setdefault(document.path, {})[modal_id] = view_id
            previous = modal_views.get(modal_id)
            if previous and previous != view_id:
                ambiguous.add(modal_id)
            elif view_id:
                modal_views[modal_id] = view_id
    for modal_id in ambiguous:
        modal_views.pop(modal_id, None)

    action_total = sum(
        document.migrate_actions(
            modal_views,
            local_modal_views=local_modal_views.get(document.path),
        )
        for document in documents
    )
    remaining_open_modal_ids: list[str] = []
    for document in documents:
        for item in _walk(document.root):
            if _token(item.get("type")) == "openModal":
                params = item.get("params") if isinstance(item.get("params"), Mapping) else {}
                remaining_open_modal_ids.append(
                    _token(params.get("modalId") or params.get("modal_id")) or "<missing>"
                )
            elif _token(item.get("openModal")):
                remaining_open_modal_ids.append(_token(item.get("openModal")))
    remaining_open_modal_total = len(remaining_open_modal_ids)
    changed_paths = [document.path for document in documents if document.changed]
    if write:
        for document in documents:
            if document.changed:
                document.path.write_text(document.dump(), encoding="utf-8")
    return {
        "documents": len(documents),
        "changed": len(changed_paths),
        "actions": action_total,
        "remaining_open_modal_total": remaining_open_modal_total,
        "remaining_open_modal_ids": sorted(remaining_open_modal_ids),
        "ambiguous_modal_ids": sorted(ambiguous),
        "changed_paths": [str(path) for path in changed_paths],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy WebUI openModal actions to public views and navigate.")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    result = migrate_paths(args.paths, write=args.write)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["remaining_open_modal_total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
