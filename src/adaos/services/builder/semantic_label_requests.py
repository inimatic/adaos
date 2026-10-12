"""Conservative, model-free requests over existing semantic labels.

This is deliberately a small language, not a general intent classifier. Unknown
requests remain on the normal route; recognized but incomplete label requests
stop for clarification rather than authorizing a full-document rewrite.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .semantic_graph import reduce_graph
from .workflow import BuilderWorkflowError

_QUOTED = r'(?:"([^"\n]+)"|«([^»\n]+)»|“([^”\n]+)”|\'([^\'\n]+)\')'
_RENAME = re.compile(
    rf'^(?:rename|переименуй|переименовать)\s+'
    rf'(view|screen|field|column|экран|представление|поле|колонку|столбец)\s+'
    rf'{_QUOTED}\s+(?:to|в|на)\s+{_QUOTED}'
    r'(?:\s+(in English|in Russian|на английском|на русском))?\s*[.!]?$', re.I)
_UNDO = re.compile(r'^(?:undo (?:the )?last label change|отмени последнее переименование)\s*[.!]?$', re.I)
_PREFIX = re.compile(r'^(?:rename|переименуй|переименовать)\s+(?:view|screen|field|column|экран|представление|поле|колонку|столбец)\b', re.I)


def parse_request(instruction: str) -> dict[str, Any] | None:
    text = instruction.strip()
    if _UNDO.fullmatch(text):
        return {"kind": "undo"}
    match = _RENAME.fullmatch(text) if len(text) <= 1000 else None
    if not match:
        return {"kind": "clarification", "reason": "explicit_quoted_labels_required"} if _PREFIX.match(text) else None
    kind = "field" if match[1].lower() in {"field", "column", "поле", "колонку", "столбец"} else "view"
    old = next(value for value in match.groups()[1:5] if value is not None)
    new = next(value for value in match.groups()[5:9] if value is not None)
    suffix = (match[10] or "").lower()
    locale = {"in english": "en", "in russian": "ru", "на английском": "en", "на русском": "ru"}.get(suffix)
    if len(old) > 240 or len(new) > 240 or not new.strip():
        return {"kind": "clarification", "reason": "label_length_invalid"}
    return {"kind": "rename", "target_kind": kind, "expected": old, "text": new, "locale": locale}


def plan(document: Mapping[str, Any], parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve exact text to one existing ref AND locale, then validate replay."""
    if parsed["kind"] == "clarification":
        return {"status": "clarification_required", "reason": parsed["reason"], "candidates": []}
    if document.get("schema") != "adaos.webui.semantic.v2":
        raise BuilderWorkflowError("label requests require canonical semantic v2 source")
    labels = []
    if parsed["target_kind"] == "view":
        labels = [(f"view:{view['id']}", view["title"]) for view in document["views"]]
    else:
        for resource in document["resources"]:
            for field in resource["fields"]:
                identity = field["id"] if field["id"].startswith(resource["id"] + ".") else f"{resource['id']}.{field['id']}"
                labels.append((f"field:{identity}", field["label"]))
    matches = [{"ref": ref, "locale": locale} for ref, label in labels for locale in ("en", "ru")
               if (parsed.get("locale") is None or locale == parsed["locale"]) and label.get(locale) == parsed["expected"]]
    if len(matches) != 1:
        return {"status": "clarification_required", "reason": "label_not_found" if not matches else "ambiguous_label", "candidates": matches}
    if parsed["expected"] == parsed["text"]:
        return {"status": "no_change", "reason": "label_already_matches", "candidates": matches}
    op = {"seq": 1, "kind": "label.set", **matches[0], "expected": parsed["expected"], "text": parsed["text"]}
    reduced = reduce_graph(document, [op], write_refs=[op["ref"]])
    return {"status": "planned", "ops": [op], "write_refs": [op["ref"]], "semantic_digest": reduced["semantic_digest"]}
