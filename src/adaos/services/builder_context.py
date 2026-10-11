"""Deterministic, budgeted context selection over immutable Builder evidence.

Authority and task requirements are must-keep. Optional platform contracts are
selected by utility/cost or exposed by digest-bound references. Provider usage
is measured separately; these UTF-8 estimates never masquerade as billed tokens.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


STRATEGIES = ("full", "core", "accents", "adaptive")
POLICY_VERSION = "adaos.builder.adaptive_context.v1"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    raw = value if isinstance(value, str) else canonical(value)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 4)


def context_strategy(value: Any = None) -> str:
    # Rollout is explicit until matched live evaluations qualify a new default.
    strategy = str(value or "full").strip()
    if strategy not in STRATEGIES:
        raise ValueError(f"unsupported Builder context strategy: {strategy}")
    return strategy


@dataclass(frozen=True)
class ContextSection:
    key: str
    text: str
    required: bool = True
    layer: str = "authority"
    utility: float = 1.0
    source_refs: tuple[str, ...] = field(default_factory=tuple)


class ContextBudgetExceeded(ValueError):
    def __init__(self, receipt: Mapping[str, Any]):
        self.receipt = dict(receipt)
        super().__init__(
            "Builder final prompt exceeds its context budget: "
            f"required={receipt['required_tokens']}, used={receipt['estimated_tokens']}, "
            f"limit={receipt['prompt_limit_tokens']}; retain exact requirements and "
            "start a bounded continuation or increase the declared context capacity"
        )


def compile_sections(
    sections: Sequence[ContextSection],
    *,
    capacity_tokens: int = 32_000,
    reserve_tokens: int = 8_192,
    target_tokens: int = 8_000,
    strategy: str = "adaptive",
    overhead_text: str = "",
    provider_overhead_tokens: int = 0,
) -> tuple[str, dict[str, Any]]:
    """Budget the complete model input, retaining all required sections exactly."""
    strategy = context_strategy(strategy)
    capacity = int(capacity_tokens)
    reserve = max(0, int(reserve_tokens))
    if capacity <= reserve:
        raise ValueError("context capacity must exceed tool/completion reserve")
    limit = capacity - reserve
    provider_overhead = max(0, int(provider_overhead_tokens))
    target = min(max(1, limit - provider_overhead), max(1, int(target_tokens)))
    unique: list[ContextSection] = []
    seen: dict[str, int] = {}
    omitted: list[dict[str, Any]] = []
    keys: set[str] = set()
    for section in sections:
        if not section.text.strip():
            continue
        if section.key in keys:
            raise ValueError(f"duplicate context section key: {section.key}")
        keys.add(section.key)
        identity = digest(section.text)
        if identity in seen:
            index = seen[identity]
            prior = unique[index]
            unique[index] = ContextSection(
                prior.key, prior.text, prior.required or section.required, prior.layer,
                max(prior.utility, section.utility),
                tuple(dict.fromkeys((*prior.source_refs, *section.source_refs))),
            )
            omitted.append({"key": section.key, "reason": "duplicate", "sha256": identity})
        else:
            seen[identity] = len(unique)
            unique.append(section)
    chosen = {item.key for item in unique if item.required}
    # Separators and provider configuration (schema/tools) participate in cost.
    def render(keys: set[str]) -> str:
        return "\n\n".join(item.text.rstrip() for item in unique if item.key in keys) + "\n"

    required_tokens = estimate_tokens(render(chosen) + overhead_text) + provider_overhead
    optional = sorted(
        (item for item in unique if not item.required),
        key=lambda item: (-item.utility / max(1, estimate_tokens(item.text)), item.key),
    )
    for item in optional:
        if estimate_tokens(render(chosen | {item.key}) + overhead_text) + provider_overhead <= (
            limit if strategy == "full" else max(target + provider_overhead, required_tokens)
        ):
            chosen.add(item.key)
        else:
            omitted.append({"key": item.key, "reason": "attention_budget", "sha256": digest(item.text),
                            "source_refs": list(item.source_refs)})
    prompt = render(chosen)
    total = estimate_tokens(prompt + overhead_text) + provider_overhead
    receipt = {
        "schema": POLICY_VERSION, "strategy": strategy,
        "capacity_tokens": capacity, "reserve_tokens": reserve,
        "prompt_limit_tokens": limit, "target_tokens": target,
        "required_tokens": required_tokens, "estimated_tokens": total,
        "configuration_tokens": estimate_tokens(overhead_text),
        "attention_tokens": total - provider_overhead,
        "provider_overhead_tokens": provider_overhead,
        "provider_overhead_authority": "conservative_estimate_not_provider_measurement",
        "estimation": "utf8_bytes_div_4_ceil",
        "status": "blocked" if total > limit else "target_exceeded" if total - provider_overhead > target else "ready",
        "prompt_sha256": digest(prompt), "omitted": omitted,
        "selected": [{"key": item.key, "layer": item.layer, "required": item.required,
                      "estimated_tokens": estimate_tokens(item.text), "sha256": digest(item.text),
                      "source_refs": list(item.source_refs)} for item in unique if item.key in chosen],
        "must_keep_complete": all(item.key in chosen for item in unique if item.required),
    }
    if total > limit:
        raise ContextBudgetExceeded(receipt)
    return prompt, receipt


def automation_core_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep exact task/authority facts; contract bodies stay available by reference."""
    result = copy.deepcopy(dict(value))
    facets = result.get("facets") or {}
    for key, facet in list(facets.items()):
        if not isinstance(facet, Mapping) or key not in {"ui_capabilities", "abi", "sdk", "api", "tool_contracts"}:
            continue
        facets[key] = {
            name: copy.deepcopy(item) for name, item in facet.items()
            if name in {"status", "schema", "valid", "ready", "missing", "ambiguous", "diagnostics",
                        "definition_ref", "definition_digest", "binding_digest", "compiler_view",
                        "catalog_ref", "catalog_version", "catalog_digest", "root_item_ids",
                        "selected_mode", "selected_profile_id", "execution_mode"}
        }
    # Artifact identities and acceptance objects are authority, not summaries.
    # Reviews are untrusted history. Exact active acceptance constraints remain.
    change = result.get("change") or {}
    if change.get("reviews"):
        change["review_refs"] = [
            {key: item[key] for key in ("review_id", "ref", "digest", "decision", "status") if key in item}
            for item in change.pop("reviews") if isinstance(item, Mapping)
        ]
    return result


_CONTRACT_SECTIONS = {
    "Prefetched descriptor working set",
    "Commit-bound public AdaOS SDK contracts", "Exact Automation binding contract",
}


def compile_automation_prompt(
    prompt: str,
    *,
    projection: Mapping[str, Any],
    packet_ref: str,
    strategy: str = "adaptive",
    capacity_tokens: int = 32_000,
    reserve_tokens: int = 8_192,
    target_tokens: int = 8_000,
    overhead_text: str = "",
    provider_overhead_tokens: int = 0,
) -> tuple[str, dict[str, Any]]:
    """Compile the final worker prompt, including its result and input contracts."""
    strategy = context_strategy(strategy)
    blocks = re.split(r"(?m)(?=^## )", prompt)
    sections = []
    transformations = []
    for index, block in enumerate(blocks):
        title = block.splitlines()[0].removeprefix("## ").strip() if block.strip() else str(index)
        text = block
        if strategy != "full" and title == "Governed Change context" and projection:
            text = (
                "## Governed Change context\n\n"
                "Exact authority, requirements and active acceptance constraints follow. "
                f"Contract bodies and history are retained in {packet_ref}; "
                "read digest-bound compiler views only for the behavior being implemented.\n\n"
                f"```json\n{canonical(automation_core_projection(projection))}\n```"
            )
        if strategy != "full" and title in _CONTRACT_SECTIONS:
            if strategy == "adaptive":
                # Contract instructions already point to admitted files. Preserve
                # them, using compact JSON blocks to avoid formatting overhead.
                text = re.sub(
                    r"```json\s*([\s\S]*?)\s*```",
                    lambda m: "```json\n" + canonical(json.loads(m.group(1))) + "\n```",
                    text,
                )
            else:
                text = ""
        if strategy == "core" and title == "Selected AdaOS rule capsules":
            rule_blocks = re.split(r"(?m)(?=^### )", block)
            text = rule_blocks[0] + "\n" + "\n".join(
                item for item in rule_blocks[1:]
                if item.splitlines()[0].split()[1] in {
                    "adaos.builder.execution_boundary.v1", "adaos.skill.sdk_boundary.v1",
                    "adaos.scenario.composition_boundary.v1",
                }
            )
        if text != block:
            transformations.append({"key": title, "reason": "strategy_projection" if text else "strategy_excluded",
                                    "source_sha256": digest(block), "source_refs": [packet_ref]})
        layer = "platform_accents" if title == "Selected AdaOS rule capsules" else (
            "drilldown" if title in _CONTRACT_SECTIONS or title == "Read-only task inputs" else "authority"
        )
        sections.append(ContextSection(f"{index}:{title}", text, layer=layer, source_refs=(packet_ref,)))
    compiled, receipt = compile_sections(
        sections, capacity_tokens=capacity_tokens, reserve_tokens=reserve_tokens,
        target_tokens=target_tokens, strategy=strategy, overhead_text=overhead_text,
        provider_overhead_tokens=provider_overhead_tokens,
    )
    receipt["source_prompt_sha256"] = digest(prompt)
    receipt["transformations"] = transformations
    return compiled, receipt


def context_use_receipt(
    events: str,
    *,
    selected: Sequence[Mapping[str, Any]],
    final_message: str = "",
    outcome: Mapping[str, Any] | None = None,
    feedback: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Report observed retrieval separately from model-declared use and causality."""
    completed = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if event.get("type") == "item.completed" and isinstance(event.get("item"), Mapping):
            completed.append(event["item"])
    opened = []
    commands = [item for item in completed if item.get("type") in {"command_execution", "mcp_tool_call"}]
    for source in selected:
        path = str(source.get("path") or "")
        name = str(source.get("name") or source.get("key") or "")
        # Lexical command evidence is a retrieval attempt, not proof that the
        # whole file was read or that its contents affected the model's answer.
        for item in commands:
            command = str(item.get("command") or "")
            if name and (name in command or path and path in command):
                opened.append({"ref": path or name, "sha256": source.get("sha256"),
                               "event_id": item.get("id"), "status": item.get("status"),
                               "evidence_kind": "observed_retrieval_attempt"})
    declared = [str(item.get("path") or item.get("name") or item.get("key")) for item in selected
                if str(item.get("name") or item.get("key") or "")
                and str(item.get("name") or item.get("key")) in final_message]
    misses = []
    for item in feedback:
        category = str(item.get("category") or item.get("kind") or "")
        if category in {"ambiguous_contract", "missing_contract", "core_capability_request", "context_miss",
                        "insufficient_context", "conflicting_contract", "inefficient_contract", "missing_capability"}:
            misses.append({"kind": "reported_capability_gap" if category in {"core_capability_request", "missing_capability"}
                           else "contract_delivery_gap", "category": category,
                           "contract_refs": list(item.get("target_refs") or []) or
                           ([str(item["component_ref"])] if item.get("component_ref") else []),
                           "evidence_refs": copy.deepcopy(item.get("evidence_refs") or []),
                           "validation_status": "model_report_not_independent",
                           "observation_digest": digest(item), "blocking": bool(item.get("blocking", True))})
    return {
        "schema": "adaos.builder.context_use.v1",
        "selected": [copy.deepcopy(dict(item)) for item in selected],
        "selection_semantics": "references_admitted_in_prompt_not_full_contents",
        "opened": opened, "declared_cited_refs": declared,
        "causal_use": "not_established", "tool_boundary_count": len(commands),
        "context_misses": misses, "outcome": copy.deepcopy(dict(outcome or {})),
    }
