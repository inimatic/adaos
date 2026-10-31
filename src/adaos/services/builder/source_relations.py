"""Bounded source-owned command/effect relations, not general prose meaning.

Direction, explicit naming and preservation are relations: finding their words
somewhere in a clause is insufficient. Exact spans and declared grammar own the
checks. Unknown scopes/history remain unproved; source syntax grants no effect.
"""

from __future__ import annotations

import json
import re

from adaos.services.builder_context import canonical, digest
from .source_literals import (
    _parts,
    _QUOTES,
    scalar_literal_matches,
    source_literal_inventory,
)
from .source_operators import record_head_operator
from .task_spec import source_clauses

GRAMMAR_REF = "source-grammar:command-scalar-relations-v1"
_QUOTED = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|«[^»]*»|“[^”]*”|`[^`]*`)"""
_FIELD = rf"(?:{_QUOTED}|[\w.-]+)"
_VALUE = rf"(?:{_QUOTED}|[-+]?\d+(?:[.,]\d+)?|[\w./:@+-]+)"
_DIRECTIONS = (
    re.compile(
        rf"(?<![\w.])(?P<field>{_FIELD})\s+from\s+(?P<before>{_VALUE})\s+to\s+(?P<after>{_VALUE})(?![\w.-])",
        re.I,
    ),
    re.compile(
        rf"\b(?:меняет|изменяет)\s+(?P<field>{_FIELD})\s+(?P<before>{_VALUE})\s+на\s+(?P<after>{_VALUE})(?![\w.-])",
        re.I,
    ),
)
_COMMANDS = (
    re.compile(rf"\bкоманда\s+(?P<label>{_QUOTED}|[\w-]+)", re.I),
    re.compile(rf"(?P<label>{_QUOTED}|[\w-]+)\s+command\b", re.I),
    re.compile(rf"\bcommand\s+(?:(?:named|called)\s+)?(?P<label>{_QUOTED})", re.I),
)
_EN_HEAD = re.compile(
    r"(?:provide|add|create|include|offer)\s+(?:(?:a|an|the)\s+)?", re.I
)
_ONLY_FIELD = re.compile(r"\b(?:only\s+this\s+field|только\s+это\s+поле)\b", re.I)
_OTHER_FIELDS = re.compile(
    r"\b(?:preserve|keep)\s+(?:(?:all|the)\s+)*other\s+fields\b|\b(?:сохрани|сохранить|сохраняй)\s+(?:все\s+)?остальные\s+поля\b",
    re.I,
)
_PRESERVE_HEAD = re.compile(
    r"^\s*(?:preserve|keep|сохрани|сохранить|сохраняй)\s+(?:(?:its|the|его|её|ее)\s+)?",
    re.I,
)


def _label(text):
    if len(text) > 1 and text[0] in _QUOTES and text[-1] == _QUOTES[text[0]]:
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError:
                pass
        return text[1:-1]
    return text


def _name(text):
    return re.sub(r"\s+", " ", _label(text)).strip().casefold()


def _unquoted(text):
    result, quote, escaped = [True] * len(text), None, False
    for index, char in enumerate(text):
        if quote:
            result[index] = False
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in _QUOTES and (index == 0 or not text[index - 1].isalnum()):
            quote = _QUOTES[char]
    return result


def transition_inventory(statement, source_digest):
    literals = source_literal_inventory(statement, source_digest)
    fields = sorted(
        {_name(row["source_field"]) for row in literals["atoms"]},
        key=lambda name: (-len(name), name),
    )
    field_pattern = (
        "(?:" + "|".join(re.escape(name) for name in fields) + ")" if fields else None
    )
    named_preservation, atoms = [], []
    for clause in source_clauses(statement):
        for start, end in _parts(clause["text"], ".!?;\n"):
            text = clause["text"][start:end]
            base = clause["start"] + start
            outside = _unquoted(text)
            negative = record_head_operator(text, source_start=base)
            commands = []
            for grammar in _COMMANDS:
                for match in grammar.finditer(text):
                    if not outside[match.start()]:
                        continue
                    raw = match["label"]
                    if raw[0] not in _QUOTES and not raw[0].isupper():
                        continue
                    prefix = text[: match.start()]
                    commands.append(
                        {
                            "label": _label(raw),
                            "start": base + match.start(),
                            "end": base + match.end(),
                            "declared_head": not prefix.strip()
                            or bool(_EN_HEAD.fullmatch(prefix.strip() + " ")),
                        }
                    )
            commands.sort(key=lambda row: row["start"])
            head = _PRESERVE_HEAD.match(text)
            if head and not negative and field_pattern:
                cursor = head.end()
                preserved = set()
                while True:
                    field = re.match(
                        rf"(?P<field>{field_pattern})(?![\w.-])", text[cursor:], re.I
                    )
                    if not field:
                        break
                    preserved.add(_name(field["field"]))
                    cursor += field.end()
                    separator = re.match(
                        r"\s*(?:,\s*(?:(?:and|и)\s+)?|(?:and|и)\s+)",
                        text[cursor:],
                        re.I,
                    )
                    if not separator:
                        break
                    cursor += separator.end()
                if preserved:
                    named_preservation.append(
                        {
                            "fields": preserved,
                            "clause_id": clause["id"],
                            "start": base + head.start(),
                            "end": base + cursor,
                        }
                    )
            for grammar in _DIRECTIONS:
                for match in grammar.finditer(text):
                    quoted = not outside[match.start()]
                    prior = [
                        row
                        for row in commands
                        if row["end"] <= base + match.start("field")
                    ]
                    command = prior[-1] if prior else None
                    preservation = [
                        {"start": base + m.start(), "end": base + m.end()}
                        for rule in (_ONLY_FIELD, _OTHER_FIELDS)
                        for m in rule.finditer(text)
                        if outside[m.start()] and not negative
                    ]
                    atoms.append(
                        {
                            "grammar_ref": GRAMMAR_REF,
                            "clause_id": clause["id"],
                            "start": base + match.start(),
                            "end": base + match.end(),
                            "field": match["field"],
                            "before": match["before"],
                            "after": match["after"],
                            "command": command,
                            "quoted_data": quoted,
                            "negative_head": bool(negative),
                            "preserve_other_fields": bool(preservation),
                            "preservation_evidence": preservation,
                        }
                    )
    known_commands = {
        _name(row["command"]["label"])
        for row in atoms
        if row["command"] and not row["quoted_data"]
    }
    if len(known_commands) == 1:
        for atom in atoms:
            others = set(fields) - {_name(atom["field"])}
            evidence = [
                row for row in named_preservation if others and others <= row["fields"]
            ]
            if evidence and not atom["quoted_data"] and not atom["negative_head"]:
                atom["preserve_other_fields"] = True
                atom["preservation_evidence"] += [
                    {key: row[key] for key in ("start", "end")} for row in evidence
                ]
    for atom in atoms:
        atom["id"] = (
            "source-relation:" + digest({"source": source_digest, **atom})[7:31]
        )
    result = {
        "schema": "adaos.builder.source_relations.v1",
        "source_digest": source_digest,
        "grammar_ref": GRAMMAR_REF,
        "atoms": atoms,
        "general_source_completeness_proven": False,
    }
    if len(atoms) > 4096 or len(canonical(result).encode("utf-8")) > 2097152:
        from .task_interpretation import TaskInterpretationRejected

        raise TaskInterpretationRejected(
            [
                {
                    "code": "task.source_relations_capacity",
                    "gate": "G5",
                    "owner": "context_compilation",
                    "detail": "The complete source relation inventory exceeds its declared capacity; no truncation is allowed",
                }
            ]
        )
    return result


def transition_relation_findings(
    statement,
    source_digest,
    transitions,
    *,
    allowed_source_digests=None,
    check_coverage=True,
):
    inventory = transition_inventory(statement, source_digest)
    allowed = set(allowed_source_digests or [source_digest])
    findings, unchecked, active = [], [], []

    def add(code, atom, row=None):
        findings.append(
            {
                "code": "task.source_transition_" + code,
                "gate": "G3",
                "owner": "task_interpretation",
                "source_relation_ref": atom["id"],
                "source_clause_ids": [atom["clause_id"]],
                "source_grammar_ref": GRAMMAR_REF,
                **({"requirement_ref": row["requirement_id"]} if row else {}),
                "detail": "The proposed transition does not preserve its source-owned command, ordered values, preservation or role. "
                "Resolve the relational meaning; the supplied source values are not missing.",
            }
        )

    for row in transitions:
        if row.get("source_digest", source_digest) not in allowed:
            unchecked.append(row.get("assertion_ref") or row["requirement_id"])
            continue
        active.append(row)
        related = [
            atom
            for atom in inventory["atoms"]
            if atom["clause_id"] in row["clause_ids"]
            and _name(atom["field"]) == _name(row["field"])
        ]
        if not related:
            continue
        executable = [
            atom
            for atom in related
            if not atom["quoted_data"] and not atom["negative_head"]
        ]
        if not executable:
            add("scope_unresolved", related[0], row)
            continue
        named = [
            atom
            for atom in executable
            if atom["command"]
            and _name(atom["command"]["label"]) == _name(row["command_label"])
        ]
        if named:
            executable = named
        elif all(atom["command"] for atom in executable):
            add("command_mismatch", executable[0], row)
            continue
        matching = [
            atom
            for atom in executable
            if scalar_literal_matches(atom["before"], row["from_value"])
            and scalar_literal_matches(atom["after"], row["to_value"])
        ]
        if not matching:
            add("direction_mismatch", executable[0], row)
        elif (
            any(atom["preserve_other_fields"] for atom in matching)
            and not row["preserve_other_fields"]
        ):
            add("preservation_weakened", matching[0], row)
    if check_coverage:
        for atom in inventory["atoms"]:
            command = atom["command"]
            if (
                atom["quoted_data"]
                or atom["negative_head"]
                or not command
                or not command["declared_head"]
            ):
                continue
            if not any(
                atom["clause_id"] in row["clause_ids"]
                and _name(row["command_label"]) == _name(command["label"])
                and _name(row["field"]) == _name(atom["field"])
                for row in active
            ):
                add("relation_unextracted", atom)
    return findings, {
        "schema": "adaos.builder.source_relation_coverage.v1",
        "source_digest": source_digest,
        "grammar_ref": GRAMMAR_REF,
        "recognized_relations": len(inventory["atoms"]),
        "unverified_historical_transition_refs": unchecked,
        "recognized_slots_consistent": not findings,
        "general_source_completeness_proven": False,
    }


def contract_relation_findings(contract):
    source = contract["source"]
    allowed = {source["digest"]}
    derivation = contract.get("derivation") or {}
    if derivation.get("kind") == "exact_source_replay":
        allowed.update(
            value
            for key in ("prior_source_digest", "meaning_source_digest")
            if isinstance((value := derivation.get(key)), str)
        )
    return transition_relation_findings(
        source["statement"],
        source["digest"],
        contract.get("transitions", []),
        allowed_source_digests=allowed,
    )
