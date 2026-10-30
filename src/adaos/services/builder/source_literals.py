"""Lossless lexical record-attribute inventory, not a prose interpreter.

The bounded grammar recognizes colon-headed record tuples with simple field /
scalar attributes separated by commas. Quote contents and decimal punctuation
are protected. Meaning/negation still belongs to interpretation: coverage of a
fact-annotated clause cannot certify all free-text source semantics.
"""

from __future__ import annotations

import re

from adaos.services.builder_context import canonical, digest
from .task_spec import seal, source_clauses, verify_identity

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


def literal_inventory(task):
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    statement = task["source"]["statement"]
    if len(statement) > MAX_SOURCE_CODEPOINTS:
        _context_failure(
            "capacity",
            "Source literal inventory exceeds its declared parser capacity; nothing was truncated",
        )
    clauses = source_clauses(statement)
    actual = [
        {key: row.get(key) for key in ("id", "start", "end", "text")}
        for row in task["clauses"]
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
                        + digest({"source": task["source"]["digest"], **attribute})[
                            7:31
                        ],
                        **attribute,
                    }
                )
    inventory = seal(
        {
            "schema": "adaos.builder.source_literal_inventory.v1",
            "source_digest": task["source"]["digest"],
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
    inventory = literal_inventory(task)
    fact_clauses = {
        row["clause_id"] for row in proposal["clause_roles"] if "fact" in row["roles"]
    }
    checked = [atom for atom in inventory["atoms"] if atom["clause_id"] in fact_clauses]
    unclassified = [
        atom["id"]
        for atom in inventory["atoms"]
        if atom["clause_id"] not in fact_clauses
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
    receipt = {
        "schema": "adaos.builder.source_literal_coverage.v1",
        "inventory_ref": inventory["inventory_ref"],
        "inventory_digest": inventory["digest"],
        "scope": "record_tuples_in_fact_annotated_clauses",
        "checked_atoms": len(checked),
        "covered_atoms": len(checked) - len(findings),
        "unclassified_atom_refs": unclassified,
        "unsupported_segments": inventory["unsupported_segments"],
        "complete_for_checked_atoms": bool(checked) and not findings,
        "general_source_completeness_proven": False,
    }
    return findings, receipt
