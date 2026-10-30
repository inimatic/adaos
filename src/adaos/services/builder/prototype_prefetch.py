"""Exact Core-prefetched task guidance over the same immutable MCP library."""
import copy
import json

from adaos.services.builder_context import canonical, digest
from .prototype_mcp import library_snapshot
from .prototype_task_context import select_task_fragments, task_evidence


def prefetch_guidance(messages, *, receipt):
    result = copy.deepcopy(messages)
    evidence = task_evidence(messages)
    if evidence is None:
        return result, {**receipt, "retrieval": "core_prefetch", "prefetch_status": "task_evidence_absent"}
    selected, reasons = select_task_fragments(evidence)
    snapshot = library_snapshot()
    fragments = {row["id"]: row for row in snapshot["fragments"]}
    resolved = []
    for message in result:
        try:
            body = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        stable = body.get("stable_builder_context") if isinstance(body, dict) else None
        if not isinstance(stable, dict) or not isinstance(stable.get("semantic_invariants"), dict):
            continue
        invariants = stable["semantic_invariants"]
        if invariants.get("contract") != snapshot["contract"]:
            raise ValueError("Core-prefetch cannot retarget an unknown semantic contract")
        mandatory = {identity for identity in fragments if not identity.startswith('ux.')
                     and identity not in {'media', 'revision_scope'}}
        for identity in sorted(mandatory | set(selected)):
            fragment = fragments[identity]
            if digest(fragment["content"]) != fragment["content_digest"]:
                raise ValueError("Core-prefetched guidance digest mismatch")
            if identity.startswith("ux."):
                invariants.setdefault("ux_recommendations", {})[identity[3:]] = copy.deepcopy(fragment["content"])
            else:
                invariants[identity] = copy.deepcopy(fragment["content"])
            resolved.append({"id": identity, "content_digest": fragment["content_digest"],
                             "source": snapshot["authority"]})
        # Everything retained by the adaptive mandatory kernel remains present.
        # No locator is advertised as a substitute for missing inline content.
        stable.pop("prototype_retrieval", None)
        message["content"] = canonical(body)
    from adaos.services.builder_context import estimate_tokens, ContextBudgetExceeded
    estimated = estimate_tokens(canonical(result)) + 256
    limit = receipt.get('budget', {}).get('prompt_limit_tokens')
    if limit and estimated > limit:
        raise ContextBudgetExceeded({**receipt['budget'], 'estimated_tokens': estimated, 'status': 'blocked'})
    return result, {**receipt, "retrieval": "core_prefetch", "prefetch_status": "resolved",
                    "library_digest": snapshot["library_digest"], "prefetched": resolved,
                    "selection_reasons": reasons, "estimated_tokens": estimated, "output_sha256": digest(result)}
