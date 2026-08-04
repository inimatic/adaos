"""Runtime specialization store and design-time promotion boundary for NLU Teacher."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from adaos.services.agent_context import AgentContext, get_ctx


_LOCK = threading.RLock()
_OVERLAY_SCHEMA = "nlu.teacher_overlay_store.v1.schema.json"
_PROMOTION_SCHEMA = "nlu.teacher_promotion_candidate.v1.schema.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(_schema(name)).iter_errors(dict(value)),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        path = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise ValueError(f"{name} validation failed at {path}: {errors[0].message}")


def _validate_store(value: Mapping[str, Any]) -> None:
    _validate(_OVERLAY_SCHEMA, value)
    for candidate in value.get("promotion_candidates") or []:
        if isinstance(candidate, Mapping):
            _validate(_PROMOTION_SCHEMA, candidate)


def overlay_store_path(ctx: AgentContext | None = None) -> Path:
    context = ctx or get_ctx()
    return (Path(context.paths.state_dir()) / "interpreter" / "nlu_teacher_overlays.json").resolve()


def _empty() -> dict[str, Any]:
    return {
        "schema": "adaos.nlu.teacher_overlay_store.v1",
        "revision": 0,
        "updated_at": None,
        "examples": [],
        "promotion_candidates": [],
    }


def read_store(ctx: AgentContext | None = None) -> dict[str, Any]:
    path = overlay_store_path(ctx)
    with _LOCK:
        if not path.is_file():
            return _empty()
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("NLU Teacher overlay store must contain an object")
        _validate_store(value)
        return copy.deepcopy(dict(value))


def _write(ctx: AgentContext | None, value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    record["revision"] = int(record.get("revision") or 0) + 1
    record["updated_at"] = _now()
    _validate_store(record)
    path = overlay_store_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return record


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{prefix}.{digest[:24]}"


def upsert_example_overlay(
    *,
    target: Mapping[str, Any],
    intent: str,
    text: str,
    slots: Mapping[str, Any] | None = None,
    action: Mapping[str, Any] | None = None,
    promotion: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    privacy: Mapping[str, Any] | None = None,
    ctx: AgentContext | None = None,
) -> dict[str, Any]:
    normalized_target = {
        "type": str(target.get("type") or "").strip(),
        "id": str(target.get("id") or "").strip(),
    }
    token = str(text or "").strip()
    intent_id = str(intent or "").strip()
    if not normalized_target["type"] or not normalized_target["id"] or not intent_id or not token:
        raise ValueError("target type/id, intent, and text are required")
    identity = {"target": normalized_target, "intent": intent_id, "text": token}
    overlay_id = _stable_id("overlay", identity)
    timestamp = _now()
    with _LOCK:
        store = read_store(ctx)
        examples = [dict(item) for item in store.get("examples") or [] if isinstance(item, Mapping)]
        existing = next((item for item in examples if item.get("overlay_id") == overlay_id), None)
        record = {
            "overlay_id": overlay_id,
            "target": normalized_target,
            "intent": intent_id,
            "text": token,
            "slots": copy.deepcopy(dict(slots or {})),
            "action": copy.deepcopy(dict(action)) if isinstance(action, Mapping) else None,
            "status": "active",
            "promotion": copy.deepcopy(dict(promotion or {})),
            "provenance": copy.deepcopy(dict(provenance or {})),
            "privacy": copy.deepcopy(dict(privacy or {})),
            "created_at": str((existing or {}).get("created_at") or timestamp),
            "updated_at": timestamp,
        }
        examples = [item for item in examples if item.get("overlay_id") != overlay_id]
        examples.append(record)
        store["examples"] = examples[-10000:]
        _write(ctx, store)
    return copy.deepcopy(record)


def list_example_overlays(ctx: AgentContext | None = None) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(dict(item))
        for item in read_store(ctx).get("examples") or []
        if isinstance(item, Mapping) and item.get("status") == "active"
    ]


def create_promotion_candidate(
    overlay: Mapping[str, Any],
    *,
    ctx: AgentContext | None = None,
) -> dict[str, Any] | None:
    target = dict(overlay.get("target") or {})
    if target.get("type") not in {"skill", "scenario"} or not target.get("id"):
        return None
    candidate_id = _stable_id("promotion", {"overlay_id": overlay.get("overlay_id"), "target": target})
    timestamp = _now()
    candidate = {
        "schema": "adaos.nlu.teacher_promotion_candidate.v1",
        "candidate_id": candidate_id,
        "source_overlay_id": str(overlay.get("overlay_id") or ""),
        "state": "promotion_candidate",
        "target": {
            "type": target["type"],
            "id": target["id"],
            "package_manifest": "conversational/manifest.yaml",
            "source_file": "conversational/examples.yaml",
        },
        "package_patch": {
            "operation": "upsert_example",
            "value": {
                "id": f"teacher.{str(overlay.get('overlay_id') or '').replace('.', '_')}",
                "intent_id": overlay.get("intent"),
                "text": overlay.get("text"),
                "locale": "und",
                "source": "teacher_candidate",
                "entities": [],
                "provenance": copy.deepcopy(dict(overlay.get("provenance") or {})),
            },
        },
        "builder_change": {
            "change_id": f"nlu-{candidate_id}",
            "object_type": target["type"],
            "object_id": target["id"],
            "request": f"Review and promote NLU Teacher example for intent {overlay.get('intent')}",
            "allowed_paths": ["conversational/examples.yaml", "conversational/tests/stories"],
            "acceptance_criteria": [
                "The conversational package validator passes.",
                "Deterministic stories cover the promoted example and repair behavior.",
                "No runtime-private provenance is published without review.",
            ],
            "evidence_refs": [f"nlu-teacher-overlay:{overlay.get('overlay_id')}"],
        },
        "validation": {"status": "pending", "report_digest": None, "diagnostics": []},
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    _validate(_PROMOTION_SCHEMA, candidate)
    with _LOCK:
        store = read_store(ctx)
        candidates = [
            dict(item)
            for item in store.get("promotion_candidates") or []
            if isinstance(item, Mapping) and item.get("candidate_id") != candidate_id
        ]
        candidates.append(candidate)
        store["promotion_candidates"] = candidates[-5000:]
        _write(ctx, store)
    return copy.deepcopy(candidate)


def create_regex_promotion_candidate(
    *,
    rule: Mapping[str, Any],
    target: Mapping[str, Any],
    webspace_id: str,
    ctx: AgentContext | None = None,
) -> dict[str, Any] | None:
    target_type = str(target.get("type") or "").strip()
    target_id = str(target.get("id") or "").strip()
    if target_type not in {"skill", "scenario"} or not target_id:
        return None
    rule_id = str(rule.get("id") or "").strip()
    source_overlay_id = f"yjs:{webspace_id}:regex:{rule_id}"
    candidate_id = _stable_id(
        "promotion",
        {"source_overlay_id": source_overlay_id, "target": {"type": target_type, "id": target_id}},
    )
    timestamp = _now()
    provenance = copy.deepcopy(dict(rule.get("provenance") or {}))
    candidate = {
        "schema": "adaos.nlu.teacher_promotion_candidate.v1",
        "candidate_id": candidate_id,
        "source_overlay_id": source_overlay_id,
        "state": "promotion_candidate",
        "target": {
            "type": target_type,
            "id": target_id,
            "package_manifest": "conversational/manifest.yaml",
            "source_file": "conversational/matchers.yaml",
        },
        "package_patch": {
            "operation": "upsert_matcher",
            "value": {
                "id": f"teacher.{rule_id.replace('.', '_')}",
                "kind": "regex",
                "intent_id": rule.get("intent"),
                "locale": "und",
                "pattern": rule.get("pattern"),
                "flags": ["ignore_case", "unicode"],
                "slots": copy.deepcopy(dict(rule.get("slots") or {})),
                "source": "teacher_candidate",
                "provenance": provenance,
            },
        },
        "builder_change": {
            "change_id": f"nlu-{candidate_id}",
            "object_type": target_type,
            "object_id": target_id,
            "request": f"Review and promote NLU Teacher matcher for intent {rule.get('intent')}",
            "allowed_paths": [
                "conversational/matchers.yaml",
                "conversational/examples.yaml",
                "conversational/tests/stories",
            ],
            "acceptance_criteria": [
                "The matcher compiles and the conversational package validator passes.",
                "Deterministic stories prove positive, hard-negative, and repair behavior.",
                "The runtime overlay remains rollbackable until a package release is admitted.",
            ],
            "evidence_refs": [source_overlay_id],
        },
        "validation": {"status": "pending", "report_digest": None, "diagnostics": []},
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    _validate(_PROMOTION_SCHEMA, candidate)
    with _LOCK:
        store = read_store(ctx)
        candidates = [
            dict(item)
            for item in store.get("promotion_candidates") or []
            if isinstance(item, Mapping) and item.get("candidate_id") != candidate_id
        ]
        candidates.append(candidate)
        store["promotion_candidates"] = candidates[-5000:]
        _write(ctx, store)
    return copy.deepcopy(candidate)


__all__ = [
    "create_promotion_candidate",
    "create_regex_promotion_candidate",
    "list_example_overlays",
    "overlay_store_path",
    "read_store",
    "upsert_example_overlay",
]
