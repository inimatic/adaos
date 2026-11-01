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


def _record_subject(header):
    """Bounded IDs with one EN/RU relation qualifier, never last-related-ID wins.

    This does not infer arbitrary noun-phrase ownership. Ambiguous qualified
    heads are retained as unresolved evidence, not promoted to fixture facts.
    """
    visible = list(header)
    quote, escaped = None, False
    for index, char in enumerate(header):
        if quote:
            visible[index] = ' '
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in _QUOTES and (index == 0 or not header[index - 1].isalnum()):
            quote = _QUOTES[char]
            visible[index] = ' '
    unquoted = ''.join(visible)
    qualifiers = [m for m in re.finditer(r'(?<![\w.-])(?:for|для)\s+', unquoted, re.I)
                  if unquoted[:m.start()].strip()]
    if not qualifiers:
        subject = re.search(r'([\w.-]+)\s*$', header, re.UNICODE)
        return (subject[1] if subject else None), False
    if len(qualifiers) == 1:
        qualifier = qualifiers[0]
        subject = re.search(r'([\w.-]+)\s*$', unquoted[:qualifier.start()])
        related = re.search(r'([\w.-]+)\s*$', unquoted[qualifier.end():])
        if subject and related:
            identifier = subject[1]
            if any(char.isdigit() for char in identifier) or re.fullmatch(r'\w+(?:[-.]\w+)+', identifier):
                return identifier, False
    return None, True


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
        # One explicit ownership header may introduce comma-separated fields
        # after a semicolon instead of a colon. Keep every member and its exact
        # source span; never infer the subject from the last related entity.
        from .source_obligations import association_heads
        for begin, end in _parts(text, '.!?\n'):
            sentence = text[begin:end]
            heads = association_heads(sentence)
            separator = sentence.find(';')
            if len(heads)!=1 or separator < heads[0].end() or ':' in sentence[:separator]:
                continue
            head = heads[0]
            operator = record_head_operator(sentence[:separator], source_start=clause['start']+begin)
            for left, right in _parts(sentence[separator+1:], ',;'):
                raw = sentence[separator+1+left:separator+1+right]
                phrase = raw.strip()
                match = _ATTRIBUTE.fullmatch(phrase)
                if not match or not _scalar(match['literal'].strip()):
                    unsupported.append({'clause_id':clause['id'],'start':clause['start']+begin+separator+1+left,
                        'end':clause['start']+begin+separator+1+right,'status':'outside_supported_prose_attribute_grammar'})
                    continue
                offset = clause['start']+begin+separator+1+left+len(raw)-len(raw.lstrip())
                atom = {'kind':'record_attribute_literal','clause_id':clause['id'],'subject':head['subject'],
                    'source_field':match['field'],'evidence':phrase,'start':offset,'end':offset+len(phrase),
                    **({'source_head_operator':operator} if operator else {})}
                atom['id'] = 'source-literal:' + digest({'source':source_digest, **atom})[7:31]
                atoms.append(atom)
        for begin, end in _parts(text, ".!?;\n"):
            segment = text[begin:end]
            colon = segment.find(":")
            if colon < 0:
                continue
            header = segment[:colon]
            operator = record_head_operator(header, source_start=clause['start'] + begin)
            subject, ambiguous_subject = _record_subject(header)
            if not subject and not ambiguous_subject:
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
                        "subject": subject,
                        "source_field": match["field"],
                        "evidence": phrase,
                        "start": offset,
                        "end": offset + len(phrase),
                        **({'source_head_operator':operator} if operator else {}),
                    }
                )
            if not attributes or ambiguous_subject:
                unsupported.append(
                    {
                        "clause_id": clause["id"],
                        "start": clause["start"] + begin,
                        "end": clause["start"] + end,
                        "status": "record_subject_unresolved" if ambiguous_subject else "outside_supported_tuple_grammar",
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
    if len(atoms) > MAX_INVENTORY_ATOMS:
        _context_failure('capacity','Source literal inventory exceeds its declared atom capacity; nothing was truncated')
    if any(statement[row['start']:row['end']] != row['evidence'] for row in atoms):
        _context_failure('source_projection_invalid','Prose attribute offsets do not reproduce the exact source')
    inventory = seal(
        {
            "schema": "adaos.builder.source_literal_inventory.v1",
            "source_digest": source_digest,
            "offset_unit": "unicode_codepoint",
            "grammar": "colon_and_qualified_semicolon_records_v3",
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
    findings.extend(source_subject_findings(inventory))
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
    inventory = literal_inventory(task)
    from .source_obligations import inventory as obligations
    pending = obligations(task['source']['statement'],task['source']['digest'])['unresolved']
    return source_operator_findings(inventory) + source_subject_findings(inventory) + [
        {'code':'task.source_obligation_scope_unresolved','gate':'G3','owner':'context_compilation',
         'source_clause_ids':row['clause_ids'],'source_expectation_ref':row['source_expectation_ref'],
         'detail':row['reason']} for row in pending]


def source_subject_findings(inventory):
    return [{'code': 'task.source_record_subject_unresolved' if row['status']=='record_subject_unresolved' else 'task.source_record_attribute_scope_unresolved', 'gate': 'G3',
             'owner': 'context_compilation', 'source_clause_ids': [row['clause_id']],
             'source_start': row['start'], 'source_end': row['end'],
             'detail': 'A qualified record header is outside the supported explicit-ID grammar. '
                       'Do not assign its attributes to the last related entity or buy another interpretation.'}
            for row in inventory['unsupported_segments'] if row['status'] in {'record_subject_unresolved','outside_supported_prose_attribute_grammar'}]
