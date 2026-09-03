from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from fnmatch import fnmatch
from functools import lru_cache
from importlib import resources
from typing import Any, Mapping, Sequence


_REGISTRY_PACKAGE = "adaos.services.builder"
_REGISTRY_NAME = "prompt_rule_capsules.json"


@lru_cache(maxsize=1)
def load_prompt_rule_registry() -> dict[str, Any]:
    raw = resources.files(_REGISTRY_PACKAGE).joinpath(_REGISTRY_NAME).read_bytes()
    registry = json.loads(raw.decode("utf-8"))
    if registry.get("schema") != "adaos.builder.prompt_rule_registry.v1":
        raise ValueError("unsupported Builder prompt rule registry schema")
    if not str(registry.get("version") or "").strip():
        raise ValueError("Builder prompt rule registry version is required")
    if not str(registry.get("published_at") or "").strip():
        raise ValueError("Builder prompt rule registry published_at is required")
    if not str(registry.get("authority_ref") or "").strip():
        raise ValueError("Builder prompt rule registry authority_ref is required")
    items = registry.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Builder prompt rule registry items are required")
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Builder prompt rule registry items must be objects")
        rule_id = str(item.get("id") or "").strip()
        rules = item.get("rules")
        if not rule_id or rule_id in ids:
            raise ValueError("Builder prompt rule ids must be non-empty and unique")
        if not isinstance(rules, list) or not all(str(rule).strip() for rule in rules):
            raise ValueError(f"Builder prompt rule {rule_id} requires non-empty rules")
        ids.add(rule_id)
    registry["digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    return registry


def select_prompt_rules(
    *,
    target_type: str,
    evidence: str,
    facts: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    registry = load_prompt_rule_registry()
    selected: list[dict[str, Any]] = []
    normalized_target = str(target_type or "").strip().lower()
    normalized_evidence = str(evidence or "").lower()
    normalized_facts = dict(facts or {})
    profile = str(normalized_facts.get("profile") or "").strip().lower()
    target_paths = [
        str(value).strip().replace("\\", "/").lower()
        for value in normalized_facts.get("target_files") or []
        if str(value).strip()
    ]
    target_refs = [
        str(value).strip().lower()
        for value in normalized_facts.get("target_refs") or []
        if str(value).strip()
    ]
    facet_keys = {
        str(value).strip().lower()
        for value in normalized_facts.get("facet_keys") or []
        if str(value).strip()
    }
    for raw_item in registry["items"]:
        item = deepcopy(dict(raw_item))
        applicability = dict(item.get("applicability") or {})
        target_types = {
            str(value).strip().lower()
            for value in applicability.get("target_types") or []
            if str(value).strip()
        }
        if target_types and normalized_target not in target_types:
            continue
        markers = [
            str(value).strip().lower()
            for value in applicability.get("match_any") or []
            if str(value).strip()
        ]
        profiles = {
            str(value).strip().lower()
            for value in applicability.get("profiles") or []
            if str(value).strip()
        }
        path_globs = [
            str(value).strip().replace("\\", "/").lower()
            for value in applicability.get("target_path_globs") or []
            if str(value).strip()
        ]
        ref_prefixes = [
            str(value).strip().lower()
            for value in applicability.get("target_ref_prefixes") or []
            if str(value).strip()
        ]
        required_facets = {
            str(value).strip().lower()
            for value in applicability.get("facet_keys") or []
            if str(value).strip()
        }
        matches = [
            any(marker in normalized_evidence for marker in markers),
            bool(profile and profile in profiles),
            any(fnmatch(path, pattern) for path in target_paths for pattern in path_globs),
            any(ref.startswith(prefix) for ref in target_refs for prefix in ref_prefixes),
            bool(facet_keys & required_facets),
        ]
        if applicability.get("always") is not True and not any(matches):
            continue
        item["registry_version"] = registry["version"]
        item["registry_digest"] = registry["digest"]
        selected.append(item)
    return selected


def context_capsule_request(rule: Mapping[str, Any]) -> dict[str, Any]:
    registry = load_prompt_rule_registry()
    rule_id = str(rule.get("id") or "").strip()
    return {
        "kind": "procedural",
        "subject_refs": [f"prompt-rule:{rule_id}"],
        "authority_ref": registry["authority_ref"],
        "trust_class": "validated",
        "sensitivity": "internal",
        "license": "internal",
        "retention_class": "accepted_release_lineage",
        "origin": {
            "type": "core_declaration",
            "registry_schema": registry["schema"],
            "registry_version": registry["version"],
        },
        "source_digests": {"prompt_rule_registry": registry["digest"]},
        "valid_from": registry["published_at"],
        "recorded_at": registry["published_at"],
        "summary": rule.get("title"),
        "index": list(dict(rule.get("applicability") or {}).get("match_any") or []),
        "content": {
            "rule_id": rule_id,
            "source": rule.get("source"),
            "rules": list(rule.get("rules") or []),
            "applicability": dict(rule.get("applicability") or {}),
        },
        "metadata": {
            "prompt_rule_id": rule_id,
            "registry_version": registry["version"],
        },
    }


__all__: Sequence[str] = (
    "context_capsule_request",
    "load_prompt_rule_registry",
    "select_prompt_rules",
)
