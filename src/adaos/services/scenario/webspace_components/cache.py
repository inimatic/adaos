from __future__ import annotations

from collections import OrderedDict
from typing import Any


class WebspaceCacheState:
    """Own in-memory resolver, materialization, and source metadata caches."""

    def __init__(self) -> None:
        self.webui_declarations: dict[str, tuple[tuple[str, int, int], dict[str, Any]]] = {}
        self.skill_declarations: dict[str, tuple[float, str, list[dict[str, Any]]]] = {}
        self.skill_source_fingerprints: dict[str, tuple[float, str]] = {}
        self.resolved_webspaces: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.materialized_webspaces: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.desktop_scenarios: dict[
            str,
            tuple[float, tuple[tuple[str, int, int], ...], list[tuple[str, str]]],
        ] = {}
        self.local_node_display: tuple[float, dict[str, Any]] = (0.0, {})
