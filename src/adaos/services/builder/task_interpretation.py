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


def interpretation_contract(*, provider=False, task=None):
    value = json.loads((Path(__file__).resolve().parents[2] / "abi" /
                        "builder.task_interpretation.v1.schema.json").read_text(encoding="utf-8"))
    if task:
        value['properties']['source_digest'] = {'type': 'string', 'const': task['source']['digest']}
        ids = [row['id'] for row in task['clauses']]
        value['$defs']['clauseRole']['properties']['clause_id']['enum'] = ids
        value['$defs']['clauseRefs']['items']['enum'] = ids
    base = value['$defs']['requirement']
    variants = []
    for kinds, operations, families in (
        (['behavior'], base['properties']['operation']['anyOf'][0], None),
        (['information', 'collection', 'ui', 'verification'], {'type': 'null'}, None),
        (['data_state'], {'type': 'null'}, ['value', 'runtime']),
        (['policy', 'exclusion'], {'type': 'null'}, ['policy']),
    ):
        branch = copy.deepcopy(base)
        branch['properties']['kind']['enum'] = kinds
        branch['properties']['operation'] = copy.deepcopy(operations)
        if families:
            branch['properties']['verification_family']['enum'] = families
        variants.append(branch)
    value['$defs']['requirement'] = {'anyOf': variants}
    value['$defs']['fact']['description'] = (
        'Only explicit initial record data. subject is the record identity scalar itself, not its noun phrase; '
        'source_field must occur literally in the source; field is a separate proposed identifier. Policy, UI labels, verification examples and preservation '
        'instructions are requirements/constraints, not invented record attributes.')
    value['$defs']['fact']['properties']['subject']['description'] = 'Exact scalar identity of one fixture record, e.g. S1 rather than Sensor S1.'
    value['$defs']['constraint']['properties']['subject']['description'] = 'An exact source identity or title identifying the collection; empty if the source has only one relevant search. Do not use a grammatical noun such as records.'
    value['$defs']['clauseRole']['properties']['roles']['description'] = (
        'fact means explicit initial record values only. policy means authoring/stage/authority constraints only. '
        'verification means instructions to check outcomes. viewport/layout is product+verification, not policy. '
        'formatting is only whitespace/list markers; design is an explicitly reversible choice, not a discarded requirement.')
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
            "fields and viewport constraints. Keep source words for subjects, source_field, command labels and choice values; "
            "do not invent internal IDs or translate literals. Do not treat missing extraction as missing user knowledge. "
            "Ask only questions material to behavior/authority; reversible presentation belongs to design_freedom. "
            "A transition requirement needs a transition with its exact command label, field, before and after values. "
            "Taxonomy: behavior has a non-null product operation. information/collection/ui/data_state/policy/verification/exclusion "
            "have operation=null. Reading records is behavior inspect/list, not information+inspect. data_state accepts value/runtime "
            "verification only; viewport and usable controls are UI/layout obligations. Only authoring, authority and stage restrictions "
            "are policy; a request to verify outcomes is verification. Keep each policy clause's requirement kind=policy or exclusion. "
            "Facts are INITIAL RECORD FIELDS ONLY. For 'Sensor S1: reading 12' extract subject='S1', source_field='reading', "
            "field='reading', value=12, record_evidence='Sensor S1: reading 12', field_evidence='reading 12'. "
            "The literal source_field and a proposed stable identifier field are different. Never translate source_field. "
            "Copy one record's source excerpt, then the exact contained field/value phrase; do not cite a whole multi-record paragraph. "
            "Never invent fact fields such as runtime_scope, label, date_semantics or a relationship field absent in the source. "
            "Record identity is the literal scalar, without a noun prefix. Preserve source-linked UI labels as ui requirements, "
            "not fixture facts. Search examples and unchanged values are query/preservation checks, not new fixture records. "
            "Use a named collection/section as search_fields subject only when its exact title is present; otherwise use empty subject. "
            "Viewport constraint values contain pixel widths only; keep usability/overflow obligations in ui requirements. "
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
        return bool(value.strip()) and bool(re.search(r'(?<![\w.-])' + re.escape(value) + r'(?![\w-]|\.\w)', text, re.I))
    if value is None:
        return bool(re.search(r"\b(?:null|unknown|неизвестно)\b", text, re.I))
    if isinstance(value, bool):
        return bool(re.search(r"\b" + str(value).lower() + r"\b", text, re.I))
    literals = re.findall(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?!\w|[.,]\d)", text)
    return any(float(literal.replace(",", ".")) == value for literal in literals)


def _attribute_literal(phrase, label, expected):
    match = re.fullmatch(re.escape(label) + r'\s*(?::|=|—|–|\bis\b)?\s*(.+)', phrase.strip(), re.I)
    if not match:
        return False
    raw = match[1].strip().rstrip(',;').strip()
    if len(raw) >= 2 and (raw[0], raw[-1]) in {('«', '»'), ('"', '"'), ("'", "'"), ('`', '`')}:
        raw = raw[1:-1]
    if isinstance(expected, str):
        return raw == expected
    if expected is None:
        return raw.casefold() in {'null', 'unknown', 'неизвестно'}
    if isinstance(expected, bool):
        return raw.casefold() == str(expected).lower()
    number = re.fullmatch(r'([-+]?\d+(?:[.,]\d+)?)\s*%?', raw)
    return bool(number) and float(number[1].replace(',', '.')) == expected


def admit_interpretation(task, proposal, intent, *, previous_brief=None):
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    verify_identity(intent, id_key="intent_id", prefix="intent:")
    try:
        Draft202012Validator(interpretation_contract()).validate(proposal)
    except Exception as exc:
        from jsonschema import ValidationError
        if not isinstance(exc, ValidationError):
            raise
        raise TaskInterpretationRejected([{'code': 'task.interpretation_shape_invalid', 'gate': 'G3',
            'owner': 'task_interpretation', 'path': '$.' + '.'.join(str(part) for part in exc.absolute_path),
            'detail': exc.message}]) from exc
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
        record_text, field_text = row['record_evidence'], row['field_evidence']
        if (record_text not in text or field_text not in record_text
                or not _contains_literal(record_text, row['subject'])
                or not _attribute_literal(field_text, row['source_field'], row['value'])):
            add("task.fact_not_source", f"Fixture fact {row['subject']}/{row['field']} lacks literal source evidence")
        # A source paragraph can contain several records with identical field
        # names. Keep each record citation inside one sentence, joining source
        # segments separated by semicolons (attributes of the same record).
        from adaos.services.builder_intent import _clauses
        pieces = _clauses(text)
        groups = []
        for _, start, end in pieces:
            if groups and ';' in text[groups[-1][1]:start] and not re.search(r'[.!?]', text[groups[-1][1]:start]):
                groups[-1] = (groups[-1][0], end)
            else:
                groups.append((start, end))
        excerpt = record_text.strip().rstrip('.!?;')
        if not any(excerpt in text[start:end] for start, end in groups):
            add('task.record_evidence_scope_invalid', 'A fixture citation must isolate one source record rather than combine record sentences')
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

    normalized_constraints = copy.deepcopy(proposal["constraints"])
    for row in normalized_constraints:
        if row['kind'] == 'locale':
            aliases = []
            for value in row['values']:
                folded = value.casefold().strip()
                if folded == 'ru' or 'русск' in folded or 'russian' in folded:
                    aliases.append('ru')
                elif folded == 'en' or 'английск' in folded or 'english' in folded:
                    aliases.append('en')
                else:
                    raise TaskInterpretationRejected([{'code': 'task.locale_not_supported', 'gate': 'G4',
                        'owner': 'capability_admission', 'detail': f'No admitted locale projection for {value!r}'}])
            row['locale_codes'] = sorted(set(aliases))
        if row['kind'] == 'viewport':
            widths = []
            for value in row['values']:
                match = re.fullmatch(r'\s*(\d+)\s*(?:px|пкс|пиксел\w*)?\s*', value, re.I)
                if not match or not 240 <= int(match[1]) <= 7680:
                    raise TaskInterpretationRejected([{'code': 'task.viewport_not_resolved', 'gate': 'G3',
                        'owner': 'task_interpretation', 'detail': 'Viewport values contain concrete supported pixel widths only'}])
                widths.append(int(match[1]))
            row['widths_px'] = sorted(set(widths))
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
                "constraints": previous.get("constraints", []) + normalized_constraints,
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
