"""Export tool metadata for discovery by LLMs and other clients."""

from __future__ import annotations

import importlib
import hashlib
import inspect
import os
import pkgutil
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from .decorators import emits_map, event_payloads, tools_meta, tools_registry

_ALLOWED_TOOL_PREFIXES: Tuple[str, ...] = ("manage.", "skills.", "scenarios.", "resources.")
_DISCOVERY_PACKAGES: Tuple[str, ...] = ("adaos.sdk.manage", "adaos.sdk.data")
_PUBLIC_FACADE_MODULES: Tuple[str, ...] = (
    "adaos.sdk.control_plane",
    "adaos.sdk.conversation",
    "adaos.sdk.context",
    "adaos.sdk.deployment",
    "adaos.sdk.distributed",
    "adaos.sdk.execution",
    "adaos.sdk.research",
    "adaos.sdk.status",
    "adaos.sdk.web",
    "adaos.sdk.workflow",
)
_PUBLIC_FACADE_SUMMARIES: dict[str, str] = {
    "adaos.sdk.control_plane": "Read canonical node, subnet, reliability, quota, and inventory projections.",
    "adaos.sdk.conversation": "Read and update governed conversational threads and Builder topics.",
    "adaos.sdk.context": "Resolve, compile, inspect, and bind governed agent context.",
    "adaos.sdk.deployment": "Plan and inspect project deployment through the public SDK boundary.",
    "adaos.sdk.distributed": "Describe and operate governed distributed datasets and services.",
    "adaos.sdk.execution": "Declare and inspect bounded execution jobs and artifacts.",
    "adaos.sdk.research": "Use governed research inquiry, synthesis, and evidence workflows.",
    "adaos.sdk.status": "Publish bounded skill and scenario status projections.",
    "adaos.sdk.web": "Read and update declarative desktop, application, and webspace state.",
    "adaos.sdk.workflow": "Create and invoke declarative workflow interactions.",
}
_GENERIC_QUERY_TERMS = {
    "add",
    "adaos",
    "and",
    "api",
    "component",
    "current",
    "data",
    "for",
    "from",
    "need",
    "please",
    "project",
    "sdk",
    "show",
    "skill",
    "the",
    "this",
    "with",
    "public",
    "данные",
    "добавить",
    "компонент",
    "навык",
    "нужно",
    "покажи",
    "проект",
    "публичный",
    "публичного",
    "публичные",
}
_QUERY_TERM_EXPANSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("токен", ("token", "quota", "usage")),
    ("расход", ("usage", "quota", "metering")),
    ("использован", ("usage", "used", "quota")),
    ("остат", ("remaining", "quota", "limit")),
    ("квот", ("quota", "limit", "remaining")),
    ("подпис", ("subscription", "quota", "plan")),
    ("лимит", ("limit", "quota", "remaining")),
    ("usage", ("usage", "quota", "metering")),
    ("subscription", ("subscription", "quota", "plan")),
    ("token", ("token", "quota", "usage")),
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # pragma: no cover - git not available
        return "unknown"


def _preload_modules() -> None:
    env_override = os.getenv("ADAOS_SDK_EXPORT_MODULES")
    if env_override:
        modules = [m.strip() for m in env_override.split(",") if m.strip()]
    else:
        modules = list(_DISCOVERY_PACKAGES)
    for mod_name in modules:
        try:
            module = importlib.import_module(mod_name)
        except Exception:  # pragma: no cover - import errors should not break export
            continue
        path = getattr(module, "__path__", None)
        if not path:
            continue
        for finder in pkgutil.walk_packages(path, prefix=f"{module.__name__}."):
            try:
                importlib.import_module(finder.name)
            except Exception:  # pragma: no cover - skip faulty modules silently
                continue


def _filter_tools() -> List[Tuple[str, str, Any]]:
    items: List[Tuple[str, str, Any]] = []
    for module_name, mapping in tools_registry.items():
        if not module_name.startswith(_DISCOVERY_PACKAGES):
            continue
        for public_name, fn in mapping.items():
            if not public_name.startswith(_ALLOWED_TOOL_PREFIXES):
                continue
            items.append((public_name, module_name, fn))
    return items


def _doc_summary(doc: str | None) -> str:
    if not doc:
        return ""
    return doc.strip().splitlines()[0][:200]


def _fallback_summary(name: str) -> str:
    words = " ".join(part for part in str(name or "").strip().split("_") if part)
    return f"{words[:1].upper()}{words[1:]}." if words else ""


def _signature_args(fn: Any, *, compact: bool) -> list[Any]:
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return []
    if compact:
        return [
            f"{parameter.name}{'' if parameter.default is inspect._empty else '?'}"
            for parameter in parameters
        ]
    args: list[dict[str, Any]] = []
    for parameter in parameters:
        entry: Dict[str, Any] = {
            "name": parameter.name,
            "annotation": str(parameter.annotation),
        }
        if parameter.default is not inspect._empty:
            entry["default"] = parameter.default
        args.append(entry)
    return args


def _public_facade_symbols(level: str) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    for module_name in _PUBLIC_FACADE_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:  # pragma: no cover - optional SDK modules may be unavailable
            continue
        for name in list(getattr(module, "__all__", ()) or ()):
            value = getattr(module, str(name), None)
            if not inspect.isfunction(value):
                continue
            public_name = f"{module_name}.{name}"
            summary = _doc_summary(value.__doc__) or _fallback_summary(str(name))
            item: dict[str, Any] = {
                "kind": "sdk_function",
                "name": public_name,
                "module": module_name,
                "qualname": f"{value.__module__}.{value.__name__}",
                "summary": summary,
                "meta": {
                    "stability": "experimental",
                    "side_effects": "public_sdk_contract",
                },
            }
            if level in {"std", "rich"}:
                try:
                    signature = inspect.signature(value)
                    item["signature_detail"] = {
                        "args": _signature_args(value, compact=False),
                        "returns": {"annotation": str(signature.return_annotation)},
                    }
                except (TypeError, ValueError):
                    pass
            symbols.append(item)
    return symbols


def _query_terms(query: str | None) -> list[str]:
    raw = [
        token.lower()
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9_]+", str(query or ""))
        if len(token) >= 3
    ]
    terms: list[str] = []
    for token in raw:
        candidates = [token]
        for prefix, expansions in _QUERY_TERM_EXPANSIONS:
            if token.startswith(prefix):
                candidates.extend(expansions)
        for candidate in candidates:
            if candidate not in _GENERIC_QUERY_TERMS and candidate not in terms:
                terms.append(candidate)
    return terms[:24]


def _selection_score(item: dict[str, Any], terms: list[str]) -> int:
    name = str(item.get("name") or "").lower()
    summary = str(item.get("summary") or "").lower()
    name_tokens = set(re.findall(r"[a-z0-9]+", name.replace("_", " ")))
    score = 0
    for term in terms:
        if term in name_tokens:
            score += 8
        elif term in name:
            score += 5
        if term in summary:
            score += 2
    return score


def _facade_module_cards(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in symbols:
        module = str(item.get("module") or "").strip()
        if module:
            counts[module] = counts.get(module, 0) + 1
    return [
        {
            "k": "sdk_module",
            "n": module,
            "s": _PUBLIC_FACADE_SUMMARIES.get(module, "Public AdaOS SDK facade."),
            "count": counts.get(module, 0),
        }
        for module in _PUBLIC_FACADE_MODULES
        if counts.get(module, 0)
    ]


def export(
    level: str = "std",
    *,
    query: str | None = None,
    limit: int = 24,
) -> Dict[str, Any]:
    """Return metadata about all exported tools and events."""

    _preload_modules()

    seen_names: set[str] = set()
    tools: List[Dict[str, Any]] = []
    for public_name, module_name, fn in sorted(_filter_tools(), key=lambda it: it[0]):
        if public_name in seen_names:
            raise RuntimeError(f"duplicate tool name detected: {public_name}")
        seen_names.add(public_name)
        qn = f"{fn.__module__}.{fn.__name__}"
        meta = tools_meta.get(qn, {})
        item: Dict[str, Any] = {
            "kind": "tool",
            "name": public_name,
            "module": module_name,
            "qualname": qn,
            "summary": meta.get("summary") or _doc_summary(fn.__doc__),
            "meta": {
                "stability": meta.get("stability", "experimental"),
                "idempotent": meta.get("idempotent"),
                "side_effects": meta.get("side_effects"),
                "approval_scope": meta.get("approval_scope"),
                "since": meta.get("since"),
                "version": meta.get("version"),
            },
            "examples": meta.get("examples", []),
        }
        if level in ("std", "rich"):
            sig = inspect.signature(fn)
            args = []
            for name, param in sig.parameters.items():
                entry: Dict[str, Any] = {"name": name, "annotation": str(param.annotation)}
                if param.default is not inspect._empty:
                    entry["default"] = param.default
                args.append(entry)
            returns: Dict[str, Any] = {"annotation": str(sig.return_annotation)}
            item["signature_detail"] = {"args": args, "returns": returns}
        if meta.get("input_schema"):
            item["input_schema"] = meta["input_schema"]
        if meta.get("output_schema"):
            item["output_schema"] = meta["output_schema"]
        topics = sorted(emits_map.get(qn, set()))
        if topics:
            item["emits"] = topics
        tools.append(item)

    facade_symbols = _public_facade_symbols(level)
    terms = _query_terms(query)
    bounded_limit = max(1, min(int(limit or 24), 64))
    if terms:
        candidates = [*tools, *facade_symbols]
        ranked = sorted(
            (
                (_selection_score(item, terms), item)
                for item in candidates
            ),
            key=lambda row: (-row[0], str(row[1].get("name") or "")),
        )
        # A single weak summary hit (for example, "typed") is not enough to
        # spend task context on an unrelated SDK function. Exact/name hits or
        # at least two corroborating summary terms remain discoverable.
        tools = [item for score, item in ranked if score >= 4][:bounded_limit]

    events = [
        {
            "kind": "event",
            "topic": topic,
            "payload": {"schema": schema},
        }
        for topic, schema in sorted(event_payloads.items())
    ]

    meta = {
        "generated_at": _iso_now(),
        "git_sha": _git_sha(),
        "py": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    if terms:
        meta["selection"] = {
            "query_digest": "sha256:"
            + hashlib.sha256(str(query or "").encode("utf-8")).hexdigest(),
            "terms": terms,
            "limit": bounded_limit,
            "matched": len(tools),
        }

    if level == "mini":
        items = []
        for tool in tools:
            items.append(
                {
                    "k": "tool",
                    "n": tool["name"],
                    "s": (tool.get("summary") or "")[:140],
                    "st": tool["meta"].get("stability"),
                    **(
                        {
                            "m": tool.get("module"),
                            "a": _signature_args(
                                getattr(
                                    importlib.import_module(str(tool.get("module"))),
                                    str(tool.get("name") or "").rsplit(".", 1)[-1],
                                    None,
                                ),
                                compact=True,
                            ),
                        }
                        if tool.get("kind") == "sdk_function"
                        else {}
                    ),
                }
            )
        if not terms:
            items.extend(_facade_module_cards(facade_symbols))
        for event in events:
            items.append({"k": "event", "topic": event["topic"]})
        return {"meta": meta, "items": items}

    return {
        "meta": meta,
        "tools": tools,
        "events": events,
        **({"facades": _facade_module_cards(facade_symbols)} if not terms else {}),
    }


__all__ = ["export"]
