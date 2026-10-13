"""Pinned, public Prototype guidance over the existing native Root MCP plane.

No project content, credentials, runtime actions or unbounded document reads are
published. Opt-in generation uses a short-lived, two-tool read-only session.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Mapping

from adaos.services.builder_context import canonical, digest

SEARCH_TOOL = "builder.search_prototype_guidance"
READ_TOOL = "builder.read_prototype_guidance"
TOOLS = (SEARCH_TOOL, READ_TOOL)
OPENAI_TOOLS = tuple(name.replace(".", "_") for name in TOOLS)
SERVER_LABEL = "adaos_prototype"
CAPABILITY = "builder.prototype.read_guidance"
_LEASES: dict[str, dict[str, Any]] = {}

_READ_WHEN = {
    "relationships": "Link records across resources or author a relationship selector.",
    "selection_links": "A selected row affects another view, child collection or details.",
    "view_roles": "Choose collection, details or editor ownership and activation.",
    "coverage": "Bind an accepted Brief requirement to executable or visible evidence.",
    "query_filters": "Author search, user filters or collection-only query controls.",
    "state_rules": "Demonstrate empty, filtered or other requested UI states with reachable fixtures.",
    "state_proofs": "Choose an executable proof for a representative state.",
    "field_validation": "An editor must reject a missing or invalid unsaved input.",
    "command_ownership": "Expose a local command or choose editable editor inputs.",
    "command_guards": "Condition a mutation on fields or illustrate a deferred cross-record rule.",
    "deferred_computations": "Show a requested calculation before Automation implements it.",
    "interactions": "Use local CRUD, locks, selectors or command effects.",
    "media": "Render or upload images, audio, video or attachments.",
    "revision_scope": "Change an existing semantic application without losing unrelated behavior.",
    "layout_regions": "Name an explicit layout region or assign views to legacy regions.",
    "ux.query_scope": "A tab permanently shows a subset, independent of resettable user filters.",
    "ux.sections": "Partition visible content into screens or tabs without duplicating resources.",
    "ux.layout": "Fit multiple panes into actual wide/narrow content boxes.",
    "ux.editor_surface": "Make an editor reachable without keeping every form on screen.",
    "ux.editor_inputs": "Separate writable command inputs from read-only editor context.",
    "ux.progressive_disclosure": "Show contextual details/history on demand.",
    "ux.collection_presentations": "Choose a supported table/card/board/tree/chart or compact presentation.",
    "ux.query_toolbar": "Expose search/filter/reset through the platform toolbar.",
    "ux.text": "Control wrapping, truncation or alignment in a collection.",
    "form_guidance": "Provide localized examples, help text or non-submitted placeholders.",
}

_KEYWORDS = {
    "stage": "prototype automation approval local persistence notifications прототип согласование",
    "limits": "counts limits resources fields views commands ограничения",
    "authoring_constraints": "schema reference normalization operations roles constraints контракт",
    "relationships": "foreign key relation supplier request ownership связи поставщики заявки",
    "layout_regions": "layout regions main supporting responsive workbench панели",
    "coverage": "requirement refs gaps coverage requirements требования",
    "view_roles": "collection details editor activation роли редактор",
    "selection_links": "selected parent filter linked history состав история",
    "state_proofs": "empty waiting pending observable state proof пустой ожидание",
    "state_rules": "conditions transition guards state rules состояния переходы",
    "query_filters": "category search filter deficit numeric comparisons фильтр дефицит",
    "form_guidance": "edit create inputs dropdown numeric form форма",
    "interactions": "commands action mutation inspect cancel save history действия",
    "fixture_values": "seed realistic records currency units данные валюта единицы",
    "command_ownership": "create update transition collection editor commands команды",
    "media": "image audio video attachments изображения вложения",
    "ux.layout": "panes layout responsive workbench panels mobile адаптивный панели",
    "ux.editor_surface": "modal sheet inline editor record create update редактор форма",
    "ux.progressive_disclosure": "history details selected relations drill down история сведения",
    "ux.query_toolbar": "search category query filter toolbar поиск категория фильтр",
    "ux.collection_presentations": "table cards board tree chart таблица карточки доска",
}


def library_snapshot() -> dict[str, Any]:
    from adaos.sdk.builder.prototype import semantic_generation_guidance

    guidance = semantic_generation_guidance()
    fragments = []
    for key, value in sorted(guidance.items()):
        if key == "contract":
            continue
        entries = (("ux." + child, content) for child, content in sorted(value.items())) if key == "ux_recommendations" else ((key, value),)
        for fragment_id, content in entries:
            keywords = _KEYWORDS.get(fragment_id, fragment_id.replace("_", " ").replace(".", " "))
            fragments.append({"id": fragment_id, "title": fragment_id.replace("_", " "),
                "keywords": keywords, "content": copy.deepcopy(content), "content_digest": digest(content)})
    return {"schema": "adaos.builder.prototype_context_library.v1", "contract": guidance["contract"],
        "library_digest": digest(guidance), "authority": "adaos.sdk.builder.prototype.semantic_generation_guidance",
        "side_effects": "none", "fragments": fragments}


def enabled() -> bool:
    value = os.getenv("ADAOS_BUILDER_PROTOTYPE_MCP_ENABLED", "0").strip()
    if value not in {"0", "1"}:
        raise ValueError("Prototype MCP switch must be 0 or 1")
    return value == "1"


def retrieval_index(*, available: bool | None = None) -> dict[str, Any]:
    from adaos.services.ui_component_contracts import discovery_policy_text
    available = enabled() if available is None else available
    snapshot = library_snapshot()
    return {"schema": "adaos.builder.prototype_retrieval_index.v1", "available": available,
        "library_digest": snapshot["library_digest"], "authority": snapshot["authority"],
        "server_label": SERVER_LABEL if available else None,
        "tools": list(OPENAI_TOOLS) if available else [],
        "policy": discovery_policy_text(available=bool(available)),
        "dimensions": [{"id": part["id"], "keywords": part["keywords"],
            "read_when": _READ_WHEN.get(part["id"], "Exact platform authoring rule for " + part["id"] + ".")}
            for part in snapshot["fragments"]]}


def indexed_messages(messages, *, available: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Experimental retrieval ablation, distinct from advisory-only adaptive.

    User facts, required refs, provider schema, stage and hard limits remain
    inline. Remaining platform guidance is indexed, not silently summarized.
    The no-retrieval arm intentionally tests this same missing-information load.
    """
    result = copy.deepcopy(list(messages))
    omitted = []
    for message in result:
        if not isinstance(message.get("content"), str):
            raise ValueError("Indexed Prototype experiment requires text inputs")
        try:
            body = json.loads(message["content"])
        except ValueError:
            continue
        stable = body.get("stable_builder_context") if isinstance(body, dict) else None
        if not isinstance(stable, dict) or not isinstance(stable.get("semantic_invariants"), dict):
            continue
        invariants = stable["semantic_invariants"]
        if invariants.get("contract") != "adaos.builder.semantic_prototype_candidate.v2":
            raise ValueError("Indexed guidance requires the exact v2 contract")
        from adaos.sdk.builder.prototype import semantic_generation_guidance
        if invariants != semantic_generation_guidance():
            raise ValueError("Refusing to index unknown or modified platform guidance")
        omitted.extend(key for key in invariants if key not in {"contract", "stage", "limits"})
        stable["semantic_invariants"] = {key: value for key, value in invariants.items() if key in {"contract", "stage", "limits"}}
        stable["prototype_retrieval"] = retrieval_index(available=available)
        message["content"] = canonical(body)
    if not omitted:
        raise ValueError("No platform guidance found for the indexed experiment")
    return result, {"schema": "adaos.builder.prototype_indexed_delivery.v1", "strategy": "adaptive-indexed",
        "input_sha256": digest(messages), "output_sha256": digest(result), "omitted": sorted(omitted),
        "library_digest": library_snapshot()["library_digest"], "retrieval_available": available,
        "inline_preserved": ["user facts", "required refs", "provider schema", "stage", "hard limits"],
        "control": "No-retrieval is an intentional missing-guidance experimental control, not a shipping default"}


def redact_credentials(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): "[REDACTED]" if re.search(r"authorization|access_token|bearer|api_key|secret|password|(?:^|[_-])session[_-]id$", str(key), re.I)
                else redact_credentials(content) for key, content in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_credentials(item) for item in value]
    return value


def _result(envelope):
    response = envelope.get("response", envelope)
    result = response.get("result", response)
    if not isinstance(result, dict) or envelope.get("ok") is False or response.get("ok") is False:
        raise RuntimeError("Root MCP operation failed")
    return result


def issue_protocol() -> dict[str, Any]:
    """Create a least-privilege native remote-MCP descriptor; never a user URL."""
    from adaos.sdk.llm import llm_client
    from adaos.services.root.client import RootHttpClient

    http, cfg = llm_client._root_http_client()
    headers = llm_client._identity_headers(llm_client._current_ctx(), cfg)
    subnet = str(headers.get("X-AdaOS-Subnet-Id") or getattr(cfg, "subnet_id", "") or "").strip()
    if not subnet:
        raise RuntimeError("Cannot scope Prototype MCP to the current subnet")
    key = http.base_url + ":" + subnet
    lease = _LEASES.get(key)
    now = datetime.now(timezone.utc).timestamp()
    if lease and lease["expires_epoch"] > now + 930 and lease["library_digest"] == library_snapshot()["library_digest"]:
        return copy.deepcopy(lease)
    issued = _result(http.request("POST", "/v1/root/mcp/call", headers=headers,
        json={"tool_id": "hub.issue_mcp_session", "arguments": {"target_id": "hub:" + subnet,
            "audience": "openai-builder-prototype", "capability_profile": "BuilderPrototypeRead",
            "capabilities": [CAPABILITY], "ttl_seconds": 3600, "note": "prototype-guidance-only"}}, timeout=15))
    token = issued.get("access_token")
    session_id = issued.get("session_id")
    if not token or not session_id or set(issued.get("capabilities", [])) != {CAPABILITY}:
        raise RuntimeError("Root did not issue the exact read-only Prototype scope")
    # Match provider authentication: the read-only bearer must work without mTLS.
    scoped = RootHttpClient(base_url=http.base_url, verify=http.verify, cert=None,
        default_headers={"Authorization": "Bearer " + str(token)})
    descriptor = scoped.request("GET", f"/v1/root/mcp/sessions/{session_id}/openai-tool",
        params={"server_label": SERVER_LABEL, "require_approval": "never", "allowed_tools": ",".join(TOOLS)}, timeout=15)
    tool = descriptor.get("openai", {}).get("responses_tool")
    if (not isinstance(tool, dict) or tool.get("type") != "mcp" or tool.get("server_label") != SERVER_LABEL
        or set(tool.get("allowed_tools", [])) != set(OPENAI_TOOLS)
        or tool.get("authorization") != token or tool.get("require_approval") != "never"):
        raise RuntimeError("Root returned an unexpected Prototype MCP tool descriptor")
    from urllib.parse import urlsplit
    url = urlsplit(str(tool.get("server_url") or ""))
    if (url.scheme != "https" or url.hostname not in {"api.inimatic.com", "ru.api.inimatic.com", "mcp.api.inimatic.com", "mcp.ru.api.inimatic.com"}
        or url.port not in {None, 443} or url.path != "/v1/root/mcp" or url.query or url.fragment or url.username or url.password):
        raise RuntimeError("Root returned an unexpected public Prototype MCP endpoint")
    provider_http = RootHttpClient(base_url=f"{url.scheme}://{url.netloc}", verify=http.verify, cert=None,
        default_headers={"Authorization": "Bearer " + str(token)})
    rpc = provider_http.request("POST", url.path, headers={"Accept": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=15)
    names = {item.get("name") for item in rpc.get("result", {}).get("tools", [])}
    if rpc.get("error") or names != set(OPENAI_TOOLS):
        raise RuntimeError("Native MCP discovery does not expose exactly the read-only Prototype tools")
    # A published library must match the exact Core guide used by this process.
    proof = _result(scoped.request("POST", "/v1/root/mcp/call", json={"tool_id": SEARCH_TOOL,
        "arguments": {"query": "view_roles", "expected_digest": library_snapshot()["library_digest"], "limit": 1}}, timeout=15))
    if proof.get("library_digest") != library_snapshot()["library_digest"] or not proof.get("items"):
        raise RuntimeError("Published Prototype guidance cannot be retrieved")
    lease = {"tools": [tool], "session_id": str(session_id), "root_url": http.base_url,
        "expires_epoch": datetime.fromisoformat(issued["expires_at"].replace("Z", "+00:00")).timestamp(),
        "library_digest": proof["library_digest"], "capabilities": [CAPABILITY],
        "native_discovery": sorted(names), "provider_authentication": "scoped_bearer_without_mtls"}
    _LEASES[key] = lease
    return copy.deepcopy(lease)


def generation_options() -> dict[str, Any]:
    if not enabled():
        return {}
    return {"tools": issue_protocol()["tools"], "tool_choice": "auto", "max_tool_calls": 40}
