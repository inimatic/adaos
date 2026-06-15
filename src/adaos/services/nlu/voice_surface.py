from __future__ import annotations

import json
import re
from typing import Any, Mapping

from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings


VOICE_CAPABILITY_BINDING_INTENT = "voice.capability.activate"

_ALLOWED_ACTIVATION_TYPES = {
    "desktop.open_modal",
    "ui.state.set",
    "ui.focus_widget",
    "ui.affordance.activate",
}


def normalize_voice_text(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def exact_phrase_pattern(text: str) -> str:
    tokens = [token for token in re.split(r"\s+", str(text or "").strip()) if token]
    if not tokens:
        return r"^\s*$"
    return r"^\s*" + r"\s+".join(re.escape(token) for token in tokens) + r"\s*$"


def encode_activation_plan(plan: Any) -> str:
    if isinstance(plan, str):
        return plan
    return json.dumps(plan if isinstance(plan, list) else [], ensure_ascii=False, separators=(",", ":"))


def decode_activation_plan(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in iter_mappings(value)]
    if isinstance(value, str) and value.strip():
        try:
            payload = json.loads(value)
        except Exception:
            return []
        if isinstance(payload, list):
            return [dict(item) for item in iter_mappings(payload)]
    return []


def collect_voice_surface(context: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    root_mcp = coerce_dict(context.get("root_mcp"))
    authoring = coerce_dict(root_mcp.get("nlu_authoring_context"))
    action_surface = coerce_dict(authoring.get("action_surface"))
    if not action_surface:
        action_surface = coerce_dict(context.get("action_surface"))

    capabilities = [dict(item) for item in iter_mappings(action_surface.get("voice_capabilities"))]
    affordances = [dict(item) for item in iter_mappings(action_surface.get("voice_affordances"))]

    voice_surface = coerce_dict(action_surface.get("voice_surface"))
    if not capabilities:
        capabilities = [dict(item) for item in iter_mappings(voice_surface.get("voice_capabilities"))]
    if not affordances:
        affordances = [dict(item) for item in iter_mappings(voice_surface.get("voice_affordances"))]
    return {"voice_capabilities": capabilities, "voice_affordances": affordances}


def _iter_label_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_label_values(item))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iter_label_values(item))
        return out
    return []


def _row_terms(row: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("title", "name", "description"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
    terms.extend(_iter_label_values(row.get("labels")))
    terms.extend(_iter_label_values(row.get("aliases")))
    row_id = row.get("id")
    if isinstance(row_id, str) and row_id.strip():
        terms.append(row_id.replace(".", " ").replace("_", " ").replace("-", " "))
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        norm = normalize_voice_text(term)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(term.strip())
    return out


def _score_row(text_norm: str, row: Mapping[str, Any]) -> tuple[int, str]:
    best_score = 0
    best_term = ""
    for term in _row_terms(row):
        term_norm = normalize_voice_text(term)
        if not term_norm:
            continue
        score = 0
        if text_norm == term_norm:
            score = 200 + len(term_norm)
        elif len(term_norm) >= 4 and re.search(rf"(?<!\w){re.escape(term_norm)}(?!\w)", text_norm, re.UNICODE):
            score = 150 + len(term_norm)
        else:
            words = [word for word in term_norm.split() if len(word) >= 4]
            if words and all(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text_norm, re.UNICODE) for word in words):
                score = 90 + len(" ".join(words))
        if score > best_score:
            best_score = score
            best_term = term
    return best_score, best_term


def _step_key(step: Mapping[str, Any]) -> str:
    return json.dumps(
        {"type": step.get("type"), "params": coerce_dict(step.get("params"))},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def expand_activation_plan(row: Mapping[str, Any], affordances: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    affordance_by_id = {str(item.get("id") or "").strip(): item for item in affordances if str(item.get("id") or "").strip()}
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_step(step: Mapping[str, Any]) -> None:
        step_type = str(step.get("type") or "").strip()
        params = coerce_dict(step.get("params"))
        if not step_type:
            return
        normalized = {"type": step_type, "params": params}
        key = _step_key(normalized)
        if key in seen:
            return
        seen.add(key)
        expanded.append(normalized)

    for step in iter_mappings(row.get("activation")):
        step_type = str(step.get("type") or "").strip()
        params = coerce_dict(step.get("params"))
        if step_type == "ui.affordance.activate":
            affordance_id = str(params.get("affordance_id") or "").strip()
            affordance = affordance_by_id.get(affordance_id)
            if affordance:
                for nested in iter_mappings(affordance.get("activation")):
                    append_step(nested)
                continue
        append_step(step)
    return expanded


def find_voice_surface_match(context: Mapping[str, Any], text: str) -> dict[str, Any] | None:
    text_norm = normalize_voice_text(text)
    if not text_norm:
        return None
    surface = collect_voice_surface(context)
    affordances = surface["voice_affordances"]
    best: tuple[int, str, str, dict[str, Any]] | None = None
    for collection, rows in (("voice_capability", surface["voice_capabilities"]), ("voice_affordance", affordances)):
        for row in rows:
            score, term = _score_row(text_norm, row)
            if score <= 0:
                continue
            if collection == "voice_capability":
                score += 20
            if best is None or score > best[0]:
                best = (score, term, collection, row)
    if best is None or best[0] < 80:
        return None
    score, term, collection, row = best
    activation_plan = expand_activation_plan(row, affordances)
    if not activation_plan:
        return None
    result: dict[str, Any] = {
        "collection": collection,
        "surface": dict(row),
        "activation_plan": activation_plan,
        "side_effect_class": str(row.get("side_effect_class") or "ui_navigation").strip() or "ui_navigation",
        "verify": coerce_dict(row.get("verify")),
        "fingerprint": row.get("fingerprint"),
        "match": {"score": score, "term": term, "normalized_text": text_norm},
    }
    if collection == "voice_capability":
        result["capability"] = dict(row)
        result["capability_id"] = row.get("id")
        for step in iter_mappings(row.get("activation")):
            if str(step.get("type") or "").strip() == "ui.affordance.activate":
                affordance_id = str(coerce_dict(step.get("params")).get("affordance_id") or "").strip()
                if affordance_id:
                    result["affordance_id"] = affordance_id
                    affordance = next((item for item in affordances if item.get("id") == affordance_id), None)
                    if affordance:
                        result["affordance"] = dict(affordance)
                    break
    else:
        result["affordance"] = dict(row)
        result["affordance_id"] = row.get("id")
    return result


def validate_activation_plan(plan: Any) -> list[dict[str, Any]]:
    steps = decode_activation_plan(plan)
    checks: list[dict[str, Any]] = []
    checks.append({"name": "activation_plan", "ok": bool(steps), "status": "present" if steps else "missing"})
    for index, step in enumerate(steps):
        step_type = str(step.get("type") or "").strip()
        params = coerce_dict(step.get("params"))
        ok_type = step_type in _ALLOWED_ACTIVATION_TYPES
        checks.append(
            {
                "name": f"activation_step[{index}].type",
                "ok": ok_type,
                "status": "allowed" if ok_type else "unsupported",
                "type": step_type,
            }
        )
        if step_type == "desktop.open_modal":
            checks.append(
                {
                    "name": f"activation_step[{index}].modal_id",
                    "ok": bool(str(params.get("modal_id") or "").strip()),
                    "status": "present" if str(params.get("modal_id") or "").strip() else "missing",
                }
            )
        if step_type == "ui.state.set":
            checks.append(
                {
                    "name": f"activation_step[{index}].state_key",
                    "ok": bool(str(params.get("key") or "").strip()),
                    "status": "present" if str(params.get("key") or "").strip() else "missing",
                }
            )
        if step_type == "ui.focus_widget":
            checks.append(
                {
                    "name": f"activation_step[{index}].widget_id",
                    "ok": bool(str(params.get("widget_id") or "").strip()),
                    "status": "present" if str(params.get("widget_id") or "").strip() else "missing",
                }
            )
    return checks
