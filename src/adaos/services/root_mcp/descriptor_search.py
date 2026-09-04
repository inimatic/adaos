from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from .registry import get_descriptor_set, list_descriptor_sets


_CHILD_INDEX_DESCRIPTORS = {
    "sdk_metadata",
    "ui_capability_catalog",
    "architecture_catalog",
    "template_catalog",
    "public_skill_registry_summary",
    "public_scenario_registry_summary",
}


def _terms(value: str | None) -> list[str]:
    return list(
        dict.fromkeys(
            token.lower()
            for token in re.findall(r"[^\W_]+", str(value or ""), flags=re.UNICODE)
            if len(token) >= 2
        )
    )[:24]


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _header(
    *,
    descriptor_id: str,
    item_id: str,
    kind: str,
    title: str,
    summary: str = "",
    owner: str = "root",
    stability: str = "experimental",
    fingerprint: str | None = None,
    args: Sequence[Any] = (),
    tags: Sequence[Any] = (),
) -> dict[str, Any]:
    identity = {
        "descriptor_id": descriptor_id,
        "item_id": item_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "owner": owner,
        "stability": stability,
    }
    return {
        **identity,
        **({"args": [str(item) for item in args if str(item).strip()]} if args else {}),
        "tags": [str(item) for item in tags if str(item).strip()],
        "fingerprint": fingerprint or _fingerprint(identity),
        "drill_down": {"descriptor_id": descriptor_id, "item_id": item_id},
    }


def _catalog_header(entry: Mapping[str, Any]) -> dict[str, Any]:
    descriptor_id = str(entry.get("descriptor_id") or "")
    overview = entry.get("overview") if isinstance(entry.get("overview"), Mapping) else {}
    return _header(
        descriptor_id=descriptor_id,
        item_id=descriptor_id,
        kind=str(overview.get("kind") or f"descriptor.{entry.get('descriptor_class') or 'set'}"),
        title=str(entry.get("title") or descriptor_id),
        summary=str(entry.get("summary") or ""),
        owner=str(overview.get("owner") or "root"),
        stability=str(entry.get("stability") or "experimental"),
        fingerprint=str(entry.get("fingerprint") or "") or None,
        tags=entry.get("tags") or (),
    )


def _payload_headers(descriptor_id: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if descriptor_id == "sdk_metadata":
        rows: list[dict[str, Any]] = []
        for item in payload.get("overview_rows") or []:
            if not isinstance(item, Mapping):
                continue
            drill_down = item.get("drill_down") if isinstance(item.get("drill_down"), Mapping) else {}
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            item_id = str(drill_down.get("item_id") or item.get("row_id") or "")
            if not item_id:
                continue
            rows.append(
                _header(
                    descriptor_id=descriptor_id,
                    item_id=item_id,
                    kind=str(item.get("kind") or "sdk_function"),
                    title=str(item.get("title") or item_id),
                    summary=str(item.get("summary") or ""),
                    owner=str(item.get("owner") or "adaos.sdk"),
                    stability=str(item.get("stability") or "experimental"),
                    fingerprint=str(item.get("fingerprint") or "") or None,
                    args=metadata.get("args") or (),
                )
            )
        return rows
    if descriptor_id == "ui_capability_catalog":
        rows = []
        for key, fallback_kind in (
            ("layouts", "ui.layout"),
            ("components", "ui.component"),
            ("recipes", "ui.recipe"),
        ):
            for item in payload.get(key) or []:
                if not isinstance(item, Mapping):
                    continue
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                aliases = item.get("aliases") if isinstance(item.get("aliases"), Mapping) else {}
                tags = [
                    str(alias)
                    for values in aliases.values()
                    if isinstance(values, list)
                    for alias in values
                    if str(alias).strip()
                ]
                rows.append(
                    _header(
                        descriptor_id=descriptor_id,
                        item_id=item_id,
                        kind=str(item.get("kind") or fallback_kind),
                        title=str(item.get("title") or item_id),
                        summary=str(item.get("summary") or ""),
                        owner="adaos.client",
                        stability=str(payload.get("stage") or "experimental"),
                        tags=tags,
                    )
                )
        return rows
    if descriptor_id == "architecture_catalog":
        return [
            _header(
                descriptor_id=descriptor_id,
                item_id=str(item.get("path") or item.get("title") or ""),
                kind="architecture.page",
                title=str(item.get("title") or item.get("path") or ""),
                summary=str(item.get("summary") or ""),
            )
            for item in payload.get("pages") or []
            if isinstance(item, Mapping) and str(item.get("path") or item.get("title") or "").strip()
        ]
    if descriptor_id in {"public_skill_registry_summary", "public_scenario_registry_summary"}:
        rows = []
        for item in payload.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            overview = item.get("overview") if isinstance(item.get("overview"), Mapping) else {}
            item_id = str(item.get("id") or overview.get("row_id") or "")
            if not item_id:
                continue
            rows.append(
                _header(
                    descriptor_id=descriptor_id,
                    item_id=item_id,
                    kind=str(overview.get("kind") or payload.get("kind") or "registry.item"),
                    title=str(item.get("name") or overview.get("title") or item_id),
                    summary=str(item.get("description") or overview.get("summary") or ""),
                    owner=str(overview.get("owner") or "workspace"),
                    stability=str(overview.get("stability") or "published"),
                    fingerprint=str(overview.get("fingerprint") or "") or None,
                )
            )
        return rows
    if descriptor_id == "template_catalog":
        return [
            _header(
                descriptor_id=descriptor_id,
                item_id=f"{kind}:{name}",
                kind=f"template.{kind[:-1]}",
                title=str(name),
                summary=f"AdaOS {kind[:-1]} template.",
                stability="published",
            )
            for kind in ("skills", "scenarios")
            for name in payload.get(kind) or []
            if str(name).strip()
        ]
    return []


def _score(row: Mapping[str, Any], terms: Sequence[str]) -> int:
    item_id = str(row.get("item_id") or "").lower()
    title = str(row.get("title") or "").lower()
    summary = str(row.get("summary") or "").lower()
    owner = str(row.get("owner") or "").lower()
    tags = " ".join(str(item).lower() for item in row.get("tags") or [])
    searchable = " ".join((item_id, title, summary, owner, tags))
    score = 0
    for term in terms:
        if term == item_id or term == title:
            score += 40
        elif term in item_id or term in title:
            score += 12
        if term in tags:
            score += 6
        if term in summary:
            score += 3
        if term in owner:
            score += 2
        if term in searchable:
            score += 1
    return score


def search_descriptors(
    query: str,
    *,
    descriptor_ids: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    """Search compact authoritative headers without returning descriptor payloads."""

    text = str(query or "").strip()
    if not text:
        raise ValueError("descriptor search query is required")
    bounded_limit = max(1, min(int(limit or 12), 64))
    query_terms = _terms(text)
    catalog = list_descriptor_sets()
    available_ids = {str(item.get("descriptor_id") or "") for item in catalog}
    selected_ids = {str(item).strip().lower() for item in descriptor_ids or () if str(item).strip()}
    unknown_ids = sorted(selected_ids - available_ids)
    if unknown_ids:
        raise KeyError(unknown_ids[0])
    selected_kinds = {str(item).strip().lower() for item in kinds or () if str(item).strip()}
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    ordinal = 0
    for entry in catalog:
        descriptor_id = str(entry.get("descriptor_id") or "")
        if selected_ids and descriptor_id not in selected_ids:
            continue
        parent = _catalog_header(entry)
        descriptor_class = str(entry.get("descriptor_class") or "").lower()
        parent_score = _score(parent, query_terms)
        if parent_score > 0 and (
            not selected_kinds
            or descriptor_class in selected_kinds
            or str(parent.get("kind") or "").lower() in selected_kinds
        ):
            ranked.append((parent_score, ordinal, parent))
            ordinal += 1
        if descriptor_id not in _CHILD_INDEX_DESCRIPTORS:
            continue
        try:
            descriptor = get_descriptor_set(
                descriptor_id,
                level="mini",
                query=text if descriptor_id == "sdk_metadata" else None,
                limit=bounded_limit,
            )
        except RuntimeError:
            continue
        payload = descriptor.get("payload") if isinstance(descriptor.get("payload"), Mapping) else {}
        for index, child in enumerate(_payload_headers(descriptor_id, payload)):
            child_kind = str(child.get("kind") or "").lower()
            if selected_kinds and child_kind not in selected_kinds and descriptor_class not in selected_kinds:
                continue
            child_score = _score(child, query_terms)
            if descriptor_id == "sdk_metadata":
                child_score = max(child_score, 30 - index)
            if child_score > 0:
                ranked.append((child_score, ordinal, child))
                ordinal += 1
    ranked.sort(key=lambda item: (-item[0], item[1], str(item[2].get("item_id") or "")))
    items = []
    for rank, (_, _, row) in enumerate(ranked[:bounded_limit], start=1):
        compact = dict(row)
        compact.pop("tags", None)
        compact["rank"] = rank
        items.append(compact)
    return {
        "schema": "adaos.descriptor.search.v1",
        "query_digest": _fingerprint({"query": text}),
        "count": len(items),
        "limit": bounded_limit,
        "items": items,
    }


def get_descriptor_item(
    descriptor_id: str,
    item_id: str,
    *,
    level: str = "std",
) -> dict[str, Any]:
    """Return one exact descriptor item selected from a search result."""

    token = str(descriptor_id or "").strip().lower()
    selected_item_id = str(item_id or "").strip()
    if not token:
        raise ValueError("descriptor_id is required")
    if not selected_item_id:
        raise ValueError("item_id is required")
    effective_level = str(level or "std").strip().lower()
    if effective_level not in {"mini", "std", "rich"}:
        effective_level = "std"
    descriptor = get_descriptor_set(
        token,
        level=effective_level,
        query=selected_item_id if token == "sdk_metadata" else None,
        limit=8,
    )
    if selected_item_id == token:
        item: Any = descriptor
    else:
        payload = descriptor.get("payload") if isinstance(descriptor.get("payload"), Mapping) else {}
        if token == "sdk_metadata" and effective_level != "mini":
            item = next(
                (
                    dict(candidate)
                    for candidate in payload.get("tools") or []
                    if isinstance(candidate, Mapping)
                    and str(candidate.get("name") or candidate.get("n") or "") == selected_item_id
                ),
                None,
            )
        elif token == "architecture_catalog":
            item = next(
                (
                    dict(candidate)
                    for candidate in payload.get("pages") or []
                    if isinstance(candidate, Mapping)
                    and str(candidate.get("path") or candidate.get("title") or "") == selected_item_id
                ),
                None,
            )
        elif token == "ui_capability_catalog":
            item = next(
                (
                    dict(candidate)
                    for key in ("layouts", "components", "recipes")
                    for candidate in payload.get(key) or []
                    if isinstance(candidate, Mapping)
                    and str(candidate.get("id") or "") == selected_item_id
                ),
                None,
            )
        elif token in {"public_skill_registry_summary", "public_scenario_registry_summary"}:
            item = next(
                (
                    dict(candidate)
                    for candidate in payload.get("items") or []
                    if isinstance(candidate, Mapping) and str(candidate.get("id") or "") == selected_item_id
                ),
                None,
            )
        elif token == "template_catalog" and ":" in selected_item_id:
            kind, name = selected_item_id.split(":", 1)
            item = {"kind": kind[:-1], "name": name} if name in (payload.get(kind) or []) else None
        else:
            item = next(
                (
                    row
                    for row in _payload_headers(token, payload)
                    if str(row.get("item_id") or "") == selected_item_id
                ),
                None,
            )
        if item is None:
            raise KeyError(selected_item_id)
    return {
        "schema": "adaos.descriptor.item.v1",
        "descriptor_id": token,
        "item_id": selected_item_id,
        "level": effective_level,
        "item": item,
    }


__all__ = ["get_descriptor_item", "search_descriptors"]
