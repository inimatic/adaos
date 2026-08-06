# \src\adaos\services\scenario\projection_registry.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional
from adaos.services.scenarios.loader import read_manifest


ProjectionBackend = Literal["yjs", "kv", "sql"]


@dataclass(slots=True)
class ProjectionTarget:
    """
    Single physical projection target for a (scope, slot) pair.

    backend:
      - "yjs"  — project into a YDoc path,
      - "kv"   — project into a KV key,
      - "sql"  — project into a SQL table/column (reserved for future use).
    """

    backend: ProjectionBackend
    webspace_id: Optional[str] = None
    path: Optional[str] = None
    table: Optional[str] = None
    column: Optional[str] = None


@dataclass(slots=True)
class ProjectionRule:
    scope: str
    slot: str
    targets: List[ProjectionTarget]
    route: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    guard_visibility: Any = None


class ProjectionRegistry:
    """
    Registry that maps (scope, slot) pairs used by ctx.*.set/get to
    concrete storage targets (Yjs paths, KV keys, SQL rows, ...).

    For the MVP this is a lightweight, read-only facade over scenario
    manifests: if a scenario.yaml defines a `data_projections` section,
    entries from there are loaded into this registry.
    """

    def __init__(self) -> None:
        self._rules: Dict[tuple[str, str], ProjectionRule] = {}
        self._skill_rules: Dict[str, Dict[tuple[str, str], ProjectionRule]] = {}
        self._skill_rule_order: list[str] = []
        self._scenario_rules: Dict[tuple[str, str], ProjectionRule] = {}
        self._active_scenario_id: Optional[str] = None
        self._active_space: str = "workspace"

    @staticmethod
    def _route_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        payload = manifest if isinstance(manifest, dict) else {}
        routes = payload.get("data_routes") if isinstance(payload.get("data_routes"), list) else []
        result: dict[str, dict[str, Any]] = {}
        for item in routes:
            if not isinstance(item, dict):
                continue
            if str(item.get("route") or "").strip().lower() != "yjs":
                continue
            slot = str(item.get("projection_slot") or "").strip()
            if not slot:
                continue
            result.setdefault(slot, dict(item))
        return result

    @staticmethod
    def _rule_metadata(item: dict[str, Any], route_index: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], Any]:
        slot = str(item.get("slot") or "").strip()
        route = dict(route_index.get(slot) or {})
        raw_budget = route.get("budget") if isinstance(route.get("budget"), dict) else item.get("budget")
        budget = dict(raw_budget) if isinstance(raw_budget, dict) else {}
        guard_visibility = route.get("guard_visibility") if route else item.get("guard_visibility")
        return route, budget, guard_visibility

    def load_manifest(self, manifest: dict[str, Any]) -> int:
        payload = manifest if isinstance(manifest, dict) else {}
        entries = payload.get("data_projections") or []
        return self.load_entries(entries, route_index=self._route_index(payload))

    def load_entries(self, entries: list[dict], *, route_index: dict[str, dict[str, Any]] | None = None) -> int:
        """
        Load projection rules from a generic ``data_projections``-like list.

        This helper is shared between scenario manifests and skill manifests
        so that skills can define default projections and scenarios can
        override them by calling :meth:`load_from_scenario` later.
        """
        rules = self._build_rules(entries, route_index=route_index)
        self._rules.update(rules)
        return len(rules)

    @classmethod
    def _build_rules(
        cls,
        entries: list[dict],
        *,
        route_index: dict[str, dict[str, Any]] | None = None,
    ) -> Dict[tuple[str, str], ProjectionRule]:
        raw = entries or []
        if not isinstance(raw, list):
            return {}
        route_lookup = route_index if isinstance(route_index, dict) else {}
        rules: Dict[tuple[str, str], ProjectionRule] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue

            scope = str(item.get("scope") or "").strip()
            slot = str(item.get("slot") or "").strip()
            if not scope or not slot:
                continue

            targets_raw = item.get("targets") or []
            if not isinstance(targets_raw, list):
                continue

            targets: List[ProjectionTarget] = []
            for t in targets_raw:
                if not isinstance(t, dict):
                    continue
                backend = str(t.get("backend") or "").strip().lower()
                if backend not in ("yjs", "kv", "sql"):
                    continue
                targets.append(
                    ProjectionTarget(
                        backend=backend,  # type: ignore[arg-type]
                        webspace_id=str(t.get("webspace_id") or "") or None,
                        path=str(t.get("path") or "") or None,
                        table=str(t.get("table") or "") or None,
                        column=str(t.get("column") or "") or None,
                    )
                )
            key = (scope, slot)
            if targets:
                route, budget, guard_visibility = cls._rule_metadata(item, route_lookup)
                rules[key] = ProjectionRule(
                    scope=scope,
                    slot=slot,
                    targets=targets,
                    route=route,
                    budget=budget,
                    guard_visibility=guard_visibility,
                )
        return rules

    def replace_skill_manifest(self, skill_name: str, manifest: dict[str, Any]) -> int:
        """Replace one skill's complete default-rule layer atomically."""

        owner = str(skill_name or "").strip()
        if not owner:
            raise ValueError("skill name is required")
        payload = manifest if isinstance(manifest, dict) else {}
        entries = payload.get("data_projections") or []
        rules = self._build_rules(entries, route_index=self._route_index(payload))
        self._skill_rules[owner] = rules
        if owner not in self._skill_rule_order:
            self._skill_rule_order.append(owner)
        return len(rules)

    def replace_scenario_entries(
        self,
        entries: list[dict],
        *,
        scenario_id: Optional[str] = None,
        space: str = "workspace",
        route_index: dict[str, dict[str, Any]] | None = None,
    ) -> int:
        """
        Replace the active scenario override layer.

        Skill-level defaults are stored in ``_rules`` and remain intact.
        Scenario manifests act as a single active override layer so switching
        to a scenario without ``data_projections`` correctly clears stale rules
        from the previous scenario.
        """
        self._scenario_rules = {}
        self._active_scenario_id = str(scenario_id or "").strip() or None
        self._active_space = "dev" if str(space or "").strip().lower() == "dev" else "workspace"

        raw = entries or []
        if not isinstance(raw, list):
            return 0
        manifest_route_lookup = route_index if isinstance(route_index, dict) else {}

        loaded = 0
        for item in raw:
            if not isinstance(item, dict):
                continue

            scope = str(item.get("scope") or "").strip()
            slot = str(item.get("slot") or "").strip()
            if not scope or not slot:
                continue

            targets_raw = item.get("targets") or []
            if not isinstance(targets_raw, list):
                continue

            targets: List[ProjectionTarget] = []
            for t in targets_raw:
                if not isinstance(t, dict):
                    continue
                backend = str(t.get("backend") or "").strip().lower()
                if backend not in ("yjs", "kv", "sql"):
                    continue
                targets.append(
                    ProjectionTarget(
                        backend=backend,  # type: ignore[arg-type]
                        webspace_id=str(t.get("webspace_id") or "") or None,
                        path=str(t.get("path") or "") or None,
                        table=str(t.get("table") or "") or None,
                        column=str(t.get("column") or "") or None,
                    )
                )

            key = (scope, slot)
            if targets:
                route, budget, guard_visibility = self._rule_metadata(item, manifest_route_lookup)
                self._scenario_rules[key] = ProjectionRule(
                    scope=scope,
                    slot=slot,
                    targets=targets,
                    route=route,
                    budget=budget,
                    guard_visibility=guard_visibility,
                )
                loaded += 1
        return loaded

    def load_from_scenario(self, scenario_id: str, *, space: str = "workspace") -> int:
        """
        Load projection rules from scenario.yaml for the given scenario id.

        Expected shape (optional) inside scenario.yaml:

        data_projections:
          - scope: subnet
            slot: example.snapshot
            targets:
              - backend: yjs
                webspace_id: desktop
                path: data/skills/example/global/snapshot
        """
        manifest = read_manifest(scenario_id, space=space)
        entries = manifest.get("data_projections") or []
        return self.replace_scenario_entries(
            entries,
            scenario_id=scenario_id,
            space=space,
            route_index=self._route_index(manifest),
        )

    def resolve_rule(self, scope: str, slot: str) -> ProjectionRule | None:
        key = (str(scope).strip(), str(slot).strip())
        scenario_rule = self._scenario_rules.get(key)
        if scenario_rule is not None:
            return scenario_rule
        for owner in reversed(self._skill_rule_order):
            rule = self._skill_rules.get(owner, {}).get(key)
            if rule is not None:
                return rule
        return self._rules.get(key)

    def resolve(self, scope: str, slot: str) -> List[ProjectionTarget]:
        """
        Resolve a (scope, slot) pair to a list of projection targets.

        If no rule is present, returns an empty list; callers should treat
        this as "no projections configured".
        """
        rule = self.resolve_rule(scope, slot)
        return list(rule.targets) if rule else []

    def active_scenario_id(self) -> Optional[str]:
        return self._active_scenario_id

    def active_space(self) -> str:
        return self._active_space

    def snapshot(self) -> dict[str, object]:
        base_keys = set(self._rules)
        for rules in self._skill_rules.values():
            base_keys.update(rules)
        return {
            "active_scenario_id": self._active_scenario_id,
            "active_space": self._active_space,
            "base_rule_count": len(base_keys),
            "skill_rule_count": sum(len(rules) for rules in self._skill_rules.values()),
            "skill_owner_count": len(self._skill_rules),
            "scenario_rule_count": len(self._scenario_rules),
        }


__all__ = ["ProjectionBackend", "ProjectionTarget", "ProjectionRule", "ProjectionRegistry"]
