"""Declared lexical source operators, not general semantic interpretation.

An unquoted negative imperative at the beginning of a record header cannot
certify positive initial data. Complex scopes/non-fact dispositions remain
unresolved; domain verbs, entity names and model role labels are not the rule.
"""

from __future__ import annotations

import re

GRAMMAR_REF = "source-grammar:negative-record-head-v1"
CLAUSE_GRAMMAR_REF = "source-grammar:negative-clause-head-v1"
_NEGATIVE_HEAD = re.compile(
    r"^[ \t]*(?P<operator>do[ \t]+not|don['’]t|never|не|нельзя)(?=[ \t]+\S)",
    re.IGNORECASE,
)


def record_head_operator(header, *, source_start):
    match = _NEGATIVE_HEAD.match(header)
    if not match:
        return None
    return {
        "grammar_ref": GRAMMAR_REF,
        "kind": "negative_imperative_head",
        "interpretation_status": "requires_meaning_resolution_not_positive_fixture",
        "text": match["operator"],
        "start": source_start + match.start("operator"),
        "end": source_start + match.end("operator"),
    }


def clause_head_operators(statement):
    """Exact clause-head evidence; no claim to resolve mixed/nested scopes."""
    from .task_spec import source_clauses

    result = []
    for clause in source_clauses(statement):
        marker = record_head_operator(clause['text'], source_start=clause['start'])
        if marker:
            marker.update(grammar_ref=CLAUSE_GRAMMAR_REF,
                          interpretation_status='requires_operation_scope_resolution')
            result.append({'clause_id':clause['id'], 'operator':marker})
    return result


def positive_operation_findings(statement, requirements, *, source_digest=None):
    heads = {row['clause_id']:row['operator'] for row in clause_head_operators(statement)}
    findings = []
    for row in requirements:
        if row.get('operation') is None:
            continue
        if source_digest is not None and (
            row.get('status')!='active' or row.get('source_digest')!=source_digest
        ):
            continue  # Clause ids are source-local; never reinterpret old history against today's text.
        affected = [ref for ref in row['clause_ids'] if ref in heads]
        if affected:
            findings.append({
                'code':'task.source_operation_polarity_unresolved','gate':'G3','owner':'task_interpretation',
                'source_clause_ids':affected,'source_grammar_ref':CLAUSE_GRAMMAR_REF,'requirement_ref':row['id'],
                'detail':'A positive product operation is attributed to a negative-headed source clause. '
                    'Its negation/condition scope must be resolved, not removed by the proposed operation label.',
            })
    return findings
