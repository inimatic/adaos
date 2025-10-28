from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import yaml


def load_capacity_from_node_yaml(base_dir: Path | None = None) -> Dict[str, Any]:
    """
    Reads node.yaml from the active base dir and returns a minimal capacity snapshot.
    Structure example:
    {
      "io": [
        {"io_type": "stdout", "capabilities": ["text", "lang:ru", "lang:en"], "priority": 50}
      ]
    }
    """
    # Resolve .adaos base dir lazily from env/context if not provided
    if base_dir is None:
        try:
            from adaos.services.node_config import node_base_dir

            base_dir = node_base_dir()
        except Exception:
            base_dir = Path.home() / ".adaos"

    node_path = Path(base_dir) / "node.yaml"
    try:
        data = yaml.safe_load(node_path.read_text(encoding="utf-8")) if node_path.exists() else {}
    except Exception:
        data = {}

    # Allow future extensions from node.yaml, but ensure stdout entry exists
    io_list: list[dict[str, Any]] = []
    io_cfg = (data or {}).get("capacity") or {}
    raw = io_cfg.get("io") if isinstance(io_cfg, dict) else None
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                io_list.append({
                    "io_type": item.get("io_type") or item.get("type") or "stdout",
                    "capabilities": list(item.get("capabilities") or []),
                    "priority": int(item.get("priority") or 50),
                })

    if not any(x.get("io_type") == "stdout" for x in io_list):
        io_list.append({
            "io_type": "stdout",
            "capabilities": ["text", "lang:ru", "lang:en"],
            "priority": 50,
        })

    return {"io": io_list}


def get_local_capacity() -> Dict[str, Any]:
    return load_capacity_from_node_yaml()

