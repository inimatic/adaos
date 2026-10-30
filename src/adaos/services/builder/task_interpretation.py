"""Source-linked interpretation proposals and deterministic Core admission.

The model supplies meaning; Core supplies identities, literal grounding, stage
policy, coverage and the acceptance checks. No application is written here.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from collections.abc import Mapping

from jsonschema import Draft202012Validator

from adaos.services.builder_context import canonical, digest
from adaos.services.builder_intent import compile_prototype_brief, _validate, _knowledge
from .task_spec import seal, verify_identity

SCHEMA = "adaos.builder.task_interpretation.v1"


class TaskInterpretationRejected(ValueError):
    def __init__(self, findings):
        self.findings = findings
        super().__init__("; ".join(item["detail"] for item in findings))


def interpretation_contract(*, provider=False):
    value = json.loads((Path(__file__).resolve().parents[2] / "abi" /
                        "builder.task_interpretation.v1.schema.json").read_text(encoding="utf-8"))
    if provider:
        def project(node):
            if isinstance(node, dict):
                for key in ("$schema", "$id", "minItems", "minLength", "uniqueItems"):
                    node.pop(key, None)
                for child in node.values():
                    project(child)
            elif isinstance(node, list):
                for child in node:
                    project(child)
        project(value)
    return value


def interpretation_messages(task, *, previous_brief=None):
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    previous = (previous_brief or {}).get("task_contract") or {}
    return [
        {"role": "system", "content": (
            "Interpret the exact user evidence for an AdaOS local Prototype. Return only the typed proposal. "
            "Classify every source clause, retaining negation, conditions, actor, target and operation direction. "
            "Use exact source substrings for requirement statements and literal fixture facts. One clause may have several roles. "
            "A verification instruction is not a product operation; policy is not a widget or data-state proof. "
            "Use data_state only for a requested observable data condition, never viewport, usability or authoring policy. "
            "Extract explicit fixture subject/field/value, command precondition and effect, named sections, exact search "
            "fields and viewport constraints. Keep source words for subjects, fields, command labels and choice values; "
            "do not invent internal IDs or translate literals. Do not treat missing extraction as missing user knowledge. "
            "Ask only questions material to behavior/authority; reversible presentation belongs to design_freedom. "
            "A transition requirement needs a transition with its exact command label, field, before and after values. "
            "Supersede only the exact prior requirement ID explicitly corrected by this turn; preserve other accepted work. "
            "No application, tools, external effects, acceptance certification or execution authority are produced here.")},
        {"role": "user", "content": canonical({"interpretation_request": {
            "source_digest": task["source"]["digest"], "source_clauses": task["clauses"],
            "authority": task["authority"], "scope": task["scope"],
            "prior_active_requirements": [row for row in previous.get("requirements", [])
                                          if row["status"] == "active"],
            "allowed_families": ["value", "transition", "preservation", "query", "layout", "locale", "policy", "runtime"],
        }})},
    ]


def _contains_literal(text, value):
    if isinstance(value, str):
        return bool(value.strip()) and value.casefold() in text.casefold()
    if value is None:
        return bool(re.search(r"\b(?:null|unknown|неизвестно)\b", text, re.I))
    if isinstance(value, bool):
        return bool(re.search(r"\b" + str(value).lower() + r"\b", text, re.I))
    literals = re.findall(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?!\w|[.,]\d)", text)
    return any(float(literal.replace(",", ".")) == value for literal in literals)


def admit_interpretation(task, proposal, intent, *, previous_brief=None):
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    verify_identity(intent, id_key="intent_id", prefix="intent:")
    Draft202012Validator(interpretation_contract()).validate(proposal)
    if proposal["source_digest"] != task["source"]["digest"] or intent["digest"] != proposal["source_digest"]:
        raise ValueError("Interpretation proposal targets a different source")
    clauses = {row["id"]: row for row in task["clauses"]}
    findings = []

    def add(code, detail):
        findings.append({"code": code, "gate": "G3", "owner": "task_interpretation", "detail": detail})

    def evidence(row):
        refs = row.get("clause_ids", [])
        if not refs or not set(refs) <= clauses.keys():
            add("task.source_reference_invalid", "Every interpreted item needs exact existing source clause IDs")
            return ""
        return "\n".join(clauses[ref]["text"] for ref in refs)

    roles = proposal["clause_roles"]
    role_ids = [row["clause_id"] for row in roles]
    if set(role_ids) != clauses.keys() or len(role_ids) != len(set(role_ids)):
        add("task.source_coverage_incomplete", "Every source clause must be classified exactly once")
    role_map = {row["clause_id"]: row["roles"] for row in roles}
    for ref, assigned in role_map.items():
        if assigned == ["formatting"] and ref in clauses and re.search(r"[^\W_]|\d", clauses[ref]["text"], re.UNICODE):
            if not re.fullmatch(r"\s*\d+[.)]?\s*", clauses[ref]["text"]):
                add("task.formatting_contains_content", "A nonempty source instruction cannot be discarded as formatting")
    requirements = proposal["requirements"]
    ids = [row["id"] for row in requirements]
    if len(ids) != len(set(ids)):
        add("task.requirement_identity_duplicate", "Requirement IDs must be unique")
    for row in requirements:
        text = evidence(row)
        if row["statement"] not in text:
            add("task.statement_not_source", f"Requirement {row['id']} must quote an exact source substring")
        if row["operation"] is not None and row["kind"] != "behavior":
            add("task.operation_owner_invalid", "Only a behavior requirement may declare an operation")
        if row["kind"] in {"policy", "exclusion"} and row["verification_family"] != "policy":
            add("task.policy_owner_invalid", "Policy and exclusions require process verification, not UI proof")
        if row["kind"] == "data_state" and row["verification_family"] not in {"value", "runtime"}:
            add("task.state_owner_invalid", "A data state cannot stand in for a layout or process check")
    for ref, assigned in role_map.items():
        owned = [row for row in requirements if ref in row["clause_ids"]]
        if "product" in assigned and not any(row["kind"] in {"behavior", "ui", "information", "collection", "data_state"} for row in owned):
            add("task.product_unrepresented", f"Product clause {ref} has no typed requirement")
        if "policy" in assigned and not any(row["kind"] in {"policy", "exclusion"} for row in owned):
            add("task.policy_unrepresented", f"Policy clause {ref} has no retained process constraint")
        if "fact" in assigned and not any(ref in row["clause_ids"] for row in proposal["facts"]):
            add("task.fact_unextracted", f"Fact clause {ref} must have literal facts or a material question")
    for row in proposal["facts"]:
        text = evidence(row)
        if not all(_contains_literal(text, row[key]) for key in ("subject", "field", "value")):
            add("task.fact_not_source", f"Fixture fact {row['subject']}/{row['field']} lacks literal source evidence")
    transition_ids = set()
    for row in proposal["transitions"]:
        text = evidence(row)
        requirement = next((item for item in requirements if item["id"] == row["requirement_id"]), None)
        if not requirement or requirement["operation"] not in {"update", "transition"}:
            add("task.transition_requirement_invalid", "A transition must own an update or transition requirement")
        if not all(_contains_literal(text, row[key]) for key in ("command_label", "field", "from_value", "to_value")):
            add("task.effect_not_source", "Transition labels, fields and values must be literal source facts")
        if type(row["from_value"]) is type(row["to_value"]) and row["from_value"] == row["to_value"]:
            add("task.transition_no_effect", "An explicitly requested transition must change its field")
        transition_ids.add(row["requirement_id"])
    for row in requirements:
        if row["operation"] == "transition" and row["id"] not in transition_ids:
            add("task.transition_unextracted", "A transition requires independently checkable precondition and effect")
    for row in proposal["constraints"]:
        text = evidence(row)
        if any(not _contains_literal(text, value) for value in row["values"]):
            add("task.constraint_not_source", f"Constraint {row['kind']} must retain source literals")
        if row["kind"] != "forbid_external" and not row["values"]:
            add("task.constraint_empty", f"Constraint {row['kind']} needs concrete values")
    for row in proposal["questions"]:
        evidence(row)
    if proposal["questions"]:
        add("task.clarification_required", "Material questions require answers before candidate generation")
    if findings:
        raise TaskInterpretationRejected(findings)

    previous = copy.deepcopy((previous_brief or {}).get("task_contract") or {})
    if previous_brief and not previous:
        raise TaskInterpretationRejected([{"code": "task.legacy_history_unadmitted", "gate": "G3",
            "detail": "Interpret the retained legacy requirements before replacing their execution authority"}])
    prior_rows = {row["id"]: row for row in previous.get("requirements", [])}
    current_ids = {row["id"]: "req:" + digest({"source": intent["digest"], "id": row["id"]})[7:31]
                   for row in requirements}
    for row in requirements:
        for old_id in row["supersedes"]:
            if old_id not in prior_rows or prior_rows[old_id]["status"] != "active":
                raise ValueError("Supersession must identify an exact active prior requirement")
            prior_rows[old_id].update(status="superseded", superseded_by=current_ids[row["id"]])
    ledger = list(prior_rows.values()) + [{**copy.deepcopy(row), "id": current_ids[row["id"]],
              "status": "active", "source_digest": intent["digest"]} for row in requirements]
    contract = {"schema": "adaos.builder.task_contract.v1", "source": copy.deepcopy(intent),
                "requirements": ledger, "facts": previous.get("facts", []) + copy.deepcopy(proposal["facts"]),
                "transitions": previous.get("transitions", []) + [
                    {**copy.deepcopy(row), "requirement_id": current_ids[row["requirement_id"]]}
                    for row in proposal["transitions"]],
                "constraints": previous.get("constraints", []) + copy.deepcopy(proposal["constraints"]),
                "clause_roles": copy.deepcopy(roles), "design_freedom": copy.deepcopy(proposal["design_freedom"]),
                "proposal_digest": digest(proposal), "previous_digest": digest(previous) if previous else None,
                "admission_basis": "source_coverage_literal_grounding_and_typed_invariants"}
    brief = compile_prototype_brief(intent)
    for group in ("principal_jobs", "residual_requirements", "information_requirements", "collection_requirements", "operations", "exclusions"):
        brief[group] = []
    brief["representative_states"] = _knowledge("unknown")
    state_values = []
    for row in ledger:
        if row["status"] != "active" or row["kind"] in {"policy", "verification"}:
            continue
        statement = {"id": row["id"], "statement": row["statement"],
                     "evidence": [row["source_digest"] + "/" + ref for ref in row["clause_ids"]], "confidence": 1.0}
        if row["kind"] == "exclusion":
            brief["exclusions"].append(statement)
        elif row["kind"] == "data_state":
            state_values.append(row["statement"])
        elif row["operation"]:
            brief["operations"].append({**{key: value for key, value in statement.items() if key != "confidence"},
                "kind": row["operation"], "target": _knowledge("unknown"),
                "effect_scope": "prototype" if row["operation"] in {"create", "update", "assign", "transition", "delete", "archive"} else "read",
                "authority": "allowed"})
        else:
            brief["residual_requirements"].append(statement)
    if state_values:
        brief["representative_states"] = {"state": "known", "value": state_values,
                                         "evidence": [intent["intent_id"]], "confidence": 1.0}
    brief["interpretation"] = {"mode": "model_assisted", "confidence": 1.0, "unresolved_fields": []}
    brief["task_contract"] = contract
    brief = seal(brief, id_key="brief_id", prefix="brief:")
    _validate("builder.prototype_brief.v1.schema.json", brief)
    admitted_task = seal({**task, "brief_ref": brief["brief_id"], "brief_digest": brief["digest"],
                          "requirements": ledger, "acceptance": {
                              key: copy.deepcopy(contract[key]) for key in ("facts", "transitions", "constraints")},
                          "interpretation_status": "admitted", "proposal_digest": digest(proposal),
                          "clauses": [{**row, "classification": role_map[row["id"]]} for row in task["clauses"]],
                          "design_freedom": proposal["design_freedom"]})
    return admitted_task, brief
