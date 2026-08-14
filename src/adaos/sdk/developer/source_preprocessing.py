from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence


_WORD_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)
_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")
_METRIC_RE = re.compile(
    r"(?i)(?:acc(?:uracy)?|loss|precision|recall|f1|auc|seed|epoch|mean|std|delta|"
    r"metric|score|ошиб|точност|потер|эпох|сид|средн).{0,160}?[-+]?\d+(?:[.,]\d+)?"
)
_RESEARCH_TERMS = {
    "ablation", "accuracy", "analysis", "augmentation", "baseline", "benchmark",
    "class", "classifier", "comparison", "config", "criterion", "data", "dataset",
    "epoch", "evaluate", "evaluation", "experiment", "hypothesis", "implementation",
    "loss", "metric", "model", "network", "pool", "random", "result", "seed", "split",
    "test", "train", "validation", "torch", "tlp", "maxpool", "stl", "cifar",
    "анализ", "гипотез", "данн", "исследован", "метрик", "модел", "обучен", "результат",
    "сравнен", "точност", "эксперимент",
}
_STOP_WORDS = {
    "about", "after", "also", "and", "are", "but", "can", "for", "from", "have", "into",
    "its", "not", "one", "only", "that", "the", "their", "then", "this", "through", "use",
    "using", "was", "were", "what", "when", "where", "which", "with", "would",
    "без", "был", "быть", "вам", "весь", "для", "его", "если", "есть", "еще", "как", "она",
    "они", "при", "так", "также", "того", "только", "уже", "что", "это", "этот",
}


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _WORD_RE.findall(str(value or ""))
        if token.casefold() not in _STOP_WORDS
    }


def _compact_prose(value: str) -> str:
    lines = [_SPACE_RE.sub(" ", line).rstrip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    return _BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def _source_segment(lines: Sequence[str], node: ast.AST) -> str:
    start = max(0, int(getattr(node, "lineno", 1) or 1) - 1)
    end = max(start + 1, int(getattr(node, "end_lineno", start + 1) or (start + 1)))
    return "".join(lines[start:end]).strip()


def _python_summary(source: str, *, query_tokens: set[str], maximum: int = 7_000) -> tuple[str, dict[str, Any]]:
    """Produce a deterministic semantic digest while retaining relevant exact code windows."""

    normalized = str(source or "").replace("\r\n", "\n")
    lines = normalized.splitlines(keepends=True)
    try:
        tree = ast.parse(normalized)
    except SyntaxError:
        compact = _compact_prose(normalized)
        return compact[:maximum], {"language": "python", "parsed": False, "compacted": len(compact) > maximum}

    imports: list[str] = []
    symbols: list[str] = []
    assignments: list[str] = []
    relevant_ranges: list[tuple[int, int]] = []
    domain_tokens = query_tokens | _RESEARCH_TERMS
    for node in tree.body:
        segment = _source_segment(lines, node)
        lowered = _tokens(segment)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(segment)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            header = segment.splitlines()[0].rstrip(":")
            symbols.append(header[:300])
            if lowered & domain_tokens or len(segment) <= 3_500:
                relevant_ranges.append((int(node.lineno), int(getattr(node, "end_lineno", node.lineno))))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            try:
                value = ast.literal_eval(node.value) if getattr(node, "value", None) is not None else None
                rendered = repr(value)
            except (ValueError, TypeError, SyntaxError):
                rendered = ""
            if rendered and len(rendered) <= 320:
                assignments.append(segment[:500])
        elif lowered & domain_tokens:
            relevant_ranges.append((int(getattr(node, "lineno", 1)), int(getattr(node, "end_lineno", getattr(node, "lineno", 1)))))

    for number, line in enumerate(lines, start=1):
        if _tokens(line) & domain_tokens:
            relevant_ranges.append((max(1, number - 2), min(len(lines), number + 2)))

    merged: list[tuple[int, int]] = []
    for start, end in sorted(relevant_ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    parts = ["Python semantic digest (exact source snippets remain exploratory input):"]
    if imports:
        parts.append("Imports: " + "; ".join(dict.fromkeys(imports))[:1200])
    if symbols:
        parts.append("Definitions:\n- " + "\n- ".join(dict.fromkeys(symbols))[:1800])
    if assignments:
        parts.append("Literal configuration:\n" + "\n".join(dict.fromkeys(assignments))[:1800])
    if len(normalized) <= 3_500:
        parts.append("Source:\n" + normalized.strip())
    else:
        snippets: list[str] = []
        for start, end in merged:
            snippet = "".join(lines[start - 1:end]).strip()
            if snippet:
                snippets.append(f"lines {start}-{end}:\n{snippet}")
            if sum(len(item) for item in snippets) >= 4_500:
                break
        if snippets:
            parts.append("Relevant source windows:\n" + "\n\n".join(snippets))
    result = "\n\n".join(parts).strip()
    return result[:maximum], {
        "language": "python",
        "parsed": True,
        "imports": len(imports),
        "definitions": len(symbols),
        "literal_assignments": len(assignments),
        "source_characters": len(normalized),
        "digest_characters": min(len(result), maximum),
        "compacted": len(normalized) > len(result[:maximum]),
    }


def _output_text(output: Mapping[str, Any]) -> Iterable[str]:
    output_type = str(output.get("output_type") or "unknown")
    if output_type == "error":
        yield f"error {output.get('ename') or ''}: {output.get('evalue') or ''}".strip()
    value = output.get("text")
    if isinstance(value, list):
        yield "".join(str(item) for item in value)
    elif isinstance(value, str):
        yield value
    data = output.get("data")
    if isinstance(data, Mapping):
        for media in ("text/markdown", "text/plain"):
            value = data.get(media)
            if isinstance(value, list):
                yield "".join(str(item) for item in value)
            elif isinstance(value, str):
                yield value


def _outputs_digest(outputs: Sequence[Any], *, maximum: int = 1_200) -> tuple[str, dict[str, Any]]:
    candidates: list[str] = []
    payload_characters = 0
    binary_items = 0
    for output in outputs:
        if not isinstance(output, Mapping):
            continue
        data = output.get("data")
        if isinstance(data, Mapping):
            binary_items += sum(1 for key in data if str(key).startswith("image/") or str(key) in {"application/pdf"})
        for raw in _output_text(output):
            payload_characters += len(raw)
            compact = _compact_prose(raw)
            if not compact:
                continue
            metric_lines = [line for line in compact.splitlines() if _METRIC_RE.search(line)]
            selected = metric_lines[:20] if metric_lines else compact.splitlines()[:6]
            candidates.extend(
                line[:500]
                for line in selected
                if line.strip() and (len(set(line.strip())) >= 6 or bool(_METRIC_RE.search(line)))
            )
    unique = list(dict.fromkeys(candidates))
    digest = "\n".join(unique)[:maximum]
    return digest, {
        "output_items": len(outputs),
        "text_payload_characters": payload_characters,
        "binary_items_omitted": binary_items,
        "summary_characters": len(digest),
    }


def prepare_notebook_units(
    text: str,
    artifact_ref: str,
    *,
    query: str = "",
    include_exploratory_outputs: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Turn notebook JSON into compact, ranked, provenance-addressable semantic units."""

    try:
        notebook = json.loads(text)
    except ValueError as exc:
        raise ValueError("notebook artifact is not valid JSON") from exc
    if not isinstance(notebook, Mapping):
        raise ValueError("notebook artifact must contain a JSON object")
    cells = notebook.get("cells") or []
    if not isinstance(cells, list):
        raise ValueError("notebook cells must be an array")

    query_tokens = _tokens(query)
    units: list[dict[str, Any]] = []
    cell_types: Counter[str] = Counter()
    output_items = 0
    summarized_outputs = 0
    source_characters = 0
    digest_characters = 0
    languages: Counter[str] = Counter()
    previous_code: list[tuple[int, str, set[str]]] = []
    kernel = notebook.get("metadata") if isinstance(notebook.get("metadata"), Mapping) else {}
    kernelspec = kernel.get("kernelspec") if isinstance(kernel.get("kernelspec"), Mapping) else {}
    language_info = kernel.get("language_info") if isinstance(kernel.get("language_info"), Mapping) else {}

    for index, cell in enumerate(cells):
        if not isinstance(cell, Mapping):
            continue
        cell_type = str(cell.get("cell_type") or "unknown")
        cell_types[cell_type] += 1
        source = cell.get("source") or ""
        raw_body = "".join(str(item) for item in source) if isinstance(source, list) else str(source)
        source_characters += len(raw_body)
        outputs = cell.get("outputs") or []
        if not isinstance(outputs, list):
            outputs = []
        output_items += len(outputs)
        output_digest, output_meta = _outputs_digest(outputs)
        if output_digest:
            summarized_outputs += len(outputs)

        if cell_type == "markdown":
            body = _compact_prose(raw_body)
            detail = {"language": "markdown", "source_characters": len(raw_body), "digest_characters": len(body), "compacted": len(body) < len(raw_body)}
        elif cell_type == "code":
            declared_language = str(language_info.get("name") or kernelspec.get("language") or "python").casefold()
            languages[declared_language] += 1
            if declared_language in {"python", "python3", ""}:
                body, detail = _python_summary(raw_body, query_tokens=query_tokens)
            else:
                body = _compact_prose(raw_body)[:7_000]
                detail = {"language": declared_language, "source_characters": len(raw_body), "digest_characters": len(body), "compacted": len(body) < len(raw_body)}
            current_tokens = _tokens(raw_body)
            closest: tuple[int, str, set[str]] | None = None
            similarity = 0.0
            if len(raw_body) > 3_500 and current_tokens:
                for prior in previous_code[-12:]:
                    union = current_tokens | prior[2]
                    score = len(current_tokens & prior[2]) / len(union) if union else 0.0
                    if score > similarity:
                        closest, similarity = prior, score
            if closest is not None and similarity >= 0.82:
                diff_lines = list(
                    difflib.unified_diff(
                        closest[1].splitlines(),
                        raw_body.splitlines(),
                        fromfile=f"cell-{closest[0]}",
                        tofile=f"cell-{index}",
                        n=1,
                        lineterm="",
                    )
                )
                structural = body.split("Relevant source windows:", 1)[0].strip()[:2_800]
                delta = "\n".join(diff_lines)[:3_600]
                body = (
                    f"Near-duplicate experiment variant of cell {closest[0]} (token similarity={similarity:.3f}).\n"
                    f"{structural}\n\nExact source delta:\n{delta or '(no textual delta after normalization)'}"
                )[:6_500]
                detail = {**detail, "near_duplicate_of": closest[0], "token_similarity": round(similarity, 4), "compacted": True}
            previous_code.append((index, raw_body, current_tokens))
        else:
            body = _compact_prose(raw_body)[:7_000]
            detail = {"language": cell_type, "source_characters": len(raw_body), "digest_characters": len(body), "compacted": len(body) < len(raw_body)}

        if include_exploratory_outputs and output_digest:
            body = (body + "\n\nExploratory output summary (historical, untrusted, not confirmatory evidence):\n" + output_digest).strip()
        if not body:
            continue
        tokens = _tokens(body)
        overlap = len(tokens & query_tokens)
        domain_overlap = len(tokens & _RESEARCH_TERMS)
        heading_bonus = 4 if cell_type == "markdown" and raw_body.lstrip().startswith("#") else 0
        definition_bonus = min(5, int(detail.get("definitions") or 0))
        output_bonus = 2 if output_digest else 0
        relevance = overlap * 12 + domain_overlap * 2 + heading_bonus + definition_bonus + output_bonus
        if index == 0:
            relevance += 3
        digest_characters += len(body)
        units.append(
            {
                "id": f"cell-{index}",
                "ref": f"{artifact_ref}#cell={index}",
                "label": f"cell {index} ({cell_type}, relevance={relevance})",
                "content": body,
                "source_characters": len(raw_body) + int(output_meta["text_payload_characters"]),
                "relevance": relevance,
                "order": index,
                "kind": cell_type,
                "detail": {**detail, "outputs": output_meta},
            }
        )

    inventory = {
        "kind": "jupyter_notebook",
        "nbformat": notebook.get("nbformat"),
        "kernel": str(kernelspec.get("display_name") or kernelspec.get("name") or "unknown"),
        "cells": len(cells),
        "cell_types": dict(cell_types),
        "source_cells": len(units),
        "source_characters": source_characters,
        "semantic_digest_characters": digest_characters,
        "output_items": output_items,
        "summarized_output_items": summarized_outputs,
        "outputs_classification": "exploratory_untrusted_not_confirmatory",
        "languages": dict(languages),
    }
    inventory_text = "Notebook structural inventory:\n" + json.dumps(inventory, ensure_ascii=False, sort_keys=True)
    units.insert(
        0,
        {
            "id": "inventory",
            "ref": f"{artifact_ref}#inventory",
            "label": "notebook inventory",
            "content": inventory_text,
            "source_characters": 0,
            "relevance": 10_000,
            "order": -1,
            "kind": "inventory",
            "detail": {"generated": True},
        },
    )
    return units, inventory


def select_units(units: Sequence[Mapping[str, Any]], *, max_characters: int, relevance_first: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select complete semantic units by relevance, then render them in source order."""

    maximum = max(1, int(max_characters))
    ranked = list(units)
    if relevance_first:
        ranked.sort(key=lambda item: (-int(item.get("relevance") or 0), int(item.get("order") or 0)))
    selected: list[dict[str, Any]] = []
    remaining = maximum
    for unit in ranked:
        if remaining <= 0:
            break
        content = str(unit.get("content") or "")
        if not content:
            continue
        # Prefer another complete unit over a very large low-value tail. The
        # first selected unit may still be truncated so tiny budgets are useful.
        if len(content) > remaining and selected:
            continue
        accepted = content[:remaining]
        selected.append(
            {
                **dict(unit),
                "label": unit.get("label") or unit.get("id") or "source unit",
                "content": accepted,
                "selected_characters": len(accepted),
                "source_characters": int(unit.get("source_characters") or len(content)),
                "truncated": len(accepted) < len(content),
            }
        )
        remaining -= len(accepted)
    selected.sort(key=lambda item: int(item.get("order") or 0))
    selected_ids = {str(item.get("id")) for item in selected}
    return selected, {
        "selection_strategy": "query_relevance_then_source_order" if relevance_first else "source_order",
        "selected_unit_ids": [str(item.get("id")) for item in selected],
        "omitted_unit_ids": [str(item.get("id")) for item in units if str(item.get("id")) not in selected_ids],
        "query_aware": bool(relevance_first),
    }


def query_digest(query: str) -> str | None:
    normalized = str(query or "").strip()
    if not normalized:
        return None
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = ["prepare_notebook_units", "query_digest", "select_units"]
