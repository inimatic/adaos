"""Lossless lexical record-attribute inventory, not a prose interpreter.

The bounded grammar recognizes colon-headed record tuples with simple field /
scalar attributes separated by commas. Quote contents and decimal punctuation
are protected. Meaning/negation still belongs to interpretation: coverage of a
fact-annotated clause cannot certify all free-text source semantics. Recognized
tuples outside that scope require meaning resolution, not silent omission or
automatic promotion to positive fixture data.
"""

from __future__ import annotations

import re
import json

from adaos.services.builder_context import canonical, digest
from .task_spec import seal, source_clauses, verify_identity
from .source_operators import record_head_operator

MAX_SOURCE_CODEPOINTS = 262144
MAX_INVENTORY_ATOMS = 4096
MAX_INVENTORY_BYTES = 2097152

_QUOTES = {'"': '"', "'": "'", "«": "»", "“": "”", "`": "`"}
_ATTRIBUTE = re.compile(
    r"(?P<field>[\w.-]+)(?:\s*[:=—–]\s*|\s+)(?P<literal>.+)", re.UNICODE
)


def _context_failure(code, detail):
    from .task_interpretation import TaskInterpretationRejected

    raise TaskInterpretationRejected(
        [
            {
                "code": "task.source_literal_" + code,
                "gate": "G5",
                "owner": "context_compilation",
                "detail": detail,
            }
        ]
    )


def _parts(text, separators):
    """Yield exact offsets, splitting only outside quotes and numeric commas."""
    start = 0
    quote = None
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in _QUOTES and (index == 0 or not text[index - 1].isalnum()):
            quote = _QUOTES[char]
            continue
        if char not in separators:
            continue
        if (
            char in ".,"
            and index
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        ):
            continue
        if (
            char == "."
            and index
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        ):
            continue
        yield start, index
        start = index + 1
    yield start, len(text)


def _scalar(text):
    if not text:
        return False
    if text[0] in _QUOTES:
        escaped = False
        for index, char in enumerate(text[1:], 1):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == _QUOTES[text[0]]:
                return index == len(text) - 1
        return False
    if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?\s*%?", text):
        return True
    return bool(re.fullmatch(r'[^\s,;"«»“”`]+', text))


def scalar_literal_matches(raw, expected):
    """One scalar grammar shared by attribute and ordered-relation grounding."""
    raw = raw.strip().rstrip(',;').strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            raw = json.loads(raw)
        except ValueError:
            return False
    elif len(raw) >= 2 and (raw[0], raw[-1]) in {('«','»'),('“','”'),("'","'"),('`','`')}:
        raw = raw[1:-1]
    if isinstance(expected,str):return raw == expected
    if expected is None:return raw.casefold() in {'null','unknown','неизвестно'}
    if isinstance(expected,bool):return raw.casefold() == str(expected).lower()
    number = re.fullmatch(r'([-+]?\d+(?:[.,]\d+)?)\s*%?',raw)
    return bool(number) and float(number[1].replace(',','.')) == expected


def source_literal_inventory(statement, source_digest):
    """Lower an already verified source without fabricating a TaskSpec."""
    return _inventory(statement,source_digest,source_clauses(statement))


def literal_inventory(task):
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    return _inventory(
        task["source"]["statement"], task["source"]["digest"], task["clauses"]
    )


def _inventory(statement, source_digest, clause_view):
    if len(statement) > MAX_SOURCE_CODEPOINTS:
        _context_failure(
            "capacity",
            "Source literal inventory exceeds its declared parser capacity; nothing was truncated",
        )
    clauses = source_clauses(statement)
    actual = [
        {key: row.get(key) for key in ("id", "start", "end", "text")}
        for row in clause_view
    ]
    expected = [
        {key: row[key] for key in ("id", "start", "end", "text")} for row in clauses
    ]
    if actual != expected:
        _context_failure(
            "source_projection_invalid",
            "Core clause offsets/text do not reproduce the exact source",
        )
    atoms = []
    unsupported = []
    for clause in clauses:
        text = clause["text"]
        for begin, end in _parts(text, ".!?;\n"):
            segment = text[begin:end]
            colon = segment.find(":")
            if colon < 0:
                continue
            header = segment[:colon]
            operator = record_head_operator(header, source_start=clause['start'] + begin)
            subject = re.search(r"([\w.-]+)\s*$", header, re.UNICODE)
            if not subject:
                continue
            attributes = []
            body = segment[colon + 1 :]
            for left, right in _parts(body, ","):
                raw = body[left:right]
                phrase = raw.strip()
                match = _ATTRIBUTE.fullmatch(phrase)
                if not match or not _scalar(match["literal"].strip()):
                    attributes = []
                    break
                offset = (
                    clause["start"]
                    + begin
                    + colon
                    + 1
                    + left
                    + len(raw)
                    - len(raw.lstrip())
                )
                attributes.append(
                    {
                        "kind": "record_attribute_literal",
                        "clause_id": clause["id"],
                        "subject": subject[1],
                        "source_field": match["field"],
                        "evidence": phrase,
                        "start": offset,
                        "end": offset + len(phrase),
                        **({'source_head_operator':operator} if operator else {}),
                    }
                )
            if not attributes:
                unsupported.append(
                    {
                        "clause_id": clause["id"],
                        "start": clause["start"] + begin,
                        "end": clause["start"] + end,
                        "status": "outside_supported_tuple_grammar",
                    }
                )
                continue
            for attribute in attributes:
                if (
                    statement[attribute["start"] : attribute["end"]]
                    != attribute["evidence"]
                ):
                    _context_failure(
                        "source_projection_invalid",
                        "Literal evidence offsets do not match the exact source",
                    )
                if len(atoms) >= MAX_INVENTORY_ATOMS:
                    _context_failure(
                        "capacity",
                        "Source literal inventory exceeds its declared atom capacity; nothing was truncated",
                    )
                atoms.append(
                    {
                        "id": "source-literal:"
                        + digest({"source": source_digest, **attribute})[7:31],
                        **attribute,
                    }
                )
    inventory = seal(
        {
            "schema": "adaos.builder.source_literal_inventory.v1",
            "source_digest": source_digest,
            "offset_unit": "unicode_codepoint",
            "grammar": "colon_record_tuple_v1",
            "authority": "lexical_evidence_not_added_product_requirements",
            "atoms": atoms,
            "unsupported_segments": unsupported,
            "scope_limit": "Record-attribute syntax only. Neither role classification nor general prose completeness is certified.",
        },
        id_key="inventory_ref",
        prefix="source-literals:",
    )
    if len(canonical(inventory).encode("utf-8")) > MAX_INVENTORY_BYTES:
        _context_failure(
            "capacity",
            "Source literal inventory exceeds its declared evidence capacity; nothing was truncated",
        )
    return inventory


def literal_coverage(task, proposal, *, literal_matches):
    return _coverage(literal_inventory(task), proposal, literal_matches=literal_matches)


def contract_literal_coverage(contract, *, literal_matches):
    """Recheck persisted meaning at compilation, not only new interpretation.

    Lower verified Intent evidence directly; do not fabricate a partial TaskSpec
    or silently treat an old admitted bit as today's source-recall proof.
    """
    source = contract.get("source") or {}
    try:
        verify_identity(source, id_key="intent_id", prefix="intent:")
        statement, source_digest = source["statement"], source["digest"]
    except (KeyError, TypeError, ValueError, AttributeError):
        _context_failure(
            "source_binding_invalid",
            "Persisted task source evidence cannot be verified",
        )
    inventory = _inventory(statement, source_digest, source_clauses(statement))
    return _coverage(inventory, contract, literal_matches=literal_matches)


def _coverage(inventory, proposal, *, literal_matches):
    fact_clauses = {
        row["clause_id"] for row in proposal["clause_roles"] if "fact" in row["roles"]
    }
    polarity_unresolved = [atom for atom in inventory['atoms'] if atom.get('source_head_operator')]
    checked = [atom for atom in inventory["atoms"] if atom["clause_id"] in fact_clauses
               and not atom.get('source_head_operator')]
    unclassified = [
        atom["id"]
        for atom in inventory["atoms"]
        if atom["clause_id"] not in fact_clauses
        and not atom.get('source_head_operator')
    ]
    findings = []
    for atom in checked:
        matches = [
            row
            for row in proposal["facts"]
            if atom["clause_id"] in row["clause_ids"]
            and row["subject"] == atom["subject"]
            and row["source_field"] == atom["source_field"]
            and literal_matches(atom["evidence"], row["source_field"], row["value"])
        ]
        if not matches:
            findings.append(
                {
                    "code": "task.source_record_attribute_unextracted",
                    "gate": "G3",
                    "owner": "task_interpretation",
                    "source_atom_ref": atom["id"],
                    "source_clause_ids": [atom["clause_id"]],
                    "detail": "An explicit source record attribute is missing from grounded facts. "
                    "This is missing extraction, not missing user knowledge.",
                }
            )
    missing_count = len(findings)
    for atom in inventory["atoms"]:
        if atom["clause_id"] in fact_clauses or atom.get('source_head_operator'):
            continue
        findings.append(
            {
                "code": "task.source_record_role_unresolved",
                "gate": "G3",
                "owner": "task_interpretation",
                "source_atom_ref": atom["id"],
                "source_clause_ids": [atom["clause_id"]],
                "detail": "A recognized record-like source fragment has no admitted fact meaning. "
                "Resolve its product, policy or verification role before generation; "
                "do not silently omit it or create a positive fixture from punctuation alone.",
            }
        )
    findings.extend(source_operator_findings(inventory))
    receipt = {
        "schema": "adaos.builder.source_literal_coverage.v1",
        "inventory_ref": inventory["inventory_ref"],
        "inventory_digest": inventory["digest"],
        "scope": "record_tuples_in_fact_annotated_clauses",
        "checked_atoms": len(checked),
        "covered_atoms": len(checked) - missing_count,
        "unclassified_atom_refs": unclassified,
        "polarity_unresolved_atom_refs": [atom['id'] for atom in polarity_unresolved],
        "unsupported_segments": inventory["unsupported_segments"],
        "complete_for_checked_atoms": bool(checked) and not missing_count,
        "recognized_atoms_accounted_for": not findings,
        "general_source_completeness_proven": False,
    }
    return findings, receipt


def source_operator_findings(inventory):
    return [
        {
            'code':'task.source_record_polarity_unresolved',
            'gate':'G3', 'owner':'task_interpretation',
            'source_atom_ref':atom['id'], 'source_clause_ids':[atom['clause_id']],
            'source_grammar_ref':atom['source_head_operator']['grammar_ref'],
            'detail':'A source record is under a negative imperative header. The current source grammar '
                'cannot certify it as positive initial data or resolve its non-fact disposition. '
                'Do not invent a fixture or ask again for its supplied values; resolve source meaning first.',
        }
        for atom in inventory['atoms'] if atom.get('source_head_operator')
    ]


def source_preflight_findings(task):
    """Known unsupported source grammar must stop before paid interpretation."""
    return source_operator_findings(literal_inventory(task))
