from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, TypedDict

from adaos.services.agent_context import get_ctx
from .subnet_repo import SubnetRepo


class LiveState(TypedDict, total=False):
    online: bool
    last_seen: float


class SubnetDirectory:
    def __init__(self) -> None:
        ctx = get_ctx()
        self.repo = SubnetRepo(ctx.sql)
        self.live: Dict[str, LiveState] = {}
        # preload persisted nodes as offline until first heartbeat
        for n in self.repo.list_nodes():
            self.live[n["node_id"]] = {"online": False, "last_seen": float(n.get("last_seen") or 0.0)}

    # ------ lifecycle events ------
    def on_register(self, node_info: Dict[str, Any]) -> None:
        node = {
            "node_id": node_info.get("node_id"),
            "subnet_id": node_info.get("subnet_id"),
            "roles": list(node_info.get("roles") or []),
            "hostname": node_info.get("hostname"),
            "base_url": node_info.get("base_url"),
            "last_seen": time.time(),
        }
        self.repo.upsert_node(node)
        capacity = node_info.get("capacity") or {}
        self.repo.replace_io_capacity(node["node_id"], capacity.get("io") or [])
        self.repo.replace_skill_capacity(node["node_id"], capacity.get("skills") or [])
        self.live[node["node_id"]] = {"online": True, "last_seen": node["last_seen"]}

    def on_heartbeat(self, node_id: str, capacity: Optional[Dict[str, Any]]) -> None:
        ts = time.time()
        self.repo.touch_heartbeat(node_id, ts, capacity)
        st = self.live.get(node_id) or {}
        st["online"] = True
        st["last_seen"] = ts
        self.live[node_id] = st

    # ------ queries ------
    def mark_stale_if_expired(self, ttl: float = 45.0) -> None:
        now = time.time()
        for nid, st in list(self.live.items()):
            last = float(st.get("last_seen") or 0.0)
            if (now - last) > ttl:
                st["online"] = False
                self.live[nid] = st

    def is_online(self, node_id: str) -> bool:
        return bool((self.live.get(node_id) or {}).get("online", False))

    def find_nodes_with_skill(self, name: str, require_online: bool = True) -> List[Dict[str, Any]]:
        nodes = self.repo.nodes_with_skill(name)
        if require_online:
            nodes = [n for n in nodes if self.is_online(n.get("node_id", ""))]
        return nodes

    def get_node_base_url(self, node_id: str) -> Optional[str]:
        n = self.repo.get_node(node_id)
        return n.get("base_url") if n else None

    def list_known_nodes(self) -> List[Dict[str, Any]]:
        items = []
        for n in self.repo.list_nodes():
            node = dict(n)
            node["online"] = self.is_online(n["node_id"])  # overlay live
            items.append(node)
        return items


_DIR: SubnetDirectory | None = None


def get_directory() -> SubnetDirectory:
    global _DIR
    if _DIR is None:
        _DIR = SubnetDirectory()
    return _DIR

