"""Independent source-owned value/effect checks over canonical semantic output.

Bindings locate an implementation; they never supply the expected answer.
Browser, cancellation and persistence checks remain explicit runtime obligations.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from adaos.services.builder_context import digest


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
    values = [field for field in resource.get('fields', []) if (
        str(name).casefold() in {str(field['id']).casefold(), str(field['id']).rsplit('.', 1)[-1].casefold()}
        or wanted and any(wanted == tokens(label) for label in _display_names(field)))]
    return values[0] if len(values) == 1 else None


def task_candidate_findings(document, brief):
    contract = (brief or {}).get("task_contract")
    if not isinstance(contract, Mapping):
        return []
    findings = []
    resources = {row["id"]: row for row in document.get("resources", [])}
    views = {row["id"]: row for row in document.get("views", [])}
    requested = {locale for row in contract['constraints'] if row['kind'] == 'locale'
        for locale in row.get('locale_codes', [])}
    locale = next(iter(requested)) if len(requested) == 1 else (contract.get('source') or {}).get('locale')
    locale = str(locale or '').split('-')[0]

    def shown(item):
        values = []
        for key in ('title', 'label'):
            value = item.get(key)
            if isinstance(value, Mapping):
                values.extend([str(value[locale])] if locale in value else [])
            elif isinstance(value, str):
                values.append(value)
        return values

    def scope_names(item):
        return [item.get('id', '')] + shown(item)

    def add(code, detail, source):
        findings.append({"code": code, "path": "$", "gate": "G8", "owner": "candidate",
                         "detail": detail, "source_clause_ids": source.get("clause_ids", [])})

    for fact in contract["facts"]:
        eligible = [resource for resource in resources.values() if _field(resource, fact['field'])
                    or _field(resource, fact.get('source_field', fact['field']))]
        matches = [(resource, record) for resource in eligible for record in resource.get("records", [])
                   if any(isinstance(value, str) and value.casefold() == fact["subject"].casefold()
                          for value in record.values())]
        exact = [(resource, record) for resource, record in matches if str(record.get('id', '')) == fact['subject']]
        if exact:
            matches = exact
        if len(matches) != 1:
            add("task.fixture_subject_unresolved", f"Source fixture {fact['subject']!r} needs exactly one record, found {len(matches)}", fact)
            continue
        resource, record = matches[0]
        field = _field(resource, fact["field"]) or _field(resource, fact.get('source_field', fact['field']))
        if not field or not _same(record.get(field["id"]), fact["value"]):
            add("task.fixture_value_mismatch", f"Source fixture {fact['subject']}/{fact['field']} must equal {fact['value']!r}", fact)
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
        field = _field(resource, transition["field"])
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
    for constraint in contract["constraints"]:
        kind, expected = constraint["kind"], constraint["values"]
        if kind == "forbid_external" and document.get("capability_surfaces"):
            add("task.external_dependency_forbidden", "This source forbids external surfaces and dependencies", constraint)
        elif kind == "section_title":
            names = [text for view in views.values() for text in shown(view.get("section") or {})]
            ids = {view['section']['id'] for view in views.values() if view.get('section') and any(
                _literal_text(value, shown(view['section']), whole=True) for value in expected)}
            inconsistent = any((view.get('section') or {}).get('id') in ids and not any(
                _literal_text(value, shown(view['section']), whole=True) for value in expected)
                for view in views.values())
            if inconsistent or any(not _literal_text(value, names, whole=True) for value in expected):
                add("task.section_title_mismatch", f"Required section titles are {expected!r}", constraint)
        elif kind == 'visible_text':
            eligible = [view for view in views.values() if not constraint['subject'] or _matches(constraint['subject'],
                scope_names(view) + scope_names(view.get('section') or {}))]
            # Application metadata titles/comments are not displayed content.
            # Labels on actual views and sections are eligible; runtime checks
            # still own their initial visibility, contrast and readability.
            names = [text for view in eligible for text in shown(view) + shown(view.get('section') or {})]
            if any(not _literal_text(value, names) for value in expected):
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


def task_acceptance_receipt(document, brief):
    contract = (brief or {}).get("task_contract")
    if not contract:
        return None
    findings = task_candidate_findings(document, brief)
    pending = [{"family": row["verification_family"], "requirement_ref": row["id"],
                "status": "not_run", "owner": "independent_runtime_acceptance"}
               for row in contract["requirements"] if row["status"] == "active"
               and row["kind"] not in {"authoring", "policy", "exclusion", "verification"}]
    return {"schema": "adaos.builder.task_acceptance_receipt.v1", "contract_digest": digest(contract),
            "candidate_digest": digest(document), "static_status": "failed" if findings else "passed",
            "findings": findings, "runtime_checks": pending, "status": "runtime_acceptance_pending"}
