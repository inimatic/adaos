"""Source-linked interpretation proposals and deterministic Core admission.

The model supplies meaning; Core supplies identities, literal grounding, stage
policy, coverage and the acceptance checks. No application is written here.
"""
from __future__ import annotations

import copy
import json
import re
import os
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


def _provider_schema_capacity(schema, *, stage='interpretation'):
    """Fail before spending; never truncate source/history enums to fit."""
    enums, characters, properties = 0, 0, 0
    oversized_enum = False
    length = lambda text: len(text.encode('utf-16-le')) // 2

    def visit(node):
        nonlocal enums, characters, properties, oversized_enum
        if isinstance(node, dict):
            for key in ('properties', '$defs'):
                names = node.get(key) or {}
                characters += sum(length(name) for name in names)
                if key == 'properties':properties += len(names)
            values = node.get('enum') or []
            enums += len(values)
            size = sum(length(value) for value in values if isinstance(value,str))
            characters += size
            oversized_enum = oversized_enum or len(values) > 250 and size > 15000
            if isinstance(node.get('const'),str):characters += length(node['const'])
            for child in node.values():visit(child)
        elif isinstance(node,list):
            for child in node:visit(child)
    visit(schema)
    if enums > 1000 or characters > 120000 or properties > 5000 or oversized_enum:
        raise TaskInterpretationRejected([{'code':f'task.{stage}_schema_capacity','gate':'G5',
            'owner':'context_compilation','detail':'The complete '+stage+' schema exceeds the supported provider capacity; no truncation or submission is allowed',
            'enum_values':enums,'string_characters':characters,'object_properties':properties}])


def interpretation_contract(*, provider=False, task=None, previous_brief=None):
    value = json.loads((Path(__file__).resolve().parents[2] / "abi" /
                        "builder.task_interpretation.v1.schema.json").read_text(encoding="utf-8"))
    if task:
        value['properties']['source_digest'] = {'type': 'string', 'const': task['source']['digest']}
        ids = [row['id'] for row in task['clauses']]
        value['$defs']['clauseRole']['properties']['clause_id']['enum'] = ids
        value['$defs']['clauseRefs']['items']['enum'] = ids
        if provider:
            # Do not ask the model to reproduce punctuation while also deciding
            # meaning. Select a complete source clause; Core still verifies its
            # cited ownership and admits older literal-substring proposals.
            value['$defs']['sourceStatement'] = {'type': 'string',
                'enum': list(dict.fromkeys(row['text'].strip() for row in task['clauses'] if row['text'].strip()))}
            value['$defs']['requirement']['properties']['statement'] = {'$ref': '#/$defs/sourceStatement'}
    base = value['$defs']['requirement']
    if provider:
        from .task_assertions import assertion_ledger
        prior = assertion_ledger((previous_brief or {}).get('task_contract') or {})
        active_ids = [row['id'] for row in prior if row['status'] == 'active']
        if active_ids:
            value['$defs']['priorRequirementRef'] = {'type': 'string', 'enum': active_ids}
            base['properties']['supersedes']['items'] = {'$ref': '#/$defs/priorRequirementRef'}
        else:
            base['properties']['supersedes']['maxItems'] = 0
    variants = []
    for kinds, operations, families in (
        (['behavior'], base['properties']['operation']['anyOf'][0], None),
        (['information', 'collection', 'ui', 'verification'], {'type': 'null'}, None),
        (['data_state'], {'type': 'null'}, ['value', 'runtime']),
        (['authoring', 'policy', 'exclusion'], {'type': 'null'}, ['policy']),
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
        'fact marks literal source evidence: initial record values or a typed literal constraint. '
        'UI labels belong to constraints, never to invented fixture attributes. policy means authoring/stage/authority constraints only. '
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
        delivery = os.getenv('ADAOS_BUILDER_INTERPRETATION_SCHEMA_DELIVERY', 'references')
        from .provider_schema import inline_local_references, coalesce_primitive_unions, SchemaProjectionError
        try:
            if delivery == 'inline_refs':
                value = inline_local_references(value)
            elif delivery == 'primitive_unions':
                value = coalesce_primitive_unions(value)
            elif delivery != 'references':
                raise TaskInterpretationRejected([{'code':'task.interpretation_schema_delivery_unknown',
                    'gate':'G5','owner':'context_compilation','detail':'Unknown interpretation schema delivery profile'}])
        except SchemaProjectionError as error:
            raise TaskInterpretationRejected([{'code':'task.interpretation_schema_projection_invalid',
                'gate':'G5','owner':'context_compilation','detail':str(error)}]) from error
        _provider_schema_capacity(value)
    return value


def interpretation_messages(task, *, previous_brief=None):
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    previous = (previous_brief or {}).get("task_contract") or {}
    from .task_assertions import assertion_ledger
    prior_rows = assertion_ledger(previous) if previous else []
    prior_assertions = [{'id': row['id'], 'group': row['assertion_group'],
        'assertion': {key: copy.deepcopy(value) for key, value in row['assertion'].items()
            if key not in {'clause_ids', 'record_evidence', 'field_evidence'}},
        'source_digest': row['source_digest']}
        for row in prior_rows if row['status'] == 'active' and row['kind'] == 'assertion']
    return [
        {"role": "system", "content": (
            "Interpret the exact user evidence for an AdaOS local Prototype. Return only the typed proposal. "
            "Classify every source clause, retaining negation, conditions, actor, target and operation direction. "
            "For each requirement statement select one COMPLETE source clause from the schema enum, without changing "
            "punctuation or adding a final period. Multiple requirements may cite the same complete clause; kind/operation "
            "separate their meanings. Literal fixture evidence remains an exact substring. One clause may have several roles. "
            "A verification instruction is not a product operation; policy is not a widget or data-state proof. "
            "Use data_state only for a requested observable data condition, never viewport, usability or authoring policy. "
            "Extract explicit fixture subject/field/value, command precondition and effect, named sections, exact search "
            "fields and viewport constraints. Keep source words for subjects, source_field, command labels and choice values; "
            "do not invent internal IDs or translate literals. Do not treat missing extraction as missing user knowledge. "
            "Ask at most three questions, only material to behavior/authority; reversible presentation belongs to design_freedom. "
            "A transition requirement needs a transition with its exact command label, field, before and after values. "
            "Taxonomy: behavior has a non-null product operation. information/collection/ui/data_state/policy/verification/exclusion "
            "have operation=null. Reading records is behavior inspect/list, not information+inspect. data_state accepts value/runtime "
            "verification only; viewport and usable controls are UI/layout obligations. Only authoring, authority and stage restrictions "
            "are policy; a request to verify outcomes is verification. Keep each policy clause's requirement kind=policy or exclusion. "
            "Positive instructions to BUILD the Prototype, choose a language/model or remain in its stage have kind=authoring, "
            "operation=null, verification_family=policy. 'Build a local executable Prototype in Spanish' is an authoring "
            "instruction plus a locale constraint, never a product create/other operation. Only an end user's application "
            "action such as 'Add a sensor record' is product create. Retain authoring clauses under the policy role. "
            "Facts are INITIAL RECORD FIELDS ONLY. For 'Sensor S1: reading 12' extract subject='S1', source_field='reading', "
            "field='reading', value=12, record_evidence='Sensor S1: reading 12', field_evidence='reading 12'. "
            "The literal source_field and a proposed stable identifier field are different. Never translate source_field. "
            "Copy one record's source excerpt, then the exact contained field/value phrase; do not cite a whole multi-record paragraph. "
            "Never invent fact fields such as runtime_scope, label, date_semantics or a relationship field absent in the source. "
            "Record identity is the literal scalar, without a noun prefix. Preserve source-linked UI labels as ui requirements, "
            "not fixture facts. Search examples and unchanged values are query/preservation checks, not new fixture records. "
            "An explicitly quoted disclosure, warning or explanatory text that must be shown needs a visible_text constraint; "
            "an application metadata title or a model comment is not evidence that the person can see it in Preview. "
            "Use a named collection/section as search_fields subject only when its exact title is present; otherwise use empty subject. "
            "Viewport constraint values contain pixel widths only; keep usability/overflow obligations in ui requirements. "
            "Supersede only the exact prior requirement ID explicitly corrected by this turn; preserve other accepted work. "
            "Prior literal assertions have separate assertion IDs. To change a previous expected value, include that exact "
            "assertion ID in the correcting requirement's supersedes; repeating it unchanged needs no supersession. "
            "No application, tools, external effects, acceptance certification or execution authority are produced here.")},
        {"role": "user", "content": canonical({"interpretation_request": {
            "source_digest": task["source"]["digest"], "source_clauses": task["clauses"],
            "authority": task["authority"], "scope": task["scope"],
            "prior_active_requirements": [row for row in prior_rows if row['status'] == 'active' and row['kind'] != 'assertion'],
            "prior_literal_assertions": prior_assertions,
            "history_admission": {
                'changed_expected_value': 'When the current source explicitly replaces a prior expected value, the correcting requirement MUST put the exact prior assertion id in supersedes. Empty supersedes keeps the old expectation active and conflicts with the new value.',
                'example': {'prior': {'id': 'assertion:example', 'subject': 'Sensor-1', 'field': 'reading', 'value': 12},
                    'source': 'Replace the initial reading for Sensor-1 with 9; preserve other data.',
                    'correcting_requirement': {'supersedes': ['assertion:example']},
                    'new_fact': {'subject': 'Sensor-1', 'field': 'reading', 'value': 9}},
                'unchanged_or_unmentioned': 'Keep active. Never supersede an unrelated assertion or a whole collection merely to alter one field.'
            } if prior_assertions else None,
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
    if intent['digest'] != task['source']['digest']:
        raise TaskInterpretationRejected([{'code':'task.interpretation_task_source_conflict','gate':'G0',
            'owner':'source_admission','detail':'Interpretation TaskSpec and Intent target different sources'}])
    if proposal['source_digest'] != task['source']['digest']:
        raise TaskInterpretationRejected([{'code':'task.interpretation_source_mismatch','gate':'G3',
            'owner':'task_interpretation','detail':'The proposed interpretation targets a different source'}])
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
        if row["kind"] in {"authoring", "policy", "exclusion"} and row["verification_family"] != "policy":
            add("task.policy_owner_invalid", "Policy and exclusions require process verification, not UI proof")
        if row["kind"] == "data_state" and row["verification_family"] not in {"value", "runtime"}:
            add("task.state_owner_invalid", "A data state cannot stand in for a layout or process check")
    for ref, assigned in role_map.items():
        owned = [row for row in requirements if ref in row["clause_ids"]]
        if ("product" in assigned and not any(row["kind"] in {"behavior", "ui", "information", "collection", "data_state"} for row in owned)
                and not any(ref in row['clause_ids'] and row['kind'] in {'section_title', 'visible_text', 'search_fields', 'locale', 'viewport'}
                            for row in proposal['constraints'])):
            add("task.product_unrepresented", f"Product clause {ref} has no typed requirement")
        if "policy" in assigned and not any(row["kind"] in {"authoring", "policy", "exclusion"} for row in owned):
            add("task.policy_unrepresented", f"Policy clause {ref} has no retained process constraint")
        if "fact" in assigned and not any(ref in row["clause_ids"] for row in
                [*proposal["facts"], *proposal["constraints"], *proposal["transitions"]]):
            add("task.fact_unextracted", f"Fact clause {ref} must have a grounded record fact or typed literal assertion")
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
    if previous_brief:
        verify_identity(previous_brief, id_key='brief_id', prefix='brief:')
    if previous and previous['source']['scope']['project_ref'] != intent['scope']['project_ref']:
        raise TaskInterpretationRejected([{'code': 'task.history_scope_conflict', 'gate': 'G3',
            'owner': 'task_interpretation', 'detail': 'Prior task history belongs to another Project'}])
    if previous_brief and not previous:
        raise TaskInterpretationRejected([{"code": "task.legacy_history_unadmitted", "gate": "G3",
            "detail": "Interpret the retained legacy requirements before replacing their execution authority"}])
    from .task_assertions import assertion_ledger, consolidate_assertions
    prior_rows = {row['id']: row for row in assertion_ledger(previous)} if previous else {}
    current_ids = {row["id"]: "req:" + digest({"source": intent["digest"], "id": row["id"]})[7:31]
                   for row in requirements}
    for row in requirements:
        for old_id in row["supersedes"]:
            if old_id not in prior_rows or prior_rows[old_id]["status"] != "active":
                raise TaskInterpretationRejected([{'code': 'task.supersession_invalid', 'gate': 'G3',
                    'owner': 'task_interpretation', 'detail': 'Supersession must identify one exact active prior requirement or assertion'}])
            prior_rows[old_id].update(status="superseded", superseded_by=current_ids[row["id"]])
    ledger = list(prior_rows.values()) + [{**copy.deepcopy(row), "id": current_ids[row["id"]],
              "status": "active", "source_digest": intent["digest"]} for row in requirements]
    additions = {'facts': proposal['facts'], 'constraints': normalized_constraints,
        'transitions': [{**copy.deepcopy(row), 'requirement_id': current_ids[row['requirement_id']]}
                        for row in proposal['transitions']]}
    ledger, assertions, conflicts = consolidate_assertions(ledger, additions, intent['digest'])
    if conflicts:
        raise TaskInterpretationRejected(conflicts)
    contract = {"schema": "adaos.builder.task_contract.v1", "source": copy.deepcopy(intent),
                "requirements": ledger, **assertions,
                "clause_roles": copy.deepcopy(roles), "design_freedom": copy.deepcopy(proposal["design_freedom"]),
                "proposal_digest": digest(proposal), "previous_digest": digest(previous) if previous else None,
                "admission_basis": "source_coverage_literal_grounding_and_typed_invariants"}
    brief = compile_prototype_brief(intent)
    for group in ("principal_jobs", "residual_requirements", "information_requirements", "collection_requirements", "operations", "exclusions"):
        brief[group] = []
    brief["representative_states"] = _knowledge("unknown")
    state_values = []
    for row in ledger:
        if row["status"] != "active" or row["kind"] in {"authoring", "policy", "verification", "assertion"}:
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
