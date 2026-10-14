"""Final-input admission of declared contract facts, not arbitrary prose truth.

Facts from intent, proposals and observations do not acquire platform authority.
Required dependencies precede ranking/budgeting. A receipt pins the entire input.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest as digest
from adaos.services.ui_component_contracts import project_component_contract

_INPUT_SCOPE = ContextVar('adaos_contract_input_scope', default=None)


@contextmanager
def component_input_scope(snapshot, *, required_ids=None, route='builder'):
    """Core-only pins shared by generation, repair, increment and final transport."""
    token = _INPUT_SCOPE.set({'snapshot': copy.deepcopy(snapshot), 'required_ids': required_ids, 'route': route})
    try:
        yield
    finally:
        _INPUT_SCOPE.reset(token)


def current_component_input_scope():
    return copy.deepcopy(_INPUT_SCOPE.get())


class ContractInputRejected(ValueError):
    def __init__(self, findings: list[dict[str, Any]]):
        self.findings = copy.deepcopy(findings)
        super().__init__("Contract input rejected: " + ", ".join(item["code"] for item in findings))


def dependency_closure(snapshot: Mapping[str, Any], required_ids: list[str]) -> list[str]:
    units = {unit["id"]: unit for unit in snapshot["retrieval_units"]}
    closure = set(required_ids)
    while True:
        missing = closure - set(units)
        if missing:
            raise ContractInputRejected([{"code": "contract.required_missing", "refs": sorted(missing)}])
        expanded = closure | {ref for identity in closure for ref in units[identity]["dependencies"]}
        if closure == expanded:
            return sorted(closure)
        closure = expanded


def _carriers(envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    packets = []
    messages = list(envelope.get("messages") or []) if "input" not in envelope else []
    if "input" in envelope and isinstance(envelope["input"], list):
        for message in envelope["input"]:
            if message.get("type") in {"function_call", "function_call_output", "reasoning"}:
                continue
            for part in message.get("content") or []:
                if isinstance(part, Mapping) and part.get("type") in {"input_text", "output_text"}:
                    messages.append({"role": message.get("role"), "content": part.get("text")})
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except ValueError:
            continue
        if isinstance(value, dict) and "platform_contracts" in value:
            packets.append({"role": message.get("role"), "packet": value["platform_contracts"]})
    # Responses normalizes system/developer messages into instructions. Check
    # that representation too; a late mutation cannot hide behind the source.
    for content in str(envelope.get("instructions") or "").split("\n\n"):
        try:
            value = json.loads(content)
        except ValueError:
            continue
        if isinstance(value, dict) and "platform_contracts" in value:
            packets.append({"role": "system", "packet": value["platform_contracts"]})
    def declared_schema_facts(value: Any) -> None:
        if isinstance(value, Mapping):
            if "x-adaos-contracts" in value:
                packets.append({"role": "developer", "packet": value["x-adaos-contracts"]})
            for item in value.values():
                declared_schema_facts(item)
        elif isinstance(value, list):
            for item in value:
                declared_schema_facts(item)
    declared_schema_facts(envelope.get("text") or {})
    declared_schema_facts(envelope.get("tools") or [])
    return packets


def callable_tool_names(envelope: Mapping[str, Any]) -> list[str]:
    if envelope.get("tool_choice") == "none":
        return []
    names = set()
    for tool in envelope.get("tools") or []:
        if tool.get("type") == "mcp":
            allowed = tool.get("allowed_tools")
            if not isinstance(allowed, list) or not all(isinstance(name, str) and name for name in allowed):
                raise ContractInputRejected([{"code": "contract.tool_scope_unbounded"}])
            names.update(tool.get("allowed_tools") or [])
        elif tool.get("type") == "function":
            name = tool.get("name") or (tool.get("function") or {}).get("name")
            if name:
                names.add(name)
    if envelope.get("tool_choice") == "none":
        names.clear()
    return sorted(names)


def admit_contract_input(envelope: Mapping[str, Any], *, snapshot: Mapping[str, Any],
                         required_ids: list[str], applicability: Mapping[str, Any] | None = None) -> dict[str, Any]:
    closure = dependency_closure(snapshot, required_ids)
    units = {unit["id"]: unit for unit in snapshot["retrieval_units"]}
    facts: dict[str, Any] = {}
    findings = []
    supplied_tools = callable_tool_names(envelope)
    for carrier in _carriers(envelope):
        packet = carrier["packet"]
        if carrier["role"] not in {"system", "developer"} or not isinstance(packet, Mapping):
            findings.append({"code": "contract.authority_incompatible", "role": carrier["role"]})
            continue
        if set(packet) != {"provenance", "discovery_policy", "callable_tools", "semantic_support", "binding_availability", "units"}:
            findings.append({"code": "contract.fact_shape_invalid"})
        if packet.get("provenance") != snapshot["provenance"]:
            findings.append({"code": "contract.source_incompatible"})
        if packet.get("discovery_policy") != snapshot["discovery_policy"]:
            findings.append({"code": "contract.discovery_policy_conflict"})
        if packet.get("callable_tools") != supplied_tools:
            findings.append({"code": "contract.callable_tools_conflict"})
        # 'Supported in Prototype' is not production-binding admission.
        support = packet.get("semantic_support") or {}
        if support != {"contract_id": snapshot["provenance"]["contract_id"], "supported": True}:
            findings.append({"code": "contract.semantic_support_conflict"})
        availability = packet.get("binding_availability") or {}
        if availability != {"status": "not_assessed", "production_callable": False}:
            findings.append({"code": "contract.binding_authority_unproven"})
        for unit in packet.get("units") or []:
            if not isinstance(unit, Mapping) or not isinstance(unit.get("id"), str):
                findings.append({"code": "contract.fact_shape_invalid"})
                continue
            identity = unit.get("id")
            if identity in facts and facts[identity] != unit:
                findings.append({"code": "contract.fact_conflict", "ref": identity})
            facts[identity] = unit
    expected_applicability = dict(applicability or snapshot["provenance"]["applicability"])
    if expected_applicability != snapshot["provenance"]["applicability"]:
        findings.append({"code": "contract.mode_incompatible", "requested": expected_applicability})
    for identity in closure:
        fact = facts.get(identity)
        if fact is None:
            findings.append({"code": "contract.required_missing", "ref": identity})
        elif fact != units[identity]:
            findings.append({"code": "contract.fact_incompatible", "ref": identity})
    for identity, fact in facts.items():
        if identity not in units:
            findings.append({"code": "contract.unknown_identity", "ref": identity})
        elif fact.get("locator") != units[identity]["locator"]:
            findings.append({"code": "contract.unresolved_locator", "ref": identity})
        elif fact != units[identity]:
            findings.append({"code": "contract.fact_incompatible", "ref": identity})
    if findings:
        raise ContractInputRejected(findings)
    receipt = {"schema": "adaos.context.contract_input_admission.v1", "status": "admitted",
               "input_digest": digest(envelope), "snapshot_digest": digest(snapshot),
               "source": copy.deepcopy(snapshot["provenance"]), "required_closure": closure,
               "fact_digests": {identity: units[identity]["content_digest"] for identity in closure},
               "projection_version": snapshot["provenance"]["projection_version"],
               "tool_scope": supplied_tools, "discovery_policy_digest": digest(snapshot["discovery_policy"]),
               "findings": [], "semantic_support": True, "binding_availability": "not_assessed"}
    receipt["receipt_digest"] = digest(receipt)
    return receipt


def prepare_component_input(envelope: Mapping[str, Any], *, snapshot: Mapping[str, Any] | None = None,
                            required_ids: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = copy.deepcopy(snapshot) if snapshot is not None else project_component_contract()
    required_ids = required_ids if required_ids is not None else [unit["id"] for unit in snapshot["retrieval_units"]]
    closure = dependency_closure(snapshot, required_ids)
    result = copy.deepcopy(dict(envelope))
    if not _carriers(result):
        from adaos.services.contract_projections import project_context_units, consume_context_units
        projection = project_context_units(snapshot=snapshot)
        selected = consume_context_units(projection, consumer='builder.inline', required_ids=closure)
        units = [{key: value for key, value in unit.items() if key not in ('consumer_masks', 'source_kind')}
                 for unit in selected['units']]
        packet = {"provenance": snapshot["provenance"], "discovery_policy": snapshot["discovery_policy"],
                  "semantic_support": {"contract_id": snapshot["provenance"]["contract_id"], "supported": True},
                  "binding_availability": {"status": "not_assessed", "production_callable": False},
                  "callable_tools": callable_tool_names(result),
                  "units": units}
        content = json.dumps({"platform_contracts": packet}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if 'input' in result:
            result['instructions'] = (str(result.get('instructions') or '') + '\n\n' + content).strip()
        else:
            result["messages"] = [*result.get("messages", []), {"role": "system", "content": content}]
    return result, admit_contract_input(result, snapshot=snapshot, required_ids=required_ids)


def discover_semantic_candidates(query: Mapping[str, Any], **snapshot_inputs: Any) -> dict[str, Any]:
    """Delegate compatibility/authority/state/evidence to the owned CBS query."""
    from adaos.services.capability_binding_state.registry_query import execute_semantic_registry_query
    result = execute_semantic_registry_query(query, **snapshot_inputs)
    return {"registry_result": result, "semantic_support": "query_contract_compatible",
            "binding_availability": "portable_candidate_only_local_admission_pending", "callable_tools": []}
