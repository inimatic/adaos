from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any


class WebspaceCacheState:
    """Own in-memory resolver, materialization, and source metadata caches.

    Callers intentionally receive values, never the mutable cache mappings.
    Eviction, LRU touches, and invalidation therefore have one owner instead of
    being reimplemented by the compatibility runtime facade.
    """

    def __init__(self) -> None:
        self._webui_declarations: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
        self._skill_declarations: dict[str, tuple[float, str, list[dict[str, Any]]]] = {}
        self._skill_source_fingerprints: dict[str, tuple[float, str]] = {}
        self._resolved_webspaces: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._materialized_webspaces: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._desktop_scenarios: dict[
            str,
            tuple[float, tuple[tuple[str, int, int], ...], list[tuple[str, str]]],
        ] = {}
        self._local_node_display: tuple[float, dict[str, Any]] = (0.0, {})

    def get_local_node_display(self) -> tuple[float, dict[str, Any]]:
        cached_at, display = self._local_node_display
        return cached_at, dict(display)

    def put_local_node_display(self, cached_at: float, display: Mapping[str, Any]) -> None:
        self._local_node_display = (float(cached_at), dict(display))

    def get_webui_declaration(
        self,
        key: str,
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        return self._webui_declarations.get(key)

    def put_webui_declaration(
        self,
        key: str,
        stamp: tuple[Any, ...],
        payload: Mapping[str, Any],
    ) -> None:
        self._webui_declarations[key] = (stamp, dict(payload))

    def discard_webui_declaration(self, key: str) -> None:
        self._webui_declarations.pop(key, None)

    def get_skill_declarations(
        self,
        key: str,
    ) -> tuple[float, str, list[dict[str, Any]]] | None:
        return self._skill_declarations.get(key)

    def put_skill_declarations(
        self,
        key: str,
        cached_at: float,
        fingerprint: str,
        declarations: list[dict[str, Any]],
    ) -> None:
        self._skill_declarations[key] = (
            float(cached_at),
            str(fingerprint),
            declarations,
        )

    def clear_skill_declarations(self) -> None:
        self._skill_declarations.clear()

    def get_skill_source_fingerprint(self, key: str) -> tuple[float, str] | None:
        return self._skill_source_fingerprints.get(key)

    def put_skill_source_fingerprint(
        self,
        key: str,
        cached_at: float,
        fingerprint: str,
    ) -> None:
        self._skill_source_fingerprints[key] = (float(cached_at), str(fingerprint))

    def clear_skill_source_fingerprints(self) -> None:
        self._skill_source_fingerprints.clear()

    def get_desktop_scenarios(
        self,
        key: str,
    ) -> tuple[float, tuple[tuple[str, int, int], ...], list[tuple[str, str]]] | None:
        return self._desktop_scenarios.get(key)

    def put_desktop_scenarios(
        self,
        key: str,
        cached_at: float,
        stamp: tuple[tuple[str, int, int], ...],
        entries: list[tuple[str, str]],
    ) -> None:
        self._desktop_scenarios[key] = (float(cached_at), stamp, list(entries))

    @staticmethod
    def _weight(value: Mapping[str, Any]) -> int:
        try:
            return max(0, int(value.get("_cache_size_bytes") or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _trim_lru(
        cls,
        cache: OrderedDict[str, dict[str, Any]],
        *,
        max_entries: int,
        max_bytes: int,
    ) -> None:
        entry_limit = max(0, int(max_entries))
        byte_limit = max(0, int(max_bytes))
        while cache and (
            len(cache) > entry_limit
            or sum(cls._weight(item) for item in cache.values()) > byte_limit
        ):
            cache.popitem(last=False)

    def get_resolved_webspace(self, key: str) -> dict[str, Any] | None:
        value = self._resolved_webspaces.get(key)
        if value is not None:
            self._resolved_webspaces.move_to_end(key)
        return value

    def put_resolved_webspace(
        self,
        key: str,
        value: dict[str, Any],
        *,
        max_entries: int,
        max_bytes: int,
    ) -> None:
        self._resolved_webspaces[key] = value
        self._resolved_webspaces.move_to_end(key)
        self._trim_lru(
            self._resolved_webspaces,
            max_entries=max_entries,
            max_bytes=max_bytes,
        )

    def clear_resolved_webspaces(self) -> int:
        count = len(self._resolved_webspaces)
        self._resolved_webspaces.clear()
        return count

    def resolved_webspace_count(self) -> int:
        return len(self._resolved_webspaces)

    def get_materialized_webspace(self, key: str) -> dict[str, Any] | None:
        value = self._materialized_webspaces.get(key)
        if value is not None:
            self._materialized_webspaces.move_to_end(key)
        return value

    def put_materialized_webspace(
        self,
        key: str,
        value: dict[str, Any],
        *,
        max_entries: int,
        max_bytes: int,
    ) -> None:
        self._materialized_webspaces[key] = value
        self._materialized_webspaces.move_to_end(key)
        self._trim_lru(
            self._materialized_webspaces,
            max_entries=max_entries,
            max_bytes=max_bytes,
        )

    def discard_materialized_webspace(self, key: str) -> None:
        self._materialized_webspaces.pop(key, None)

    def clear_materialized_webspaces(self) -> int:
        count = len(self._materialized_webspaces)
        self._materialized_webspaces.clear()
        return count

    def materialized_webspace_count(self) -> int:
        return len(self._materialized_webspaces)

    def discard_materialized_webspaces(
        self,
        predicate: Callable[[str, Mapping[str, Any]], bool],
    ) -> int:
        removed = 0
        for key, value in tuple(self._materialized_webspaces.items()):
            if predicate(key, value):
                self._materialized_webspaces.pop(key, None)
                removed += 1
        return removed
