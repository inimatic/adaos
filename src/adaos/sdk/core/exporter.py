"""Export tool metadata for discovery by LLMs and other clients."""

from __future__ import annotations

import importlib
import hashlib
import inspect
import json
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
    "adaos.sdk.access",
    "adaos.sdk.applications",
    "adaos.sdk.automation",
    "adaos.sdk.builder.applications",
    "adaos.sdk.control_plane",
    "adaos.sdk.conversation",
    "adaos.sdk.context",
    "adaos.sdk.data.blob",
    "adaos.sdk.data.skill_env",
    "adaos.sdk.data.configuration",
    "adaos.sdk.data.lifecycle",
    "adaos.sdk.data.secrets",
    "adaos.sdk.deployment",
    "adaos.sdk.distributed",
    "adaos.sdk.execution",
    "adaos.sdk.llm.content",
    "adaos.sdk.llm.images",
    "adaos.sdk.llm.media",
    "adaos.sdk.developer.documents",
    "adaos.sdk.research",
    "adaos.sdk.resources",
    "adaos.sdk.status",
    "adaos.sdk.subscriptions",
    "adaos.sdk.system",
    "adaos.sdk.web",
    "adaos.sdk.workflow",
)
_PUBLIC_FACADE_SUMMARIES: dict[str, str] = {
    "adaos.sdk.access": "Read verified caller and invocation identity and require caller capabilities in the current skill scope.",
    "adaos.sdk.applications": "Inspect Applications and execute reviewed install, update, removal, and track operations.",
    "adaos.sdk.automation": "Read the secret-free remote automation fleet inventory through the Core-owned Builder identity and the selected AdaOS Root route. Applications never receive bearer tokens, certificate material, SSH keys, MCP tickets, or arbitrary transport URLs. Requires external_provider.use.",
    "adaos.sdk.builder.applications": "Create, preview, publish, and promote Applications through the governed Builder lifecycle.",
    "adaos.sdk.control_plane": "Read canonical node, subnet, reliability, quota, and inventory projections.",
    "adaos.sdk.conversation": "Read and update governed conversational threads and Builder topics.",
    "adaos.sdk.context": "Resolve, compile, inspect, and bind governed agent context.",
    "adaos.sdk.data.blob": "Store immutable skill-owned bytes admitted by the authenticated binary tool ingress. Production attachments use a one-use upload context, storage.blob, and separate permissioned upload/read tools; bytes never enter JSON tool arguments.",
    "adaos.sdk.data.skill_env": "Resolve owner-scoped persistent skill data paths and environment state.",
    "adaos.sdk.data.configuration": "Read/write declared typed settings with revision checks. Production binds the selected owned Application skill; DEV uses isolated synthetic defaults/overrides. No secret resolution; Beta configuration must be prepared by lifecycle first.",
    "adaos.sdk.data.lifecycle": "Initialize or verify a skill.yaml-declared SQLite database through the same Core migration ledger as Beta. Requires storage.relational. Never upgrade existing installed data outside the fenced lifecycle; DEV synthetic stores are isolated.",
    "adaos.sdk.data.secrets": "Resolve configuration.credentials slots through the node vault with secrets.read/write and verified local-owner admission. Declare each slot's purpose, never values. Beta inherits/adopts bindings; DEV is isolated. Undeclared legacy skills use their existing private file adapter and are not migration-qualified.",
    "adaos.sdk.deployment": "Plan and inspect project deployment through the public SDK boundary.",
    "adaos.sdk.distributed": "Describe and operate governed distributed datasets and services.",
    "adaos.sdk.execution": "Declare and inspect bounded execution jobs and artifacts.",
    "adaos.sdk.llm.content": "Generate and poll durable typed form drafts through Root subscriptions; review before explicit application.",
    "adaos.sdk.llm.images": "Generate and poll owner-scoped still-image drafts through durable Root jobs with an explicit image model. List retained draft metadata by explicit local context without provider calls. Requires verified caller workspace.read/write; never apply to assets automatically. Model availability, admission and actual modality usage come from Root, not a text-model fallback.",
    "adaos.sdk.llm.media": "Provide explicit bounded image inputs to vision-capable content requests; does not generate images.",
    "adaos.sdk.developer.documents": "Read and save DEV text documents with optimistic concurrency; never edit sealed Trials.",
    "adaos.sdk.research": "Use governed research inquiry, synthesis, and evidence workflows.",
    "adaos.sdk.resources": "Query and mutate current-skill Resource Workbench records through declared operations.",
    "adaos.sdk.status": "Publish bounded skill and scenario status projections.",
    "adaos.sdk.subscriptions": "Read bounded subscription usage and quota projections.",
    "adaos.sdk.system": "Read bounded system operations projections and rename the local subnet or current node through stable public identities.",
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
    ("подпис", ("subscription", "quota")),
    ("лимит", ("limit", "quota", "remaining")),
    ("usage", ("usage", "quota", "metering")),
    ("subscription", ("subscription", "quota")),
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


def _schema_digest(value: Any) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sdk_contract(public_name: str, fn: Any, meta: dict[str, Any]) -> dict[str, Any]:
    try:
        parameter_names = list(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        parameter_names = []
    input_schema = meta.get("input_schema") if isinstance(meta.get("input_schema"), dict) else {}
    properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
    names = list(dict.fromkeys([*parameter_names, *[str(name) for name in properties]]))
    bound_args = [name for name in ("limit", "top_k", "page_size") if name in names]
    cursor_args = [name for name in ("cursor", "page_token", "pagination_token", "offset") if name in names]
    operation = str(public_name or "").rsplit(".", 1)[-1].lower()
    supplied_boundedness = meta.get("boundedness") if isinstance(meta.get("boundedness"), dict) else {}
    boundedness = dict(supplied_boundedness)
    if not boundedness:
        boundedness = {
            "kind": (
                "bounded_page"
                if bound_args
                else "unbounded"
                if operation.startswith(("list", "search", "query"))
                else "single_result"
            ),
            "arguments": bound_args,
        }
    pagination = dict(meta.get("pagination") or {}) if isinstance(meta.get("pagination"), dict) else {}
    if not pagination:
        pagination = {"supported": bool(cursor_args), "arguments": cursor_args}
    deprecated = bool(meta.get("deprecated")) or str(meta.get("stability") or "").lower() == "deprecated"
    contract = {
        "permissions": sorted({str(value).strip() for value in meta.get("permissions") or [] if str(value).strip()}),
        "effects": sorted({str(value).strip() for value in meta.get("effects") or ([meta.get("side_effects")] if meta.get("side_effects") else []) if str(value).strip()}),
        "errors": sorted({str(value).strip() for value in meta.get("errors") or [] if str(value).strip()}),
        "boundedness": boundedness,
        "pagination": pagination,
        "stability": str(meta.get("stability") or "experimental"),
        "since": meta.get("since"),
        "deprecated": deprecated,
        "removedIn": meta.get("removed_in"),
        "replacement": meta.get("replacement"),
        "migration_recipe": meta.get("migration_recipe"),
        "authoring_visibility": "migration_only" if deprecated else "default",
        "schema_refs": {
            "input": _schema_digest(input_schema),
            "output": _schema_digest(meta.get("output_schema")),
        },
    }
    contract["digest"] = "sha256:" + hashlib.sha256(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return contract


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
            item["contract"] = _sdk_contract(public_name, value, item["meta"])
            if level in {"std", "rich"}:
                item["description"] = inspect.getdoc(value) or summary
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
        if term == name or term == name.rsplit(".", 1)[-1]:
            score += 40
        elif term in name_tokens:
            score += 8
        elif term in name:
            score += 5
        if term in summary:
            score += 2
    return score


def _usage_frequency(item: dict[str, Any]) -> int:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    try:
        return max(
            0,
            int(
                meta.get("usage_frequency")
                or item.get("usage_frequency")
                or item.get("invocation_count")
                or 0
            ),
        )
    except (TypeError, ValueError):
        return 0


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
    include_deprecated: bool = False,
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
        item["contract"] = _sdk_contract(public_name, fn, meta)
        if item["contract"]["deprecated"] and not include_deprecated:
            continue
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

    facade_symbols = [
        item
        for item in _public_facade_symbols(level)
        if include_deprecated or not bool((item.get("contract") or {}).get("deprecated"))
    ]
    terms = _query_terms(query)
    bounded_limit = max(1, min(int(limit or 24), 64))
    if terms:
        candidates = [*tools, *facade_symbols]
        ranked = sorted(
            (
                (_selection_score(item, terms), _usage_frequency(item), item)
                for item in candidates
            ),
            key=lambda row: (-row[0], -row[1], str(row[2].get("name") or "")),
        )
        # A single weak summary hit (for example, "typed") is not enough to
        # spend task context on an unrelated SDK function. Exact/name hits or
        # at least two corroborating summary terms remain discoverable.
        tools = [item for score, _frequency, item in ranked if score >= 4][:bounded_limit]
    else:
        tools.sort(key=lambda item: (-_usage_frequency(item), str(item.get("name") or "")))

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
            contract = dict(tool.get("contract") or {})
            items.append(
                {
                    "k": tool.get("kind") or "tool",
                    "n": tool["name"],
                    "s": (tool.get("summary") or "")[:140],
                    "st": tool["meta"].get("stability"),
                    "contract": {
                        key: value
                        for key, value in contract.items()
                        if value not in (None, "", [], {}, False)
                        or key in {"deprecated", "pagination"}
                    },
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


def _export_items(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = value.get("tools") if isinstance(value.get("tools"), list) else value.get("items")
    return {
        str(item.get("name") or item.get("n") or "").strip(): dict(item)
        for item in rows or []
        if isinstance(item, dict) and str(item.get("name") or item.get("n") or "").strip()
    }


def compatibility_report(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Compare two generated SDK metadata releases using stable contracts.

    Removal, newly required inputs, permission expansion, and loss of bounded
    pagination are breaking. Additions and deprecations are reported
    separately so release CI can gate them without feeding both full exports to
    Builder.
    """

    before = _export_items(previous)
    after = _export_items(current)
    breaking: list[dict[str, Any]] = []
    additive: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for name in sorted(set(before) - set(after)):
        breaking.append({"symbol": name, "kind": "removed"})
    for name in sorted(set(after) - set(before)):
        additive.append({"symbol": name, "kind": "added"})
    for name in sorted(set(before) & set(after)):
        old = before[name]
        new = after[name]
        old_contract = dict(old.get("contract") or {})
        new_contract = dict(new.get("contract") or {})
        old_schema = old.get("input_schema") if isinstance(old.get("input_schema"), dict) else {}
        new_schema = new.get("input_schema") if isinstance(new.get("input_schema"), dict) else {}
        old_required = {str(item) for item in old_schema.get("required") or []}
        new_required = {str(item) for item in new_schema.get("required") or []}
        required_added = sorted(new_required - old_required)
        if required_added:
            breaking.append(
                {"symbol": name, "kind": "required_inputs_added", "fields": required_added}
            )
        old_permissions = {str(item) for item in old_contract.get("permissions") or []}
        new_permissions = {str(item) for item in new_contract.get("permissions") or []}
        permissions_added = sorted(new_permissions - old_permissions)
        if old_contract and permissions_added:
            breaking.append(
                {"symbol": name, "kind": "permissions_expanded", "permissions": permissions_added}
            )
        old_bounded = dict(old_contract.get("boundedness") or {})
        new_bounded = dict(new_contract.get("boundedness") or {})
        old_page = dict(old_contract.get("pagination") or {})
        new_page = dict(new_contract.get("pagination") or {})
        if old_contract and (
            new_bounded.get("kind") == "unbounded"
            or (bool(old_page.get("supported")) and not bool(new_page.get("supported")))
        ):
            breaking.append({"symbol": name, "kind": "boundedness_regressed"})
        if not bool(old_contract.get("deprecated")) and bool(new_contract.get("deprecated")):
            changes.append(
                {
                    "symbol": name,
                    "kind": "deprecated",
                    "replacement": new_contract.get("replacement"),
                    "removedIn": new_contract.get("removedIn"),
                }
            )
        old_digest = str(old_contract.get("digest") or "")
        new_digest = str(new_contract.get("digest") or "")
        if old_digest and new_digest and old_digest != new_digest:
            changes.append({"symbol": name, "kind": "contract_changed"})
    report = {
        "schema": "adaos.sdk.compatibility_report.v1",
        "compatible": not breaking,
        "previous_digest": _schema_digest(previous),
        "current_digest": _schema_digest(current),
        "breaking": breaking,
        "additive": additive,
        "changes": changes,
    }
    report["digest"] = "sha256:" + hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return report


__all__ = ["compatibility_report", "export"]
