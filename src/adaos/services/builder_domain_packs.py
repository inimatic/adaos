"""Explicit, attributable Builder domain-pack loading."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator


DOMAIN_PACK_SCHEMA = "adaos.builder.domain_pack.v1"
_PACK_ROOT = Path(__file__).resolve().parent / "builder" / "domain_packs"
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "abi" / "builder.domain_pack.v1.schema.json"


def _normalized_ids(pack_ids: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(pack_id or "").strip()
            for pack_id in pack_ids or ()
            if str(pack_id or "").strip()
        )
    )


@lru_cache(maxsize=16)
def _load_domain_pack(pack_id: str) -> dict[str, Any]:
    normalized = str(pack_id or "").strip()
    if not normalized:
        raise ValueError("Builder domain pack id is required")
    path = _PACK_ROOT / f"{normalized}.json"
    if not path.is_file():
        raise KeyError(f"Builder domain pack not found: {normalized}")
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))).validate(
        value
    )
    if value["pack_id"] != normalized:
        raise ValueError(f"Builder domain pack identity mismatch: {normalized}")
    result = copy.deepcopy(dict(value))
    result["digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    return result


def load_domain_pack(pack_id: str) -> dict[str, Any]:
    return copy.deepcopy(_load_domain_pack(pack_id))


def resolve_domain_packs(pack_ids: Sequence[str] | None) -> list[dict[str, Any]]:
    return [load_domain_pack(pack_id) for pack_id in _normalized_ids(pack_ids)]


def domain_pack_ids_for_recipes(recipe_ids: Sequence[str] | None) -> list[str]:
    requested = {
        str(recipe_id or "").strip()
        for recipe_id in recipe_ids or ()
        if str(recipe_id or "").strip()
    }
    if not requested:
        return []
    selected: list[str] = []
    for path in sorted(_PACK_ROOT.glob("*.json"), key=lambda item: item.name):
        pack = load_domain_pack(path.stem)
        pack_recipes = {
            str(recipe.get("id") or "").strip()
            for recipe in pack.get("ui_recipes") or []
            if isinstance(recipe, Mapping) and str(recipe.get("id") or "").strip()
        }
        if requested & pack_recipes:
            selected.append(str(pack["pack_id"]))
    return selected


def domain_pack_receipts(pack_ids: Sequence[str] | None) -> list[dict[str, Any]]:
    return [
        {
            "pack_id": pack["pack_id"],
            "version": pack["version"],
            "classification": pack["classification"],
            "digest": pack["digest"],
        }
        for pack in resolve_domain_packs(pack_ids)
    ]


def ui_domain_adapter(pack_ids: Sequence[str] | None) -> ModuleType | None:
    adapters = [
        str(pack.get("ui_adapter") or "").strip()
        for pack in resolve_domain_packs(pack_ids)
        if str(pack.get("ui_adapter") or "").strip()
    ]
    if not adapters:
        return None
    if len(set(adapters)) != 1:
        raise ValueError("only one Builder UI domain adapter may be active")
    return importlib.import_module(adapters[0])


def domain_prompt_rules(pack_ids: Sequence[str] | None) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pack in resolve_domain_packs(pack_ids):
        for raw in pack.get("prompt_rules") or []:
            if not isinstance(raw, Mapping):
                raise ValueError(
                    f"domain pack {pack['pack_id']} contains an invalid prompt rule"
                )
            rule = copy.deepcopy(dict(raw))
            rule_id = str(rule.get("id") or "").strip()
            if not rule_id or rule_id in seen:
                raise ValueError(
                    f"duplicate or empty domain prompt rule id: {rule_id!r}"
                )
            seen.add(rule_id)
            rule["domain_pack"] = {
                "pack_id": pack["pack_id"],
                "version": pack["version"],
                "digest": pack["digest"],
            }
            rules.append(rule)
    return rules


__all__ = [
    "DOMAIN_PACK_SCHEMA",
    "domain_pack_ids_for_recipes",
    "domain_pack_receipts",
    "domain_prompt_rules",
    "load_domain_pack",
    "resolve_domain_packs",
    "ui_domain_adapter",
]
