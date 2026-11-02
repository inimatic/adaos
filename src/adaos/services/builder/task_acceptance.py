"""Independent source-owned value/effect checks over canonical semantic output.

Bindings locate an implementation; they never supply the expected answer.
Browser, cancellation and persistence checks remain explicit runtime obligations.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from adaos.services.builder_context import digest


_DISPLAY_MATCH = {'section_title': True, 'visible_text': False}


def display_assertion_contract():
    """Expose the existing display match policy; never add source obligations."""
    return {'scope':'Meaning of admitted display constraints, not additional task facts.',
        'rules': {
            'section_title': {'whole_normalized_text': _DISPLAY_MATCH['section_title'],
                'surface':'Localized section.title (canonical section.label) on the actual views.',
                'meaning':'Every required section name must match as a whole after whitespace/case normalization. '
                    'Keep the same section id/title consistent on all its views. Appending explanatory text changes '
                    'a required section name; put that text in a separate view.title instead.'},
            'visible_text': {'whole_normalized_text': _DISPLAY_MATCH['visible_text'],
                'surface':'A localized title/label on an actual view or its section in the admitted subject scope.',
                'meaning':'The required literal may be part of that displayed title/label, with word boundaries. '
                    'Document title metadata, technical ids and record data alone do not satisfy this check. '
                    'Runtime acceptance still checks visibility; static admission does not establish first-frame visibility.'},
            'view_title': {'surface':'One exact source-owned view.title, NOT section metadata. Typed refs retain collection/details/editor role.',
                'meaning':'The required literal is the complete view title. Preserve retained view identity; an untyped empty subject still requires an unambiguous collection.'},
            'field_label': {'surface':'The actual source-owned resource field.label, projected by a real view.',
                'meaning':'subject is the exact source field identifier, not its new caption. Matching text on another field or in a disclosure is insufficient.'},
            'field_order': {'surface':'The exact collection field_refs and its compiled columns.',
                'meaning':'Every listed source field remains visible in this relative order; unrelated additional fields are not implicitly prohibited.'},
            'source_record_table': {'surface':'Table projections of the exact instruction source digest\'s fact slots.',
                'meaning':'Show all source fields in a table protects subject/source_field identities from that source request, '
                    'not fields introduced by future requests. Original assertion history retains this scope after a value amendment; '
                    'changing a value does not permit hiding the field. An instruction without resolvable source slots needs clarification.'}},
        'binding':'A section is grouping metadata, not another view. Bind a section or disclosure requirement '
            'to the actual declared views that display it. kind=view selects views[].id, NEVER section.id, '
            'document_id, a layout region or an imagined content/main view. You need not create a resource '
            'or unsupported content view to show explanatory text in a real view.title.'}


def _names(item):
    values = [item.get("id", "")]
    for key in ("label", "title"):
        value = item.get(key)
        if isinstance(value, Mapping):
            values.extend(str(text) for locale, text in value.items() if locale != "key")
        elif isinstance(value, str):
            values.append(value)
    return values


def _matches(expected, values):
    tokens = lambda text: re.findall(r"[^\W_]+", str(text).casefold(), re.UNICODE)
    wanted = tokens(expected)
    return bool(wanted) and any(wanted == tokens(value) for value in values)


def _display_names(item):
    # Technical identifiers locate implementations; they cannot prove that a
    # requested label or disclosure is visible to the person using Preview.
    return _names({key: item[key] for key in ('label', 'title') if key in item})[1:]


def _same(actual, expected):
    # JSON numbers compare by value, booleans never equal 0/1 by accident.
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def _literal_text(expected, values, *, whole=False):
    normalized = lambda text: re.sub(r'\s+', ' ', str(text)).strip().casefold()
    wanted = normalized(expected)
    return bool(wanted) and any(wanted == normalized(value) if whole else bool(re.search(
        r'(?<!\w)' + re.escape(wanted) + r'(?!\w)', normalized(value))) for value in values)


def _field(resource, name):
    tokens = lambda text: re.findall(r"[^\W_]+", str(text).casefold(), re.UNICODE)
    wanted = tokens(name)
    exact = [field for field in resource.get('fields', []) if str(name).casefold()==str(field['id']).casefold()]
    if exact:
        return exact[0] if len(exact)==1 else None
    values = [field for field in resource.get('fields', []) if str(name).casefold()==str(field['id']).rsplit('.',1)[-1].casefold()]
    if values:
        return values[0] if len(values)==1 else None
    values = [field for field in resource.get('fields', [])
              if wanted and any(wanted == tokens(label) for label in _display_names(field))]
    return values[0] if len(values) == 1 else None


def localized_display_names(contract):
    requested = {locale for row in contract['constraints'] if row['kind'] == 'locale'
        for locale in row.get('locale_codes', [])}
    locale = next(iter(requested)) if len(requested) == 1 else (contract.get('source') or {}).get('locale')
    locale = str(locale or '').split('-')[0]
    def shown(item):
        values=[]
        for key in ('title','label'):
            value=item.get(key)
            if isinstance(value,Mapping):values.extend([str(value[locale])] if locale in value else [])
            elif isinstance(value,str):values.append(value)
        return values
    return shown


def resolved_field_locator(resolved_fields):
    def bound_field(resource,name,subject=None):
        selected={target['field']['id']:target['field'] for target in resolved_fields
            if target['resource']['id']==resource.get('id') and (subject is None or target['fact']['subject']==subject)
            and str(name).casefold() in {str(target['fact']['field']).casefold(),
                                         str(target['fact'].get('source_field',target['fact']['field'])).casefold()}}
        return next(iter(selected.values())) if len(selected)==1 else (_field(resource,name) if not selected else None)
    return bound_field


def task_candidate_findings(document, brief, *, previous=None):
    contract = (brief or {}).get("task_contract")
    if not isinstance(contract, Mapping):
        return []
    from .source_literals import contract_literal_coverage
    from .task_interpretation import _attribute_literal
    source_findings, _ = contract_literal_coverage(contract,literal_matches=_attribute_literal)
    findings = [{**row,'path':'$.task_contract.facts'} for row in source_findings]
    from .source_operators import positive_operation_findings
    findings += [{**row,'path':'$.task_contract.requirements'} for row in positive_operation_findings(
        contract['source']['statement'],contract.get('requirements',[]),source_digest=contract['source']['digest'])]
    from .source_relations import contract_relation_findings
    findings += [{**row,'path':'$.task_contract.transitions'} for row in contract_relation_findings(contract)[0]]
    from .source_obligations import interpretation_findings, effective_constraints
    findings += [{**row,'path':'$.task_contract.constraints'} for row in interpretation_findings(
        contract['source']['statement'],contract['source']['digest'],contract)]
    from .semantic_ui_constraints import interpretation_findings as display_findings, candidate_issue, KINDS
    findings += [{**row,'path':'$.task_contract.constraints'} for row in display_findings(contract['source']['statement'],contract)]
    resources = {row["id"]: row for row in document.get("resources", [])}
    views = {row["id"]: row for row in document.get("views", [])}
    shown = localized_display_names(contract)

    def scope_names(item):
        return [item.get('id', '')] + shown(item)

    def add(code, detail, source, *, owner='candidate', gate='G8'):
        findings.append({"code": code, "path": "$", "gate": gate, "owner": owner,
                         "detail": detail, "source_clause_ids": source.get("clause_ids", [])})

    resolved_subjects = {}
    from .source_field_bindings import resolve_fields
    resolved_fields, binding_findings = resolve_fields(document,contract,previous=previous)
    findings.extend(binding_findings)
    for target in resolved_fields:
        fact,resource,record,field=(target[key] for key in ('fact','resource','record','field'))
        resolved_subjects.setdefault(fact['subject'], {})[(resource['id'],str(record['id']))] = (resource,record)
        if not _same(record.get(field["id"]), fact["value"]):
            add("task.fixture_value_mismatch", f"Source fixture {fact['subject']}/{fact['field']} must equal {fact['value']!r}", fact)

    bound_field = resolved_field_locator(resolved_fields)
    bindings = {row["requirement_ref"]: set(row["semantic_refs"]) for row in document.get("requirement_bindings", [])}
    resolved_transitions = []
    for transition in contract["transitions"]:
        refs = bindings.get(transition["requirement_id"], set())
        commands = [row for row in document.get("commands", [])
                    if _literal_text(transition["command_label"], shown(row), whole=True)
                    and (not refs or "command:" + row["id"] in refs)]
        if len(commands) != 1:
            add("task.transition_command_unresolved", f"Transition {transition['command_label']!r} needs one bound command", transition)
            continue
        command = commands[0]
        resource = resources.get(views.get(command["view_ref"], {}).get("resource_ref"), {})
        field = bound_field(resource, transition["field"])
        resolved_transitions.append((transition, command, resource, field))
        values = command.get("fixed_values") or {}
        if (not field or command["kind"] not in {"update", "transition"}
                or not _same(values.get(field["id"]), transition["to_value"])):
            add("task.transition_effect_mismatch", f"Command {command['id']} must set {transition['field']} to {transition['to_value']!r}", transition)
        if transition["preserve_other_fields"] and field and (
                set(values) - {field["id"]} or set(command.get("input_field_refs", [])) - {field["id"]}):
            add("task.transition_preservation_mismatch", f"Command {command['id']} can change fields outside the admitted effect", transition)
        if field and not any(_same(row.get(field["id"]), transition["from_value"])
                             for row in resource.get("records", [])):
            add("task.transition_precondition_unreachable", "No fixture reaches the source-owned transition precondition", transition)
    for transition, command, resource, field in resolved_transitions:
        if not field:
            continue
        # The current ABI has a stored-record lock, not a command eligibility
        # predicate. A conditional required-input guard allows mutations when
        # its condition is false and therefore cannot prove this obligation.
        condition = resource.get("read_only_when")
        if not condition:
            add("task.transition_precondition_unenforced",
                f"Command {command['id']} needs an executable stored-state precondition; "
                "a reachable fixture or command.guard is not eligibility policy", transition)
            continue
        if (condition.get("field_ref") != field["id"]
                or condition.get("operator") != "not_equals"
                or not _same(condition.get("value"), transition["from_value"])):
            # Required editor fields do not make persisted drafts non-null.
            # Locking only the target value is not the inverse of eligibility.
            add("task.transition_precondition_mismatch",
                f"Command {command['id']} must be blocked outside its admitted from_value, "
                "including null/draft and other states", transition)
            continue
        mutable_commands = [row for row in document.get("commands", [])
            if row["kind"] in {"update", "transition", "delete"}
            and views.get(row["view_ref"], {}).get("resource_ref") == resource.get("id")]
        compatible_ids = {owned["id"] for other, owned, target, target_field in resolved_transitions
            if target.get("id") == resource.get("id") and target_field
            and target_field["id"] == field["id"]
            and _same(other["from_value"], transition["from_value"])}
        if any(row["id"] not in compatible_ids for row in mutable_commands):
            add("task.transition_precondition_scope_mismatch",
                "Resource-wide locking cannot prove preservation of other mutable commands "
                "without the same source-owned precondition. Refine the supported candidate "
                "or report the unsupported command-policy granularity; do not invent a guard", transition)
    def subject_record(subject):
        known = list(resolved_subjects.get(subject, {}).values())
        if len(known)==1:
            return known[0]
        exact = [(resource,record) for resource in resources.values() for record in resource.get('records', [])
                 if str(record.get('id'))==subject]
        return exact[0] if not known and len(exact)==1 else (None,None)

    for constraint in effective_constraints(contract):
        kind, expected = constraint["kind"], constraint["values"]
        if kind in KINDS:
            issue=candidate_issue(constraint,views=views,resources=resources,previous=previous,
                shown=shown,matches=_matches,literal_matches=_literal_text,bound_field=bound_field,
                bindings=document.get('requirement_bindings',[]))
            if issue:add(*issue,constraint)
        elif kind == 'source_record_table':
            from .source_obligations import source_table_field_scope
            scope = source_table_field_scope(contract, constraint)
            targets = [target for target in resolved_fields if (target['fact']['subject'],
                target['fact'].get('source_field', target['fact']['field'])) in scope]
            if not scope or scope != {(target['fact']['subject'], target['fact'].get('source_field',
                    target['fact']['field'])) for target in targets}:
                add('task.source_table_scope_unresolved', 'The exact source table obligation needs all original source-field identities',
                    constraint, owner='task_interpretation', gate='G3')
            for target in targets:
                if not any(view.get('role')=='collection' and view.get('presentation')=='table'
                    and view.get('resource_ref')==target['resource']['id']
                    and target['field']['id'] in view.get('field_refs',[]) for view in views.values()):
                    add('task.source_table_field_not_visible','Every field in this instruction\'s frozen source scope needs a table projection',constraint)
                    break
        elif kind == 'record_association':
            left, source_record = subject_record(constraint['subject'])
            right, target_record = subject_record(expected[0])
            if left is None or right is None:
                add('task.association_subject_unresolved','The source-owned record association needs two uniquely identified records',constraint)
                continue
            def linked(edge):
                if edge['cardinality']=='one_to_many':
                    a,af,b,bf=edge['to_resource_ref'],edge['to_field_ref'],edge['from_resource_ref'],edge['from_field_ref']
                else:
                    a,af,b,bf=edge['from_resource_ref'],edge['from_field_ref'],edge['to_resource_ref'],edge['to_field_ref']
                if a==left['id'] and b==right['id']:
                    av,bv=source_record.get(af),target_record.get(bf)
                elif a==right['id'] and b==left['id']:
                    av,bv=target_record.get(af),source_record.get(bf)
                else:return False
                # A missing/null endpoint is never evidence of the requested link.
                return av is not None and bv is not None and _same(av,bv)
            def linked_via_explicit_record():
                # Supported M:N authoring uses a link resource with scalar FKs,
                # not a direct many_to_many edge. Do not confuse a common parent
                # of two records with a join record linking those records.
                edges=[]
                for edge in document.get('relationships', []):
                    if edge['cardinality']=='one_to_many':
                        edges.append((edge['to_resource_ref'],edge['to_field_ref'],edge['from_resource_ref'],edge['from_field_ref']))
                    else:
                        edges.append((edge['from_resource_ref'],edge['from_field_ref'],edge['to_resource_ref'],edge['to_field_ref']))
                for middle in resources.values():
                    for item in middle.get('records', []):
                        def reaches(resource, record):
                            return any(a==middle['id'] and b==resource['id'] and item.get(af) is not None
                                and record.get(bf) is not None and _same(item[af],record[bf]) for a,af,b,bf in edges)
                        if reaches(left,source_record) and reaches(right,target_record):
                            return True
                return False
            if not any(linked(edge) for edge in document.get('relationships', [])) and not linked_via_explicit_record():
                add('task.record_association_mismatch',f"Source record {constraint['subject']!r} must link to {expected[0]!r}",constraint)
        elif kind == 'field_display_meaning':
            resource, record = subject_record(constraint['subject'])
            field = bound_field(resource,constraint['source_field'],constraint['subject']) if resource else None
            labels = shown(field or {})
            from .timestamp_labels import classify_caption
            classified = [classify_caption(label) for label in labels]
            if not field or any(row.get('meaning') not in (None,constraint['meaning']) for row in classified):
                add('task.field_display_meaning_mismatch',f"The displayed field must identify {expected!r}, not {constraint.get('excluded_values',[])!r}",constraint)
            elif not classified or any(row['status'] != 'recognized' for row in classified):
                add('task.field_display_meaning_unresolved','The caption is outside the declared timestamp grammar or mixes/negates meanings. '
                    'This is a display-grammar review obligation, not a proven incorrect candidate; do not send it for automatic paid repair.',
                    constraint,owner='display_grammar')
            elif not any(view.get('resource_ref')==resource['id'] and field['id'] in view.get('field_refs', []) for view in views.values()):
                add('task.field_display_meaning_not_visible','The source-owned field meaning has no visible field projection',constraint)
        elif kind == "forbid_external" and document.get("capability_surfaces"):
            add("task.external_dependency_forbidden", "This source forbids external surfaces and dependencies", constraint)
        elif kind == "section_title":
            names = [text for view in views.values() for text in shown(view.get("section") or {})]
            ids = {view['section']['id'] for view in views.values() if view.get('section') and any(
                _literal_text(value, shown(view['section']), whole=_DISPLAY_MATCH[kind]) for value in expected)}
            inconsistent = any((view.get('section') or {}).get('id') in ids and not any(
                _literal_text(value, shown(view['section']), whole=_DISPLAY_MATCH[kind]) for value in expected)
                for view in views.values())
            if inconsistent or any(not _literal_text(value, names, whole=_DISPLAY_MATCH[kind]) for value in expected):
                add("task.section_title_mismatch", f"Required section titles are {expected!r}", constraint)
        elif kind == 'visible_text':
            eligible = [view for view in views.values() if not constraint['subject'] or _matches(constraint['subject'],
                scope_names(view) + scope_names(view.get('section') or {}))]
            # Application metadata titles/comments are not displayed content.
            # Labels on actual views and sections are eligible; runtime checks
            # still own their initial visibility, contrast and readability.
            names = [text for view in eligible for text in shown(view) + shown(view.get('section') or {})]
            if any(not _literal_text(value, names, whole=_DISPLAY_MATCH[kind]) for value in expected):
                add('task.disclosure_text_missing', 'Required explanatory text has no UI label surface: ' + repr(expected), constraint)
        elif kind == "locale":
            if set(constraint.get('locale_codes', expected)) - set(document.get("title", {})):
                add("task.locale_missing", f"Requested locales {expected!r} are incomplete", constraint)
        elif kind == "search_fields":
            collections = [view for view in views.values() if view.get("role") == "collection"
                           and (not constraint["subject"] or _matches(constraint["subject"],
                               scope_names(view) + scope_names(view.get('section') or {})
                               + scope_names(resources.get(view["resource_ref"], {}))))]
            queries = [(view, query) for view in collections for query in view.get("query_controls", [])
                       if query["kind"] == "search"]
            if len(queries) != 1:
                add("task.query_scope_unresolved", "Exact search scope needs one identified collection search", constraint)
                continue
            view, query = queries[0]
            fields = [_field(resources[view["resource_ref"]], value) for value in expected]
            actual = query.get("search_field_refs") or ([query["field_ref"]] if query.get("field_ref") else [])
            if any(field is None for field in fields) or set(actual) != {field["id"] for field in fields if field}:
                add("task.query_scope_mismatch", f"Search must be restricted to source fields {expected!r}", constraint)
    return findings


def task_acceptance_receipt(document, brief, *, previous=None):
    contract = (brief or {}).get("task_contract")
    if not contract:
        return None
    findings = task_candidate_findings(document, brief, previous=previous)
    from .source_literals import contract_literal_coverage
    from .task_interpretation import _attribute_literal
    _, source_coverage = contract_literal_coverage(contract,literal_matches=_attribute_literal)
    pending = [{"family": row["verification_family"], "requirement_ref": row["id"],
                "status": "not_run", "owner": "independent_runtime_acceptance"}
               for row in contract["requirements"] if row["status"] == "active"
               and row["kind"] not in {"authoring", "policy", "exclusion", "verification"}]
    return {"schema": "adaos.builder.task_acceptance_receipt.v1", "contract_digest": digest(contract),
            "candidate_digest": digest(document), "static_status": "failed" if findings else "passed",
            "findings": findings, "source_literal_coverage":source_coverage,
            "runtime_checks": pending, "status": "runtime_acceptance_pending"}
