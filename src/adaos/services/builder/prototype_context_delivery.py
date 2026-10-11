"""Opt-in attention budgeting for Prototype guidance, never its accepted contract.

This advisory projection does not depend on retrieval tools. Exact task facts,
schema and compiler invariants therefore stay inline. The separate indexed MCP
experiment can retrieve version-pinned platform guidance. Only explicitly
classified advisory hints can compete for the attention budget. The full default
is byte-preserving; this experimental projector does not activate itself.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.builder_context import (
    ContextSection, canonical, compile_sections, digest, estimate_tokens,
)

POLICY = "adaos.builder.prototype_context_delivery.v1"
_HINTS = {
    "collection_presentations": ("table", "cards", "board", "tree", "chart", "таблиц", "карточ", "доска"),
    "sections": ("section", "tabs", "partition", "заявк", "раздел", "вкладк"),
    "query_toolbar": ("search", "filter", "поиск", "фильтр"),
    "text": ("truncate", "wrap", "readable", "читаем", "перенос"),
    "query_scope": ("subset", "only", "deficit", "below", "дефицит", "ниже", "только"),
    "editor_inputs": ("create", "edit", "update", "созда", "редакт", "заявк"),
    "layout": ("workspace", "pane", "width", "responsive", "интерфейс", "адаптив", "панел"),
    "editor_surface": ("modal", "sheet", "inline", "editor", "редактор", "форм", "заявк"),
    "progressive_disclosure": ("details", "focused", "history", "сведения", "истори", "позици"),
}
_MEDIA = re.compile(r"image|video|audio|media|attachment|upload|file|изображ|видео|аудио|медиа|вложени|загруз|файл", re.I)


def compile_prototype_messages(
    messages: Sequence[Mapping[str, Any]], *, strategy: str = "full",
    generation_options: Mapping[str, Any] | None = None,
    hint_budget_tokens: int = 512, capacity_tokens: int = 64_000,
    reserve_tokens: int = 8_192,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project an actual assembled request before journaling and submission.

    Optional budgets are additive to the entire must-keep input, including schema
    and repair payloads. Receipts are out-of-band, not extra prompt instructions.
    Unknown guidance entries remain mandatory. Non-semantic/multimodal inputs
    return intact rather than being summarized or guessed.
    """
    if strategy not in {"full", "adaptive"}:
        raise ValueError("Prototype context strategy must be full or adaptive")
    if int(hint_budget_tokens) < 0:
        raise ValueError("Prototype hint budget cannot be negative")
    result = copy.deepcopy([dict(message) for message in messages])
    receipt: dict[str, Any] = {
        "schema": POLICY, "strategy": strategy, "input_sha256": digest(result),
        "hint_budget_tokens": int(hint_budget_tokens), "omitted": [],
        "authority_policy": "task_and_provider_contract_unchanged; advisory_guidance_only",
        "retrieval": "none; omitted hints are not advertised as available tools",
    }
    parsed: list[tuple[int, dict[str, Any]]] = []
    for index, message in enumerate(result):
        if not isinstance(message.get("content"), str):
            receipt.update(status="full_fallback", reason="non_text_input", output_sha256=digest(result))
            return result, receipt
        try:
            content = json.loads(message["content"])
        except (ValueError, TypeError):
            continue
        if isinstance(content, dict):
            parsed.append((index, content))
    stable = next(((i, data) for i, data in parsed if isinstance(data.get("stable_builder_context"), dict)
        and isinstance(data["stable_builder_context"].get("semantic_invariants"), dict)), None)
    if stable is None:
        receipt.update(status="full_fallback", reason="semantic_guidance_absent", output_sha256=digest(result))
        return result, receipt
    index, body = stable
    invariants = body["stable_builder_context"]["semantic_invariants"]
    if invariants.get("contract") != "adaos.builder.semantic_prototype_candidate.v2":
        receipt.update(status="full_fallback", reason="unknown_contract", output_sha256=digest(result))
        return result, receipt
    # Never route by instructional hint prose itself. Use the actual task and
    # baseline, including original_request nested in a repair message.
    tasks = []
    def task_facts(data):
        if not isinstance(data, dict):
            return
        if isinstance(data.get("original_request"), dict):
            task_facts(data["original_request"])
        request = data.get("builder_request", data)
        if not isinstance(request, dict) or "instruction" not in request:
            return
        brief = request.get("prototype_brief") or {}
        tasks.append({"instruction": request.get("instruction"),
            "required_references": brief.get("required_references"), "facts": brief.get("facts"),
            "interface_context": brief.get("interface_context"), "current_semantic": request.get("current_semantic")})
    for i, data in parsed:
        if i != index:
            task_facts(data)
    if not tasks:
        receipt.update(status="full_fallback", reason="task_facts_absent", output_sha256=digest(result))
        # The mutable working copy has not yet changed the submitted string.
        return result, receipt
    # Legacy UX paragraphs also explain accepted region/content constraints.
    # Do not classify them as optional when an explicit interface contract is
    # present; that requires a separately qualified per-dimension projection.
    if strategy == "adaptive" and any(task.get("interface_context") for task in tasks):
        receipt.update(status="full_fallback", reason="accepted_interface_contract",
            output_sha256=digest(result), must_keep_complete=True)
        return result, receipt
    task_text = canonical(tasks).casefold()
    optional: dict[str, Any] = {}
    ux = invariants.get("ux_recommendations")
    if isinstance(ux, dict):
        for key in list(ux):
            if key in _HINTS:
                optional["ux_recommendations." + key] = ux.pop(key)
        if not ux:
            invariants.pop("ux_recommendations", None)
    if "media" in invariants and not _MEDIA.search(task_text):
        optional["media"] = invariants.pop("media")
    if "revision_scope" in invariants and not any(task.get("current_semantic") is not None for task in tasks):
        optional["revision_scope"] = invariants.pop("revision_scope")
    core = copy.deepcopy(result)
    core[index]["content"] = canonical(body)
    options = dict(generation_options or {})
    # Non-provider diagnostic options are harmless overestimates, not discounts.
    overhead = canonical(options)
    core_tokens = estimate_tokens(canonical(core) + overhead)
    sections = [ContextSection("prototype_input", canonical(core), required=True)]
    for key, value in optional.items():
        terms = _HINTS.get(key.removeprefix("ux_recommendations."), ())
        matches = [term for term in terms if term in task_text]
        sections.append(ContextSection(key, canonical({key: value}), required=False,
            layer="prototype_advisory", utility=1 + 4 * len(matches),
            source_refs=(f"semantic_generation_guidance:{key}",)))
    _, budget = compile_sections(sections, strategy=strategy,
        capacity_tokens=capacity_tokens, reserve_tokens=reserve_tokens,
        target_tokens=core_tokens + int(hint_budget_tokens),
        overhead_text=overhead, provider_overhead_tokens=256)
    selected = {item["key"] for item in budget["selected"]}
    if strategy == "full":
        # A control is not a reserialized/reordered version of the input.
        result = copy.deepcopy([dict(message) for message in messages])
    else:
        for key, value in optional.items():
            if key not in selected:
                continue
            if key.startswith("ux_recommendations."):
                invariants.setdefault("ux_recommendations", {})[key.split(".", 1)[1]] = value
            else:
                invariants[key] = value
        result[index]["content"] = canonical(body)
    # Recheck the actual JSON messages, not just section estimates. No truncation.
    final_tokens = estimate_tokens(canonical(result) + overhead) + 256
    if final_tokens > capacity_tokens - reserve_tokens:
        from adaos.services.builder_context import ContextBudgetExceeded
        budget.update(estimated_tokens=final_tokens, status="blocked")
        raise ContextBudgetExceeded(budget)
    receipt.update(status="ready", output_sha256=digest(result),
        estimated_tokens=final_tokens, full_estimated_tokens=estimate_tokens(canonical(messages) + overhead) + 256,
        selected_hints=sorted(set(optional) & selected),
        omitted=[item for item in budget["omitted"] if item["reason"] != "duplicate"],
        budget=budget, core_sha256=digest(core), must_keep_complete=True)
    return result, receipt


__all__ = ["compile_prototype_messages"]
