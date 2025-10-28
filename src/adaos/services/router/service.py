from __future__ import annotations

from typing import Any, Callable
from pathlib import Path
import asyncio
import json
import requests

from adaos.services.eventbus import LocalEventBus
from adaos.domain import Event
from adaos.services.node_config import load_config
from .rules_loader import load_rules, watch_rules
from adaos.services.registry.subnet_directory import get_directory
from adaos.services.io_console import print_text


class RouterService:
    def __init__(self, eventbus: LocalEventBus, base_dir: Path) -> None:
        self.bus = eventbus
        self.base_dir = base_dir
        self._started = False
        self._stop_watch: Callable[[], None] | None = None
        self._rules: list[dict[str, Any]] = []
        self._subscribed = False

    def _on_event(self, ev: Event) -> None:
        payload = ev.payload or {}
        text = (payload or {}).get("text")
        if not isinstance(text, str) or not text:
            return

        conf = load_config()
        this_node = conf.node_id
        target_node = this_node

        rule = self._rules[0] if self._rules else None
        if isinstance(rule, dict):
            target = rule.get("target") or {}
            if isinstance(target, dict):
                node_id = target.get("node_id")
                if node_id == "this" or not node_id:
                    target_node = this_node
                else:
                    target_node = str(node_id)

        if target_node == this_node:
            print_text(text, node_id=this_node, origin={"source": ev.source})
            return

        # Cross-node delivery: resolve base_url and POST
        base_url = self._resolve_node_base_url(target_node, conf.role, conf.hub_url)
        if not base_url:
            return
        url = f"{base_url.rstrip('/')}/api/io/console/print"
        headers = {"X-AdaOS-Token": conf.token or "dev-local-token", "Content-Type": "application/json"}
        body = {"text": text, "origin": {"source": ev.source, "from": this_node}}
        try:
            requests.post(url, json=body, headers=headers, timeout=2.5)
        except Exception:
            pass

    def _resolve_node_base_url(self, node_id: str, role: str, hub_url: str | None) -> str | None:
        try:
            if role == "hub":
                directory = get_directory()
                if not directory.is_online(node_id):
                    return None
                return directory.get_node_base_url(node_id)
            # member: ask hub
            if not hub_url:
                return None
            url = f"{hub_url.rstrip('/')}/api/subnet/nodes/{node_id}"
            token = (load_config().token or "dev-local-token")
            r = requests.get(url, headers={"X-AdaOS-Token": token}, timeout=2.5)
            if r.status_code != 200:
                return None
            data = r.json() or {}
            node = data.get("node") or {}
            return node.get("base_url")
        except Exception:
            return None

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        # Subscribe to ui.notify on local event bus
        if not self._subscribed:
            self.bus.subscribe("ui.notify", self._on_event)
            self._subscribed = True
        # Watch rules file
        def _reload(rules: list[dict]):
            self._rules = rules or []

        # Preload rules and start watcher
        self._rules = load_rules(self.base_dir, load_config().node_id)
        self._stop_watch = watch_rules(self.base_dir, load_config().node_id, _reload)

    async def stop(self) -> None:
        if self._stop_watch:
            try:
                self._stop_watch()
            except Exception:
                pass
            self._stop_watch = None
        self._started = False
