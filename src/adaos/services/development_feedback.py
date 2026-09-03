from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from adaos.domain import Event
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


DEVELOPMENT_FEEDBACK_SCHEMA = "adaos.development_feedback.v1"
DEVELOPMENT_FEEDBACK_STATE_SCHEMA = "adaos.development_feedback.state.v1"
ACTIVE_STATUSES = {"observed", "triaged", "accepted"}
SOURCES = {
    "pre_codex_llm",
    "codex",
    "validator",
    "builder",
    "human_review",
    "legacy_builder_session",
}
CATEGORIES = {
    "missing_capability",
    "ambiguous_contract",
    "conflicting_contract",
    "inefficient_contract",
    "insufficient_context",
    "observability_gap",
    "validation_gap",
    "policy_block",
    "result_rejected",
}
REJECTION_CLASSES = {
    "requirement_ambiguity",
    "builder_misread_user",
    "sdk_doc_ambiguity",
    "sdk_capability_gap",
    "weak_patch",
    "insufficient_validation",
}
OWNER_ROUTES = {
    "user_clarification",
    "nlu_teacher",
    "builder_retry",
    "sdk_documentation",
    "sdk_examples",
    "sdk_implementation",
    "policy_review",
    "core_ticket",
}
PROMOTION_ROUTES = {"project", "sdk_understanding", "core"}
OWNER_ROUTE_PROMOTIONS = {
    "user_clarification": {"project"},
    "nlu_teacher": {"project"},
    "builder_retry": {"project"},
    "sdk_documentation": {"sdk_understanding"},
    "sdk_examples": {"sdk_understanding"},
    "sdk_implementation": {"core"},
    "policy_review": {"core"},
    "core_ticket": {"core"},
}
QUALIFICATION_ACTOR_PREFIXES = ("human:", "user:", "owner:", "policy:", "admin:")
IMPACTS = {
    "blocker",
    "correctness",
    "reliability",
    "efficiency",
    "generalization",
    "comprehension",
    "observability",
    "policy",
}
TRANSITIONS = {
    "observed": {"triaged", "accepted", "rejected"},
    "triaged": {"accepted", "rejected"},
    "accepted": {"rejected", "promoted"},
    "rejected": {"triaged"},
    "promoted": set(),
}
_LEGACY_CATEGORY = {
    "clarification_required": "ambiguous_contract",
    "feasibility_constraint": "conflicting_contract",
    "capability_gap": "missing_capability",
    "protocol_conflict": "conflicting_contract",
    "runtime_blocker": "validation_gap",
}
_SDK_KINDS = {
    "ambiguous_contract": "sdk_unclear_definition",
    "conflicting_contract": "sdk_application_failure",
    "inefficient_contract": "sdk_generalization_pressure",
    "insufficient_context": "sdk_example_gap",
    "observability_gap": "sdk_observability_gap",
    "validation_gap": "sdk_application_failure",
    "policy_block": "sdk_policy_boundary",
    "missing_capability": "sdk_application_failure",
    "result_rejected": "builder_rejection_learning",
}
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _fingerprint(*parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "devfeedback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dedup_mappings(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _target_scope(target_refs: Sequence[str]) -> tuple[dict[str, Any], str, str]:
    refs = [_text(value) for value in target_refs if _text(value)]
    priority = ("project", "scenario", "skill", "modal", "component", "sdk", "api", "core")
    selected = next(
        (ref for prefix in priority for ref in refs if ref.startswith(f"{prefix}:")),
        refs[0] if refs else "component:unknown",
    )
    kind, _, identifier = selected.partition(":")
    owner_area = kind if kind in {"project", "scenario", "skill", "sdk", "core"} else "project"
    return (
        {"type": kind, "id": identifier, "component_ref": selected, "source": "development_feedback"},
        owner_area,
        selected,
    )


def _first_ref(target_refs: Sequence[str], prefixes: Sequence[str]) -> str:
    return next(
        (
            ref
            for ref in (_text(value) for value in target_refs)
            if any(ref.startswith(f"{prefix}:") for prefix in prefixes)
        ),
        "",
    )


def _routing_choice(record: Mapping[str, Any]) -> tuple[str, str, float, str]:
    category = _text(record.get("category"))
    classification = (
        dict(record.get("classification"))
        if isinstance(record.get("classification"), Mapping)
        else {}
    )
    rejection_class = _text(classification.get("rejection_class"))
    rejection_routes = {
        "requirement_ambiguity": (
            "user_clarification",
            "project",
            0.95,
            "The rejection is classified as an ambiguous user requirement.",
        ),
        "builder_misread_user": (
            "builder_retry",
            "project",
            0.95,
            "The Builder interpretation, rather than the platform contract, was rejected.",
        ),
        "sdk_doc_ambiguity": (
            "sdk_documentation",
            "sdk_understanding",
            0.95,
            "The rejection is qualified as ambiguous SDK documentation.",
        ),
        "sdk_capability_gap": (
            "sdk_implementation",
            "core",
            0.95,
            "The rejection is qualified as a missing SDK or API capability.",
        ),
        "weak_patch": (
            "builder_retry",
            "project",
            0.95,
            "The requested capability exists but the implementation was inadequate.",
        ),
        "insufficient_validation": (
            "builder_retry",
            "project",
            0.95,
            "The implementation requires stronger project validation before acceptance.",
        ),
    }
    if category == "result_rejected" and rejection_class in rejection_routes:
        return rejection_routes[rejection_class]
    routes = {
        "missing_capability": (
            "sdk_implementation",
            "core",
            0.85,
            "A missing public capability requires an SDK or API owner.",
        ),
        "ambiguous_contract": (
            "sdk_documentation",
            "sdk_understanding",
            0.85,
            "An ambiguous public contract should first be clarified by its documentation owner.",
        ),
        "conflicting_contract": (
            "sdk_implementation",
            "core",
            0.85,
            "Conflicting public behavior requires the owning SDK or API implementation boundary.",
        ),
        "inefficient_contract": (
            "sdk_implementation",
            "core",
            0.75,
            "A recurring contract cost requires an SDK or API design decision.",
        ),
        "insufficient_context": (
            "user_clarification",
            "project",
            0.75,
            "The project cannot proceed safely without bounded clarification.",
        ),
        "observability_gap": (
            "sdk_implementation",
            "core",
            0.8,
            "Missing public diagnostics require an SDK or API implementation owner.",
        ),
        "validation_gap": (
            "builder_retry",
            "project",
            0.75,
            "The current project result needs another bounded implementation or validation pass.",
        ),
        "policy_block": (
            "policy_review",
            "core",
            0.9,
            "A policy boundary must be decided by the core policy owner.",
        ),
        "result_rejected": (
            "user_clarification",
            "project",
            0.4,
            "The rejected result has not yet been diagnostically classified.",
        ),
    }
    return routes[category]


def _owner_ref_for(record: Mapping[str, Any], promotion_route: str) -> str:
    prefixes = {
        "project": ("project", "scenario", "skill", "modal", "component"),
        "sdk_understanding": ("sdk", "api", "resource"),
        "core": ("core",),
    }[promotion_route]
    return _first_ref(record.get("target_refs") or [], prefixes)


def _qualification_preview(record: Mapping[str, Any]) -> dict[str, Any]:
    owner_route, promotion_route, confidence, reason = _routing_choice(record)
    owner_ref = _owner_ref_for(record, promotion_route)
    classification = (
        dict(record.get("classification"))
        if isinstance(record.get("classification"), Mapping)
        else {}
    )
    missing_requirements: list[str] = []
    if record.get("category") == "result_rejected" and _text(
        classification.get("rejection_class")
    ) not in REJECTION_CLASSES:
        missing_requirements.append("rejection_class")
    if not owner_ref:
        missing_requirements.append("owner_ref")
    current = (
        dict(classification.get("qualification"))
        if isinstance(classification.get("qualification"), Mapping)
        else None
    )
    result = {
        "schema": "adaos.development_feedback.routing_preview.v1",
        "feedback_id": record.get("feedback_id"),
        "feedback_revision": int(record.get("revision") or 0),
        "authoritative": False,
        "recommended": {
            "owner_route": owner_route,
            "promotion_route": promotion_route,
            "owner_ref": owner_ref or None,
            "confidence": confidence,
            "reason": reason,
        },
        "missing_requirements": missing_requirements,
        "ready_to_qualify": not missing_requirements,
        "current_qualification": current,
    }
    result["digest"] = "sha256:" + hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return result


def _validate_qualification(qualification: Mapping[str, Any]) -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "abi"
        / "development_feedback.qualification.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(qualification),
        key=lambda item: list(item.path),
    )
    if errors:
        raise ValueError(
            f"invalid development feedback qualification: {errors[0].message}"
        )


@dataclass(slots=True)
class DevelopmentFeedbackService:
    """Workspace index for model/developer observations before ticket promotion."""

    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "development_feedback"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".state.lock"

    def capture(
        self,
        *,
        source: str,
        category: str,
        summary: str,
        blocking: bool = False,
        confidence: float = 1.0,
        impact: Sequence[str] = (),
        target_refs: Sequence[str] = (),
        details: str = "",
        recommendation: str = "",
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        relation_refs: Sequence[Mapping[str, Any]] = (),
        classification: Mapping[str, Any] | None = None,
        dedup_key: str | None = None,
        actor: str = "system",
        idempotent_replay: bool = False,
    ) -> dict[str, Any]:
        source_token = _text(source).lower()
        category_token = _text(category).lower()
        text = _text(summary)
        if source_token not in SOURCES:
            raise ValueError(f"unsupported development feedback source: {source_token}")
        if category_token not in CATEGORIES:
            raise ValueError(f"unsupported development feedback category: {category_token}")
        if len(text) < 3:
            raise ValueError("development feedback summary is required")
        if isinstance(classification, Mapping) and "qualification" in classification:
            raise ValueError(
                "development feedback qualification requires the governed qualify operation"
            )
        refs = list(dict.fromkeys(_text(value) for value in target_refs if ":" in _text(value)))[:50]
        impacts = list(
            dict.fromkeys(_text(value).lower() for value in impact if _text(value).lower() in IMPACTS)
        )[:8]
        if blocking and "blocker" not in impacts:
            impacts.insert(0, "blocker")
        key = _text(dedup_key) or _fingerprint(category_token, text.casefold(), refs)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            for record in state["records"].values():
                if record.get("dedup_key") == key and record.get("status") in ACTIVE_STATUSES:
                    incoming_relations = _dedup_mappings(relation_refs)
                    recorded_relation_keys = {
                        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                        for item in _dedup_mappings(record.get("relation_refs") or [])
                    }
                    incoming_relation_keys = {
                        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                        for item in incoming_relations
                    }
                    if idempotent_replay and incoming_relation_keys.issubset(
                        recorded_relation_keys
                    ):
                        return {
                            "ok": True,
                            "duplicate": True,
                            "idempotent_replay": True,
                            "feedback": _clone(record),
                            "event": None,
                        }
                    record["occurrence_count"] = int(record.get("occurrence_count") or 1) + 1
                    record["evidence_refs"] = _dedup_mappings(
                        [*(record.get("evidence_refs") or []), *evidence_refs]
                    )[:100]
                    record["relation_refs"] = _dedup_mappings(
                        [*(record.get("relation_refs") or []), *incoming_relations]
                    )[:100]
                    self._advance(record, actor=actor, kind="duplicate_observed")
                    event = self._event(state, record, "observed_again", actor)
                    self._validate(record)
                    self._write(state)
                    self._publish(event)
                    return {"ok": True, "duplicate": True, "feedback": _clone(record), "event": event}
            now = _now()
            feedback_id = f"devfeedback.{new_id()}"
            record = {
                "schema": DEVELOPMENT_FEEDBACK_SCHEMA,
                "feedback_id": feedback_id,
                "revision": 1,
                "source": source_token,
                "category": category_token,
                "status": "observed",
                "summary": text[:4000],
                "details": _text(details)[:12000],
                "recommendation": _text(recommendation)[:4000],
                "blocking": bool(blocking),
                "confidence": max(0.0, min(1.0, float(confidence))),
                "impact": impacts,
                "target_refs": refs,
                "evidence_refs": _dedup_mappings(evidence_refs)[:100],
                "relation_refs": _dedup_mappings(relation_refs)[:100],
                "ticket_refs": [],
                "comments": [],
                "classification": dict(classification or {}),
                "dedup_key": key[:500],
                "occurrence_count": 1,
                "history": [{"kind": "observed", "actor": _text(actor) or "system", "recorded_at": now}],
                "created_at": now,
                "updated_at": now,
            }
            self._validate(record)
            state["records"][feedback_id] = record
            event = self._event(state, record, "observed", actor)
            self._write(state)
        self._publish(event)
        return {"ok": True, "duplicate": False, "feedback": _clone(record), "event": event}

    def get(self, feedback_id: str) -> dict[str, Any] | None:
        self.import_legacy_builder_feedback()
        value = self._read()["records"].get(_text(feedback_id))
        return _clone(value) if isinstance(value, Mapping) else None

    def qualification_preview(
        self, feedback: str | Mapping[str, Any]
    ) -> dict[str, Any]:
        record = dict(feedback) if isinstance(feedback, Mapping) else self.get(feedback)
        if not record:
            raise KeyError(feedback)
        return _clone(_qualification_preview(record))

    def list(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        source: str | None = None,
        blocking: bool | None = None,
        target_ref: str | None = None,
        search: str | None = None,
        rejection_class: str | None = None,
        contract_ref: str | None = None,
        operation_id: str | None = None,
        owner_route: str | None = None,
        qualification_status: str | None = None,
        updated_since: str | None = None,
        limit: int = 200,
        import_legacy: bool = True,
    ) -> list[dict[str, Any]]:
        if import_legacy:
            self.import_legacy_builder_feedback()
        values = [dict(item) for item in self._read()["records"].values()]
        token = _text(search).casefold()
        rejection_token = _text(rejection_class).lower()
        contract_token = _text(contract_ref)
        operation_token = _text(operation_id)
        owner_route_token = _text(owner_route)
        qualification_status_token = _text(qualification_status)
        if rejection_token and rejection_token not in REJECTION_CLASSES:
            raise ValueError(
                f"unsupported development feedback rejection class: {rejection_token}"
            )
        values = [
            item
            for item in values
            for classification in [
                dict(item.get("classification"))
                if isinstance(item.get("classification"), Mapping)
                else {}
            ]
            for application_trace in [
                dict(classification.get("application_trace"))
                if isinstance(classification.get("application_trace"), Mapping)
                else {}
            ]
            for qualification in [
                dict(classification.get("qualification"))
                if isinstance(classification.get("qualification"), Mapping)
                else {}
            ]
            if (not status or item.get("status") == status)
            and (not category or item.get("category") == category)
            and (not source or item.get("source") == source)
            and (blocking is None or item.get("blocking") is blocking)
            and (not target_ref or target_ref in (item.get("target_refs") or []))
            and (
                not rejection_token
                or _text(
                    (item.get("classification") or {}).get("rejection_class")
                    if isinstance(item.get("classification"), Mapping)
                    else ""
                ).lower()
                == rejection_token
            )
            and (
                not contract_token
                or _text(application_trace.get("contract_ref")) == contract_token
                or _text(classification.get("public_contract_ref")) == contract_token
            )
            and (
                not operation_token
                or _text(application_trace.get("operation_id")) == operation_token
                or operation_token
                in {
                    _text(value)
                    for value in classification.get("operation_ids") or []
                    if _text(value)
                }
            )
            and (
                not owner_route_token
                or _text(qualification.get("owner_route")) == owner_route_token
            )
            and (
                not qualification_status_token
                or _text(qualification.get("status"))
                == qualification_status_token
            )
            and (not updated_since or _text(item.get("updated_at")) >= updated_since)
            and (
                not token
                or token
                in json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).casefold()
            )
        ]
        values.sort(key=lambda item: (_text(item.get("updated_at")), _text(item.get("feedback_id"))), reverse=True)
        return _clone(values[: max(0, min(int(limit), 1000))])

    def qualify(
        self,
        feedback_id: str,
        *,
        owner_route: str,
        promotion_route: str,
        actor: str,
        rationale: str,
        owner_ref: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        owner_route_token = _text(owner_route).lower()
        promotion_route_token = _text(promotion_route).lower()
        actor_token = _text(actor)
        rationale_token = _text(rationale)
        if owner_route_token not in OWNER_ROUTES:
            raise ValueError(
                f"unsupported development feedback owner route: {owner_route_token}"
            )
        if promotion_route_token not in PROMOTION_ROUTES:
            raise ValueError(
                "development feedback promotion route must be project, "
                "sdk_understanding, or core"
            )
        if promotion_route_token not in OWNER_ROUTE_PROMOTIONS[owner_route_token]:
            raise ValueError(
                "development feedback owner route is incompatible with promotion route"
            )
        if not actor_token:
            raise ValueError("development feedback qualification actor is required")
        if not actor_token.lower().startswith(QUALIFICATION_ACTOR_PREFIXES):
            raise ValueError(
                "development feedback qualification requires a human or policy actor"
            )
        if len(rationale_token) < 3:
            raise ValueError("development feedback qualification rationale is required")

        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            record = state["records"].get(_text(feedback_id))
            if not isinstance(record, Mapping):
                raise KeyError(feedback_id)
            record = dict(record)
            if expected_revision is not None and int(record.get("revision") or 0) != expected_revision:
                raise ValueError("development feedback revision conflict")
            if record.get("status") == "promoted":
                raise ValueError("promoted development feedback cannot be requalified")
            classification = (
                dict(record.get("classification"))
                if isinstance(record.get("classification"), Mapping)
                else {}
            )
            if record.get("category") == "result_rejected" and _text(
                classification.get("rejection_class")
            ) not in REJECTION_CLASSES:
                raise ValueError(
                    "rejected Builder result must be diagnostically classified before qualification"
                )
            resolved_owner_ref = _text(owner_ref) or _owner_ref_for(
                record, promotion_route_token
            )
            required_prefixes = {
                "project": ("project:", "scenario:", "skill:", "modal:", "component:"),
                "sdk_understanding": ("sdk:", "api:", "resource:"),
                "core": ("core:",),
            }[promotion_route_token]
            if not resolved_owner_ref.startswith(required_prefixes):
                raise ValueError(
                    f"development feedback {promotion_route_token} qualification "
                    "requires a compatible owner_ref"
                )
            preview = _qualification_preview(record)
            qualification = {
                "schema": "adaos.development_feedback.qualification.v1",
                "status": "qualified",
                "owner_route": owner_route_token,
                "promotion_route": promotion_route_token,
                "owner_ref": resolved_owner_ref,
                "rationale": rationale_token[:4000],
                "qualified_by": actor_token[:200],
                "qualified_at": _now(),
                "routing_preview_digest": preview["digest"],
            }
            _validate_qualification(qualification)
            record["classification"] = {
                **classification,
                "qualification": qualification,
            }
            if record.get("status") in {"observed", "rejected"}:
                record["status"] = "triaged"
            self._advance(
                record,
                actor=actor_token,
                kind="qualified",
                owner_route=owner_route_token,
                promotion_route=promotion_route_token,
                owner_ref=resolved_owner_ref,
            )
            self._validate(record)
            state["records"][record["feedback_id"]] = record
            event = self._event(state, record, "qualified", actor_token)
            self._write(state)
        self._publish(event)
        return _clone(record)

    def transition(
        self,
        feedback_id: str,
        *,
        status: str,
        actor: str,
        reason: str = "",
        classification: Mapping[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        target = _text(status).lower()
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            record = state["records"].get(_text(feedback_id))
            if not isinstance(record, Mapping):
                raise KeyError(feedback_id)
            record = dict(record)
            if expected_revision is not None and int(record.get("revision") or 0) != expected_revision:
                raise ValueError("development feedback revision conflict")
            current = _text(record.get("status"))
            if target not in TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid development feedback transition: {current}->{target}")
            if classification:
                if "qualification" in classification:
                    raise ValueError(
                        "development feedback qualification requires the governed qualify operation"
                    )
                record["classification"] = {
                    **dict(record.get("classification") or {}),
                    **dict(classification),
                }
            record["status"] = target
            self._advance(record, actor=actor, kind=target, reason=reason)
            self._validate(record)
            state["records"][record["feedback_id"]] = record
            event = self._event(state, record, target, actor)
            self._write(state)
        self._publish(event)
        return _clone(record)

    def comment(
        self,
        feedback_id: str,
        *,
        body: str,
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        text = _text(body)
        if not text:
            raise ValueError("development feedback comment body is required")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            record = state["records"].get(_text(feedback_id))
            if not isinstance(record, Mapping):
                raise KeyError(feedback_id)
            record = dict(record)
            if expected_revision is not None and int(record.get("revision") or 0) != expected_revision:
                raise ValueError("development feedback revision conflict")
            comment = {
                "comment_id": f"devfeedback-comment.{new_id()}",
                "body": text[:4000],
                "actor": _text(actor) or "system",
                "created_at": _now(),
            }
            record["comments"] = [*(record.get("comments") or []), comment][-500:]
            self._advance(record, actor=actor, kind="commented")
            self._validate(record)
            state["records"][record["feedback_id"]] = record
            event = self._event(state, record, "commented", actor)
            self._write(state)
        self._publish(event)
        return _clone(record)

    def promote(
        self,
        feedback_id: str,
        *,
        route: str,
        actor: str,
        payload: Mapping[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        record = self.get(feedback_id)
        if not record:
            raise KeyError(feedback_id)
        if expected_revision is not None and int(record.get("revision") or 0) != expected_revision:
            raise ValueError("development feedback revision conflict")
        if record.get("status") == "promoted":
            return {"ok": True, "idempotent": True, "feedback": record, "ticket_refs": record.get("ticket_refs") or []}
        if record.get("status") != "accepted":
            raise ValueError("development feedback must be accepted before promotion")
        classification = (
            dict(record.get("classification"))
            if isinstance(record.get("classification"), Mapping)
            else {}
        )
        qualification = (
            dict(classification.get("qualification"))
            if isinstance(classification.get("qualification"), Mapping)
            else {}
        )
        rejection_class = _text(
            classification.get("rejection_class")
        ).lower()
        if record.get("category") == "result_rejected" and rejection_class not in REJECTION_CLASSES:
            raise ValueError(
                "rejected Builder result must be qualified before promotion"
            )
        requested_route = _text(route).lower()
        qualified_route = _text(qualification.get("promotion_route")).lower()
        if requested_route == "qualified":
            if qualification.get("status") != "qualified" or not qualified_route:
                raise ValueError(
                    "development feedback requires an explicit qualification before qualified promotion"
                )
            route_token = qualified_route
        else:
            route_token = requested_route
        if qualified_route and route_token != qualified_route:
            raise ValueError(
                "development feedback promotion route conflicts with its qualification"
            )
        body = dict(payload or {})
        target_scope, owner_area, component_ref = _target_scope(record.get("target_refs") or [])
        evidence_refs = [
            *(record.get("evidence_refs") or []),
            {
                "type": "development_feedback",
                "id": record["feedback_id"],
                "source": record["source"],
                "category": record["category"],
            },
        ]
        from adaos.services.development_tickets import DevelopmentTicketService

        tickets = DevelopmentTicketService(state_dir=self.state_dir)
        if route_token == "core":
            core_ref = _text(body.get("component_ref")) or _text(
                qualification.get("owner_ref")
            ) or next(
                (ref for ref in record.get("target_refs") or [] if ref.startswith("core:")),
                "",
            )
            if not core_ref.startswith("core:"):
                raise ValueError("core promotion requires a core: component_ref")
            desired_contract = _text(body.get("desired_contract") or record.get("recommendation"))
            if not desired_contract:
                raise ValueError("core promotion requires desired_contract")
            ticket_result = tickets.create_core_capability_request(
                summary=record["summary"],
                component_ref=core_ref,
                desired_contract=desired_contract,
                actor=actor,
                impact=_text(body.get("impact")) or ("blocker" if record.get("blocking") else "contract_gap"),
                motivation=_text(record.get("details")),
                observed_limitation=_text(body.get("observed_limitation") or record.get("details") or record.get("summary")),
                blocked_ticket_ids=body.get("blocked_ticket_ids") or [],
                evidence_refs=evidence_refs,
                metadata={
                    "development_feedback_id": record["feedback_id"],
                    "development_feedback_owner_route": qualification.get(
                        "owner_route"
                    ),
                    "development_feedback_qualification": qualification or None,
                },
                source="development_feedback",
            )
        elif route_token == "sdk_understanding":
            qualified_owner_ref = _text(qualification.get("owner_ref"))
            method_ref = _text(body.get("method_ref")) or (
                qualified_owner_ref.split(":", 1)[1]
                if qualified_owner_ref.startswith(("sdk:", "api:", "resource:"))
                else ""
            ) or next(
                (
                    ref.split(":", 1)[1]
                    for ref in record.get("target_refs") or []
                    if ref.startswith(("sdk:", "api:", "resource:"))
                ),
                "",
            )
            if not method_ref:
                raise ValueError("SDK promotion requires method_ref")
            ticket_result = tickets.record_sdk_understanding_signal(
                kind=_SDK_KINDS[record["category"]],
                summary=record["summary"],
                method_ref=method_ref,
                actor=actor,
                expected_behavior=_text(record.get("recommendation")),
                observed_behavior=_text(record.get("details")),
                diagnosis=record["category"],
                project_ticket_id=_text(body.get("project_ticket_id")) or None,
                evidence_refs=evidence_refs,
                metadata={
                    "development_feedback_id": record["feedback_id"],
                    "development_feedback_owner_route": qualification.get(
                        "owner_route"
                    ),
                    "development_feedback_qualification": qualification or None,
                },
            )
        elif route_token == "project":
            signal = tickets.capture_signal(
                kind="review_comment",
                summary=record["summary"],
                owner_scope={"type": "workspace", "id": "local"},
                origin_scope={"type": "builder", "id": actor, "surface": "development_feedback"},
                target_scope=target_scope,
                severity="high" if record.get("blocking") else "medium",
                blocking=bool(record.get("blocking")),
                source="development_feedback",
                dedup_key=f"{record['dedup_key']}:project",
                evidence_refs=evidence_refs,
                metadata={
                    "development_feedback_id": record["feedback_id"],
                    "development_feedback_owner_route": qualification.get(
                        "owner_route"
                    ),
                    "development_feedback_qualification": qualification or None,
                },
                owner_area=owner_area,
                component_ref=component_ref,
            )
            ticket_result = tickets.ensure_ticket_for_signal(
                signal["signal"],
                kind="review_debt",
                status="proposed",
                source="development_feedback",
                dedup_key=f"{record['dedup_key']}:project",
                metadata={
                    "development_feedback_id": record["feedback_id"],
                    "development_feedback_owner_route": qualification.get(
                        "owner_route"
                    ),
                    "development_feedback_qualification": qualification or None,
                },
                owner_area=owner_area,
                component_ref=component_ref,
            )
        else:
            raise ValueError(
                "development feedback route must be project, sdk_understanding, core, or qualified"
            )
        ticket = dict(ticket_result["ticket"])
        promoted = self._mark_promoted(
            feedback_id,
            ticket_id=ticket["ticket_id"],
            route=route_token,
            actor=actor,
            expected_revision=expected_revision,
        )
        return {"ok": True, "idempotent": False, "feedback": promoted, "ticket": ticket, "ticket_refs": promoted["ticket_refs"]}

    def import_legacy_builder_feedback(self) -> int:
        state_root = Path(self.state_dir or current_state_dir())
        paths = sorted((state_root / "builder" / "development_sessions").glob("*/feedback/feedback_*.json"))
        imported = 0
        existing_keys = {
            _text(item.get("dedup_key"))
            for item in self._read()["records"].values()
            if isinstance(item, Mapping)
        }
        for path in paths:
            try:
                legacy = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(legacy, Mapping):
                    continue
                legacy_key = f"legacy:{_text(legacy.get('feedback_id'))}"
                if legacy_key in existing_keys:
                    continue
                evidence = [
                    {"type": item.get("kind"), "ref": item.get("ref"), "digest": item.get("digest")}
                    for item in legacy.get("evidence") or []
                    if isinstance(item, Mapping)
                ]
                result = self.capture(
                    source="legacy_builder_session",
                    category=_LEGACY_CATEGORY.get(_text(legacy.get("kind")), "insufficient_context"),
                    summary=_text(legacy.get("summary")),
                    blocking=bool(legacy.get("blocking")),
                    confidence=1.0,
                    impact=["blocker"] if legacy.get("blocking") else ["reliability"],
                    target_refs=legacy.get("affected_refs") or [],
                    details="\n".join(_text(item) for item in legacy.get("constraints") or [] if _text(item)),
                    recommendation=_text(legacy.get("proposed_action")),
                    evidence_refs=evidence,
                    relation_refs=[
                        {"type": "builder_session", "id": _text(legacy.get("session_id"))},
                        {"type": "legacy_feedback", "id": _text(legacy.get("feedback_id"))},
                    ],
                    classification={"legacy_schema": legacy.get("schema")},
                    dedup_key=legacy_key,
                    actor=_text(legacy.get("created_by")) or "builder",
                    idempotent_replay=True,
                )
                if not result.get("duplicate"):
                    imported += 1
                    existing_keys.add(legacy_key)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return imported

    def _mark_promoted(
        self,
        feedback_id: str,
        *,
        ticket_id: str,
        route: str,
        actor: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            record = dict(state["records"].get(feedback_id) or {})
            if not record:
                raise KeyError(feedback_id)
            if expected_revision is not None and int(record.get("revision") or 0) != expected_revision:
                raise ValueError("development feedback revision conflict after ticket promotion")
            record["status"] = "promoted"
            record["ticket_refs"] = list(dict.fromkeys([*(record.get("ticket_refs") or []), ticket_id]))[:20]
            record["promotion"] = {"route": route, "ticket_id": ticket_id, "actor": actor, "promoted_at": _now()}
            self._advance(record, actor=actor, kind="promoted", ticket_id=ticket_id, route=route)
            self._validate(record)
            state["records"][feedback_id] = record
            event = self._event(state, record, "promoted", actor)
            self._write(state)
        self._publish(event)
        return _clone(record)

    @staticmethod
    def _advance(record: dict[str, Any], *, actor: str, kind: str, reason: str = "", **extra: Any) -> None:
        now = _now()
        record["revision"] = int(record.get("revision") or 0) + 1
        record["updated_at"] = now
        record["history"] = [
            *(record.get("history") or []),
            {"kind": kind, "actor": _text(actor) or "system", "reason": _text(reason) or None, "recorded_at": now, **extra},
        ][-1000:]

    def _event(self, state: dict[str, Any], record: Mapping[str, Any], action: str, actor: str) -> dict[str, Any]:
        previous = _text((state.get("events") or [{}])[-1].get("digest")) if state.get("events") else ""
        event = {
            "schema": "adaos.development_feedback.lifecycle_event.v1",
            "event_id": f"devfeedback-event.{new_id()}",
            "sequence": int(state.get("sequence") or 0) + 1,
            "type": f"development.feedback.{action}",
            "feedback_id": record["feedback_id"],
            "revision": record["revision"],
            "status": record["status"],
            "category": record["category"],
            "actor": _text(actor) or "system",
            "previous_digest": previous or None,
            "occurred_at": _now(),
        }
        event["digest"] = "sha256:" + hashlib.sha256(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        state["sequence"] = event["sequence"]
        state["events"] = [*(state.get("events") or []), event][-5000:]
        return event

    @staticmethod
    def _publish(event: Mapping[str, Any]) -> None:
        try:
            from adaos.services.agent_context import get_ctx

            get_ctx().bus.publish(
                Event(
                    type=_text(event.get("type")),
                    payload=_clone(event),
                    source="adaos.development_feedback",
                    ts=time.time(),
                )
            )
        except Exception:
            pass

    def _validate(self, record: Mapping[str, Any]) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "abi" / "development_feedback.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(record), key=lambda item: list(item.path))
        if errors:
            raise ValueError(f"invalid development feedback: {errors[0].message}")
        classification = record.get("classification")
        if isinstance(classification, Mapping) and isinstance(
            classification.get("qualification"), Mapping
        ):
            _validate_qualification(classification["qualification"])

    def _read(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"schema": DEVELOPMENT_FEEDBACK_STATE_SCHEMA, "records": {}, "events": [], "sequence": 0}
        value = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, Mapping) or value.get("schema") != DEVELOPMENT_FEEDBACK_STATE_SCHEMA:
            raise ValueError("invalid development feedback state")
        return {
            "schema": DEVELOPMENT_FEEDBACK_STATE_SCHEMA,
            "records": dict(value.get("records") or {}),
            "events": list(value.get("events") or []),
            "sequence": int(value.get("sequence") or 0),
        }

    def _write(self, state: Mapping[str, Any]) -> None:
        atomic_write_json(self.state_path, dict(state))


__all__ = [
    "CATEGORIES",
    "DEVELOPMENT_FEEDBACK_SCHEMA",
    "DevelopmentFeedbackService",
    "IMPACTS",
    "REJECTION_CLASSES",
    "SOURCES",
]
