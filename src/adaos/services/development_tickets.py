from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.domain import Event
from adaos.sdk.core.decorators import subscribe
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder.repair import BuilderRepairService
from adaos.services.id_gen import new_id
from adaos.services.runtime_paths import current_state_dir


DEVELOPMENT_SIGNAL_SCHEMA = "adaos.development_signal.v1"
DEV_TICKET_SCHEMA = "adaos.dev_ticket.v1"
STATE_SCHEMA = "adaos.development_tickets.state.v1"
COMPATIBILITY_PENDING_ACTION_KIND = "development_ticket.runtime_compatibility.review"
COMPATIBILITY_RESPONSE_TOPIC = "development_tickets.compatibility.response"
ACTIVE_SIGNAL_STATES = {
    "captured",
    "classified",
    "needs_clarification",
    "triaged",
    "deferred",
    "teacher_candidate",
    "repair_created",
    "issue_created",
    "in_progress",
}
ACTIVE_TICKET_STATES = {
    "captured",
    "proposed",
    "accepted",
    "deferred",
    "waiting_for_user",
    "ready_for_builder",
    "claimed",
    "in_progress",
    "in_builder",
    "resolved",
    "verified",
}
TERMINAL_TICKET_STATES = {"closed", "superseded", "stale"}
TICKET_STATUS_GROUPS = {
    "open": ACTIVE_TICKET_STATES,
    "active": ACTIVE_TICKET_STATES,
    "triage": {"captured", "proposed", "accepted", "waiting_for_user", "ready_for_builder"},
    "work": {"claimed", "in_progress", "in_builder"},
    "review": {"resolved", "verified"},
    "terminal": TERMINAL_TICKET_STATES,
    "closed": TERMINAL_TICKET_STATES,
}
RECEIVER_COMPATIBILITY_REASONS = {
    "stream_receiver_policy_missing",
    "stream_receiver_not_declared",
}
_LOCK = threading.RLock()
_log = logging.getLogger("adaos.development_tickets")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _sequence_of_mappings(value: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if not value:
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fingerprint(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _target_identity(target_scope: Mapping[str, Any]) -> str:
    target = _mapping(target_scope)
    target_type = _text(target.get("type")) or "unknown"
    target_id = _text(target.get("id") or target.get("name")) or "unknown"
    version = _text(target.get("version"))
    digest = _text(target.get("digest"))
    return ":".join(item for item in (target_type, target_id, version, digest) if item)


def _project_id_from_target(target_scope: Mapping[str, Any]) -> str:
    target = _mapping(target_scope)
    token = _text(target.get("id") or target.get("name"))
    return token or _text(target.get("type")) or "unknown"


def ticket_status_group(status: str) -> str:
    token = _text(status)
    if token in {"claimed", "in_progress", "in_builder"}:
        return "work"
    if token in {"resolved", "verified"}:
        return "review"
    if token in TERMINAL_TICKET_STATES:
        return "closed"
    return "triage"


def _normalized_ticket(ticket: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(ticket)
    out["status_group"] = ticket_status_group(_text(out.get("status")))
    return _clone(out)


def development_source_options(target_scope: Mapping[str, Any]) -> dict[str, Any]:
    target = _mapping(target_scope)
    source = _text(target.get("source")).lower()
    target_type = _text(target.get("type")) or "unknown"
    target_id = _text(target.get("id") or target.get("name"))
    if source in {"dev", "workspace", "local", "source"}:
        return {
            "status": "source_available",
            "source": source or "workspace",
            "target_type": target_type,
            "target_id": target_id or None,
            "options": ["use_existing_dev_source"],
            "default_option": "use_existing_dev_source",
        }
    return {
        "status": "needs_materialization",
        "source": source or "unknown",
        "target_type": target_type,
        "target_id": target_id or None,
        "options": [
            "materialize_dev_source",
            "create_local_fork",
            "create_runtime_overlay",
            "defer",
        ],
        "default_option": "materialize_dev_source",
    }


def _merge_refs(current: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in [*current, *incoming]:
        item = dict(raw)
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[-100:]


def _merge_ids(current: Sequence[Any], incoming: Sequence[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in [*current, *incoming]:
        item = _text(raw)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out[-100:]


def _event_payload(evt: Any) -> dict[str, Any]:
    payload = getattr(evt, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(evt, Mapping):
        return dict(evt)
    return {}


def _collect_scope_tokens(tokens: set[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, Mapping):
        scope_type = _text(value.get("type") or value.get("kind"))
        scope_id = _text(value.get("id") or value.get("name"))
        if scope_id:
            tokens.add(scope_id)
            if scope_type:
                tokens.add(f"{scope_type}:{scope_id}")
        for key in (
            "ref",
            "canonical_ref",
            "target_ref",
            "project_ref",
            "scenario_ref",
            "skill_ref",
            "modal_ref",
            "component_ref",
            "project_id",
            "scenario_id",
            "skill_id",
            "modal_id",
            "component_id",
            "surface",
        ):
            _collect_scope_tokens(tokens, value.get(key))
        for key in (
            "component_refs",
            "components",
            "target_refs",
            "affected_refs",
            "scope_refs",
            "related_refs",
        ):
            _collect_scope_tokens(tokens, value.get(key))
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_scope_tokens(tokens, item)
        return
    token = _text(value)
    if not token or token == ":" or "$" in token:
        return
    tokens.add(token)
    if ":" in token:
        tail = token.rsplit(":", 1)[-1].strip()
        if tail:
            tokens.add(tail)


def _ticket_scope_tokens(ticket: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    _collect_scope_tokens(tokens, ticket.get("target_scope"))
    _collect_scope_tokens(tokens, _mapping(ticket.get("metadata")).get("invocation_context"))
    return tokens


def _ticket_owner_tokens(ticket: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    _collect_scope_tokens(tokens, ticket.get("owner_scope"))
    metadata = _mapping(ticket.get("metadata"))
    _collect_scope_tokens(tokens, metadata.get("claimed_by"))
    return tokens


def _ticket_search_text(ticket: Mapping[str, Any]) -> str:
    chunks = [
        _text(ticket.get("ticket_id")),
        _text(ticket.get("kind")),
        _text(ticket.get("status")),
        _text(ticket.get("summary")),
        _text(ticket.get("source")),
        json.dumps(ticket.get("target_scope") or {}, ensure_ascii=False, sort_keys=True, default=str),
        json.dumps(ticket.get("metadata") or {}, ensure_ascii=False, sort_keys=True, default=str),
    ]
    return "\n".join(chunks).lower()


def _artifact_id_from_ref(ref: Mapping[str, Any]) -> str:
    direct = _text(ref.get("artifact_id"))
    if direct:
        return direct
    uri = _text(ref.get("uri"))
    if uri.startswith("dev-ticket-artifact:"):
        return uri.split(":", 1)[1].strip()
    return ""


@dataclass(slots=True)
class DevelopmentTicketService:
    state_dir: Path | None = None

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "development_tickets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".state.lock"

    def capture_signal(
        self,
        *,
        kind: str,
        summary: str,
        owner_scope: Mapping[str, Any] | None = None,
        origin_scope: Mapping[str, Any] | None = None,
        target_scope: Mapping[str, Any] | None = None,
        severity: str = "medium",
        blocking: bool = False,
        source: str = "runtime",
        dedup_key: str | None = None,
        artifact_refs: Sequence[Mapping[str, Any]] = (),
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        conversation_ref: Mapping[str, Any] | None = None,
        nlu_teacher_ref: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        classification_confidence: float | None = None,
    ) -> dict[str, Any]:
        signal_kind = _text(kind)
        text = _text(summary)
        if not signal_kind or not text:
            raise ValueError("kind and summary are required")
        owner = _mapping(owner_scope) or {"type": "workspace", "id": "local"}
        origin = _mapping(origin_scope) or {"type": "runtime"}
        target = _mapping(target_scope) or {"type": "unknown"}
        key = _text(dedup_key) or _fingerprint("dsig", signal_kind, text.lower(), _target_identity(target), metadata or {})
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            for signal in state["signals"].values():
                if signal.get("dedup_key") == key and signal.get("status") in ACTIVE_SIGNAL_STATES:
                    signal["occurrence_count"] = int(signal.get("occurrence_count") or 1) + 1
                    signal["artifact_refs"] = _merge_refs(signal.get("artifact_refs") or [], artifact_refs)
                    signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], evidence_refs)
                    signal["updated_at"] = _now()
                    self._append_history(
                        signal,
                        {
                            "kind": "duplicate_occurrence",
                            "source": _text(source) or "runtime",
                            "recorded_at": signal["updated_at"],
                        },
                    )
                    self._validate_signal(signal)
                    self._write(state)
                    return {"ok": True, "duplicate": True, "signal": _clone(signal)}
            now = _now()
            signal_id = f"dsig.{new_id()}"
            signal = {
                "schema": DEVELOPMENT_SIGNAL_SCHEMA,
                "signal_id": signal_id,
                "kind": signal_kind,
                "status": "captured",
                "summary": text,
                "severity": _text(severity) or "medium",
                "blocking": bool(blocking),
                "classification_confidence": float(classification_confidence if classification_confidence is not None else 1.0),
                "owner_scope": owner,
                "origin_scope": origin,
                "target_scope": target,
                "dedup_key": key,
                "occurrence_count": 1,
                "source": _text(source) or "runtime",
                "artifact_refs": _merge_refs([], artifact_refs),
                "evidence_refs": _merge_refs([], evidence_refs),
                "conversation_ref": _mapping(conversation_ref),
                "nlu_teacher_ref": _mapping(nlu_teacher_ref),
                "builder_ref": {},
                "issue_ref": {},
                "policy": _mapping(policy),
                "metadata": _mapping(metadata),
                "history": [{"kind": "captured", "recorded_at": now}],
                "created_at": now,
                "updated_at": now,
            }
            self._validate_signal(signal)
            state["signals"][signal_id] = signal
            self._write(state)
            return {"ok": True, "duplicate": False, "signal": _clone(signal)}

    def ensure_ticket_for_signal(
        self,
        signal: Mapping[str, Any],
        *,
        kind: str,
        summary: str | None = None,
        status: str = "captured",
        source: str | None = None,
        dedup_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        signal_id = _text(signal.get("signal_id"))
        if not signal_id:
            raise ValueError("signal_id is required")
        ticket_kind = _text(kind)
        text = _text(summary or signal.get("summary"))
        if not ticket_kind or not text:
            raise ValueError("kind and summary are required")
        target = _mapping(signal.get("target_scope"))
        key = _text(dedup_key) or _fingerprint("dticket", ticket_kind, signal.get("dedup_key"), _target_identity(target))
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            if signal_id not in state["signals"]:
                state["signals"][signal_id] = _clone(signal)
            for ticket in state["tickets"].values():
                if ticket.get("dedup_key") == key and ticket.get("status") in ACTIVE_TICKET_STATES:
                    ticket["occurrence_count"] = int(ticket.get("occurrence_count") or 1) + 1
                    ticket["signal_ids"] = _merge_ids(ticket.get("signal_ids") or [], [signal_id])
                    ticket["evidence_refs"] = _merge_refs(
                        ticket.get("evidence_refs") or [],
                        _sequence_of_mappings(signal.get("evidence_refs") or []),
                    )
                    ticket["updated_at"] = _now()
                    self._append_history(
                        ticket,
                        {
                            "kind": "duplicate_signal",
                            "signal_id": signal_id,
                            "recorded_at": ticket["updated_at"],
                        },
                    )
                    self._validate_ticket(ticket)
                    self._write(state)
                    return {"ok": True, "duplicate": True, "ticket": _normalized_ticket(ticket)}
            now = _now()
            ticket_id = f"dticket.{new_id()}"
            ticket = {
                "schema": DEV_TICKET_SCHEMA,
                "ticket_id": ticket_id,
                "kind": ticket_kind,
                "status": _text(status) or "captured",
                "summary": text,
                "severity": _text(signal.get("severity")) or "medium",
                "blocking": bool(signal.get("blocking")),
                "owner_scope": _mapping(signal.get("owner_scope")),
                "origin_scope": _mapping(signal.get("origin_scope")),
                "target_scope": target,
                "signal_ids": [signal_id],
                "dedup_key": key,
                "occurrence_count": 1,
                "source": _text(source or signal.get("source")) or "runtime",
                "evidence_refs": _merge_refs([], _sequence_of_mappings(signal.get("evidence_refs") or [])),
                "pending_action_refs": [],
                "builder_refs": [],
                "external_refs": [],
                "policy": _mapping(policy) or _mapping(signal.get("policy")),
                "metadata": _mapping(metadata),
                "history": [{"kind": "created", "signal_id": signal_id, "recorded_at": now}],
                "created_at": now,
                "updated_at": now,
            }
            self._validate_ticket(ticket)
            state["tickets"][ticket_id] = ticket
            self._write(state)
            return {"ok": True, "duplicate": False, "ticket": _normalized_ticket(ticket)}

    def report_compatibility_finding(
        self,
        *,
        code: str,
        summary: str,
        target_scope: Mapping[str, Any],
        owner_scope: Mapping[str, Any] | None = None,
        origin_scope: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        artifact_refs: Sequence[Mapping[str, Any]] = (),
        context: Mapping[str, Any] | None = None,
        severity: str = "high",
        blocking: bool = True,
        run_policy: str = "block",
        design_time_fixable: bool = True,
        autonomous_repair_eligible: bool = True,
        source: str = "runtime_guard",
        dedup_key: str | None = None,
        publish_pending_action: bool = False,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        reason_code = _text(code)
        target = _mapping(target_scope)
        details = _mapping(context)
        key = _text(dedup_key) or _fingerprint("compat", reason_code, _target_identity(target), details.get("receiver"), details.get("route"), details.get("projection_slot"))
        policy = {
            "blocking": bool(blocking),
            "run_policy": _text(run_policy) or "block",
            "design_time_fixable": bool(design_time_fixable),
            "autonomous_repair_eligible": bool(autonomous_repair_eligible),
        }
        signal_result = self.capture_signal(
            kind="compatibility_finding",
            summary=summary,
            owner_scope=owner_scope,
            origin_scope=origin_scope or {"type": "runtime", "surface": "compatibility_guard"},
            target_scope=target,
            severity=severity,
            blocking=blocking,
            source=source,
            dedup_key=key,
            artifact_refs=artifact_refs,
            evidence_refs=evidence_refs,
            policy=policy,
            metadata={"code": reason_code, "context": details},
        )
        ticket_result = self.ensure_ticket_for_signal(
            signal_result["signal"],
            kind="runtime_compatibility_debt",
            status="accepted" if blocking else "captured",
            source=source,
            dedup_key=key,
            metadata={"code": reason_code, "context": details},
            policy=policy,
        )
        ticket = ticket_result["ticket"]
        pending_action = None
        pending_action_published = False
        if publish_pending_action and (blocking or _text(run_policy) in {"block", "degrade"}):
            pa_result = self.publish_compatibility_pending_action(
                ticket["ticket_id"],
                ctx=ctx,
                webspace_id=webspace_id,
            )
            pending_action = pa_result.get("pending_action")
            pending_action_published = bool(pa_result.get("published"))
            ticket = pa_result.get("ticket") or ticket
        return {
            "ok": True,
            "signal": signal_result["signal"],
            "signal_duplicate": bool(signal_result.get("duplicate")),
            "ticket": ticket,
            "ticket_duplicate": bool(ticket_result.get("duplicate")),
            "pending_action": pending_action,
            "pending_action_published": pending_action_published,
        }

    def report_stream_receiver_compatibility_finding(
        self,
        *,
        skill_id: str,
        admission: Mapping[str, Any],
        topic: str = "",
        event_type: str = "",
        owner_scope: Mapping[str, Any] | None = None,
        target_scope: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
        artifact_refs: Sequence[Mapping[str, Any]] = (),
        publish_pending_action: bool = False,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        reason = _text(admission.get("reason"))
        if reason not in RECEIVER_COMPATIBILITY_REASONS:
            return {"ok": True, "reported": False, "reason": reason or "not_receiver_compatibility"}
        skill = _text(skill_id)
        if not skill:
            raise ValueError("skill_id is required")
        receiver = _text(admission.get("receiver"))
        summary = (
            f"Skill {skill} lacks receiver/data-route declaration"
            + (f" for {receiver}" if receiver else "")
        )
        context = {
            "code": f"compat.{reason}",
            "reason": reason,
            "receiver": receiver or None,
            "receiver_patterns": list(admission.get("receiver_patterns") or [])[:12],
            "topic": _text(topic) or None,
            "event_type": _text(event_type) or _text(topic) or None,
        }
        target = _mapping(target_scope) or {"type": "skill", "id": skill, "source": "installed"}
        return {
            **self.report_compatibility_finding(
                code=f"compat.{reason}",
                summary=summary,
                target_scope=target,
                owner_scope=owner_scope,
                origin_scope={"type": "runtime", "surface": "stream_receiver_admission", "id": skill},
                evidence_refs=[
                    *evidence_refs,
                    {
                        "type": "runtime_guard",
                        "code": f"compat.{reason}",
                        "receiver": receiver or None,
                        "topic": _text(topic) or None,
                    },
                ],
                artifact_refs=artifact_refs,
                context=context,
                severity="high" if reason == "stream_receiver_not_declared" else "medium",
                blocking=reason == "stream_receiver_not_declared",
                run_policy="block" if reason == "stream_receiver_not_declared" else "degrade",
                design_time_fixable=True,
                autonomous_repair_eligible=True,
                source="runtime_guard",
                dedup_key=_fingerprint("compat.receiver", skill, reason, receiver or _text(topic)),
                publish_pending_action=publish_pending_action,
                ctx=ctx,
                webspace_id=webspace_id,
            ),
            "reported": True,
        }

    def publish_compatibility_pending_action(
        self,
        ticket_id: str,
        *,
        ctx: Any = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        existing = [
            ref
            for ref in _sequence_of_mappings(ticket.get("pending_action_refs") or [])
            if ref.get("kind") == COMPATIBILITY_PENDING_ACTION_KIND
        ]
        if existing:
            return {"ok": True, "published": False, "reason": "pending_action_already_linked", "pending_action": existing[-1]}

        from adaos.services import pending_actions

        action = pending_actions.publish_pending_action(
            ctx=ctx,
            webspace_id=webspace_id,
            kind=COMPATIBILITY_PENDING_ACTION_KIND,
            title="Runtime compatibility issue",
            summary=ticket["summary"],
            producer={"type": "system", "system_id": "development_tickets"},
            owner_scope=ticket.get("owner_scope") or {"type": "workspace", "id": "local"},
            domain_ref={
                "ticket_id": ticket["ticket_id"],
                "signal_ids": list(ticket.get("signal_ids") or []),
                "target_scope": ticket.get("target_scope") or {},
            },
            allowed_actions=[
                {"id": "preview_evidence", "label": "Preview evidence", "terminal": False},
                {"id": "postpone", "label": "Later", "terminal": True},
                {"id": "open_builder", "label": "Open Builder", "terminal": True},
                {"id": "start_autonomous_repair", "label": "Repair autonomously", "terminal": True},
                {"id": "refuse", "label": "Refuse", "terminal": True},
            ],
            response_topic=COMPATIBILITY_RESPONSE_TOPIC,
            metadata={
                "schema": "adaos.dev_ticket.compatibility.pending_action_metadata.v1",
                "ticket_id": ticket["ticket_id"],
                "signal_ids": list(ticket.get("signal_ids") or []),
                "target_scope": ticket.get("target_scope") or {},
                "policy": ticket.get("policy") or {},
                "code": _mapping(ticket.get("metadata")).get("code"),
            },
        )
        ref = {
            "id": action.get("id"),
            "kind": action.get("kind"),
            "status": action.get("status"),
            "created_at": action.get("created_at"),
        }
        updated = self._update_ticket(
            ticket["ticket_id"],
            pending_action_refs=_merge_refs(ticket.get("pending_action_refs") or [], [ref]),
            status="waiting_for_user",
            history_item={"kind": "pending_action_published", "pending_action_id": ref.get("id")},
        )
        return {"ok": True, "published": True, "pending_action": action, "ticket": updated}

    def handle_compatibility_response(
        self,
        *,
        ticket_id: str,
        response_action_id: str,
        pending_action_id: str | None = None,
        responder: Mapping[str, Any] | None = None,
        response_payload: Mapping[str, Any] | None = None,
        repair_service: BuilderRepairService | None = None,
    ) -> dict[str, Any]:
        action = _text(response_action_id)
        if not action:
            raise ValueError("response_action_id is required")
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        actor = _text(_mapping(responder).get("actor") or _mapping(responder).get("id")) or "pending_action"
        if action == "preview_evidence":
            updated = self._append_ticket_history(
                ticket["ticket_id"],
                {"kind": "evidence_previewed", "pending_action_id": _text(pending_action_id), "actor": actor},
            )
            return {"ok": True, "action": action, "ticket": updated, "repair": None}
        if action == "postpone":
            updated = self._update_ticket(
                ticket["ticket_id"],
                status="deferred",
                history_item={"kind": "postponed", "pending_action_id": _text(pending_action_id), "actor": actor},
            )
            return {"ok": True, "action": action, "ticket": updated, "repair": None}
        if action == "refuse":
            updated = self._close_ticket(
                ticket["ticket_id"],
                reason="refused",
                actor=actor,
                evidence_refs=_sequence_of_mappings(_mapping(response_payload).get("evidence_refs") or []),
            )
            return {"ok": True, "action": action, "ticket": updated, "repair": None}
        if action in {"open_builder", "start_autonomous_repair"}:
            mode = "interactive" if action == "open_builder" else "autonomous"
            result = self.handoff_ticket(
                ticket["ticket_id"],
                mode=mode,
                repair_service=repair_service,
                actor=actor,
            )
            return {"ok": True, "action": action, "ticket": result["ticket"], "repair": result["repair"]}
        raise ValueError(f"unsupported compatibility response action: {action}")

    def defer_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
        reason: str = "",
    ) -> dict[str, Any]:
        return self._update_ticket(
            ticket_id,
            status="deferred",
            history_item={
                "kind": "deferred",
                "actor": _text(actor) or "system",
                "reason": _text(reason) or None,
            },
        )

    def handoff_ticket(
        self,
        ticket_id: str,
        *,
        mode: str,
        repair_service: BuilderRepairService | None = None,
        actor: str,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        mode_token = _text(mode) or "interactive"
        if mode_token not in {"autonomous", "interactive"}:
            raise ValueError("mode must be autonomous or interactive")
        repair = self._create_builder_repair(ticket, mode=mode_token, repair_service=repair_service)
        updated = self._link_builder_repair(ticket["ticket_id"], repair, mode=mode_token, actor=_text(actor) or "system")
        return {"ok": True, "ticket": updated, "repair": repair}

    def close_ticket(
        self,
        ticket_id: str,
        *,
        reason: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        return self._close_ticket(
            ticket_id,
            reason=reason,
            actor=_text(actor) or "system",
            evidence_refs=evidence_refs,
        )

    def update_ticket_summary(
        self,
        ticket_id: str,
        *,
        summary: str,
        actor: str,
    ) -> dict[str, Any]:
        text = _text(summary)
        if not text:
            raise ValueError("ticket summary is required")
        actor_token = _text(actor) or "system"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            if _text(ticket.get("status")) in TERMINAL_TICKET_STATES:
                raise ValueError("terminal Dev Ticket cannot be edited")
            previous = _text(ticket.get("summary"))
            if previous == text:
                return _normalized_ticket(ticket)
            now = _now()
            ticket["summary"] = text
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "summary_updated",
                    "actor": actor_token,
                    "previous_summary": previous,
                    "summary": text,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def claim_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
        owner: str | None = None,
    ) -> dict[str, Any]:
        actor_token = _text(actor) or "system"
        owner_token = _text(owner) or actor_token
        return self._update_ticket(
            ticket_id,
            status="claimed",
            metadata={
                **_mapping((self.get_ticket(ticket_id) or {}).get("metadata")),
                "claimed_by": owner_token,
            },
            history_item={
                "kind": "claimed",
                "actor": actor_token,
                "owner": owner_token,
            },
        )

    def start_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        return self._update_ticket(
            ticket_id,
            status="in_progress",
            history_item={
                "kind": "in_progress",
                "actor": _text(actor) or "system",
            },
        )

    def comment_ticket(
        self,
        ticket_id: str,
        *,
        body: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        text = _text(body)
        if not text:
            raise ValueError("ticket comment is required")
        actor_token = _text(actor) or "system"
        refs = _sequence_of_mappings(evidence_refs)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            if _text(ticket.get("status")) in TERMINAL_TICKET_STATES:
                raise ValueError("terminal Dev Ticket cannot be commented")
            now = _now()
            comment = {
                "id": f"dcomment.{new_id()}",
                "body": text,
                "actor": actor_token,
                "evidence_refs": refs,
                "created_at": now,
            }
            comments = [dict(item) for item in ticket.get("comments") or [] if isinstance(item, Mapping)]
            comments.append(comment)
            ticket["comments"] = comments[-100:]
            if refs:
                ticket["evidence_refs"] = _merge_refs(ticket.get("evidence_refs") or [], refs)
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "commented",
                    "comment_id": comment["id"],
                    "actor": actor_token,
                    "recorded_at": now,
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def record_resolution(
        self,
        ticket_id: str,
        *,
        evidence_refs: Sequence[Mapping[str, Any]],
        actor: str,
        resolved_by_version: str | None = None,
        resolved_by_overlay: str | None = None,
        repair_service: BuilderRepairService | None = None,
        repair_id: str | None = None,
        capability_works: bool = True,
        regression_free: bool = True,
    ) -> dict[str, Any]:
        refs = _sequence_of_mappings(evidence_refs)
        if not refs:
            raise ValueError("ticket resolution requires evidence_refs")
        actor_token = _text(actor)
        if not actor_token:
            raise ValueError("ticket resolution requires actor")
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            raise KeyError(ticket_id)
        linked_repair_id = _text(repair_id) or self._latest_repair_id(ticket)
        if repair_service is not None and linked_repair_id:
            repair_service.record_acceptance(
                linked_repair_id,
                capability_works=capability_works,
                regression_free=regression_free,
                evidence_refs=refs,
                actor=actor_token,
            )
        closure = {
            "kind": "resolved",
            "actor": actor_token,
            "evidence_refs": refs,
            "resolved_by_version": _text(resolved_by_version) or None,
            "resolved_by_overlay": _text(resolved_by_overlay) or None,
            "repair_id": linked_repair_id or None,
            "capability_works": bool(capability_works),
            "regression_free": bool(regression_free),
            "recorded_at": _now(),
        }
        signal_status = "resolved_by_version" if closure["resolved_by_version"] else "resolved_by_overlay"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            stored = state["tickets"].get(ticket["ticket_id"])
            if not stored:
                raise KeyError(ticket["ticket_id"])
            stored["status"] = "resolved"
            stored["closure"] = closure
            stored["evidence_refs"] = _merge_refs(stored.get("evidence_refs") or [], refs)
            stored["updated_at"] = closure["recorded_at"]
            self._append_history(stored, {"kind": "resolved", "actor": actor_token, "recorded_at": closure["recorded_at"]})
            for signal_id in stored.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if not signal:
                    continue
                signal["status"] = signal_status
                signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], refs)
                signal["updated_at"] = closure["recorded_at"]
                signal["builder_ref"] = {
                    **_mapping(signal.get("builder_ref")),
                    "repair_id": linked_repair_id or None,
                    "resolved_by_version": closure["resolved_by_version"],
                    "resolved_by_overlay": closure["resolved_by_overlay"],
                }
                self._validate_signal(signal)
            self._validate_ticket(stored)
            self._write(state)
            return {"ok": True, "ticket": _normalized_ticket(stored), "closure": _clone(closure)}

    def verify_ticket(
        self,
        ticket_id: str,
        *,
        evidence_refs: Sequence[Mapping[str, Any]],
        actor: str,
        repair_id: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        refs = _sequence_of_mappings(evidence_refs)
        if not refs:
            raise ValueError("ticket verification requires evidence_refs")
        actor_token = _text(actor)
        if not actor_token:
            raise ValueError("ticket verification requires actor")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            if _text(ticket.get("status")) != "resolved":
                raise ValueError("ticket verification requires resolved status")
            now = _now()
            verification = {
                "kind": "verified",
                "actor": actor_token,
                "evidence_refs": refs,
                "repair_id": _text(repair_id) or self._latest_repair_id(ticket) or None,
                "notes": _text(notes) or None,
                "recorded_at": now,
            }
            ticket["status"] = "verified"
            ticket["verification"] = verification
            ticket["evidence_refs"] = _merge_refs(ticket.get("evidence_refs") or [], refs)
            ticket["updated_at"] = now
            self._append_history(ticket, {"kind": "verified", "actor": actor_token, "recorded_at": now})
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], refs)
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return {"ok": True, "ticket": _normalized_ticket(ticket), "verification": _clone(verification)}

    def reopen_ticket(
        self,
        ticket_id: str,
        *,
        actor: str,
        reason: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        reason_token = _text(reason)
        if not reason_token:
            raise ValueError("ticket reopen requires reason")
        actor_token = _text(actor) or "system"
        refs = _sequence_of_mappings(evidence_refs)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            previous_status = _text(ticket.get("status"))
            now = _now()
            ticket["status"] = "in_progress" if ticket.get("builder_refs") else "accepted"
            ticket["reopened_at"] = now
            if refs:
                ticket["evidence_refs"] = _merge_refs(ticket.get("evidence_refs") or [], refs)
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "reopened",
                    "actor": actor_token,
                    "reason": reason_token,
                    "previous_status": previous_status or None,
                    "recorded_at": now,
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "in_progress"
                    if refs:
                        signal["evidence_refs"] = _merge_refs(signal.get("evidence_refs") or [], refs)
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def relate_ticket(
        self,
        ticket_id: str,
        *,
        related_ticket_id: str,
        relation: str = "related",
        actor: str,
    ) -> dict[str, Any]:
        related = _text(related_ticket_id)
        if not related:
            raise ValueError("related_ticket_id is required")
        relation_token = _text(relation) or "related"
        actor_token = _text(actor) or "system"
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            if related not in state["tickets"]:
                raise KeyError(related)
            ref = {"type": "dev_ticket", "ticket_id": related, "relation": relation_token}
            ticket["related_refs"] = _merge_refs(ticket.get("related_refs") or [], [ref])
            ticket["updated_at"] = _now()
            self._append_history(
                ticket,
                {
                    "kind": "related",
                    "actor": actor_token,
                    "related_ticket_id": related,
                    "relation": relation_token,
                    "recorded_at": ticket["updated_at"],
                },
            )
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def duplicate_ticket(
        self,
        ticket_id: str,
        *,
        duplicate_of: str,
        actor: str,
    ) -> dict[str, Any]:
        duplicate_target = _text(duplicate_of)
        if not duplicate_target:
            raise ValueError("duplicate_of is required")
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            if duplicate_target not in state["tickets"]:
                raise KeyError(duplicate_target)
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            ticket["related_refs"] = _merge_refs(
                ticket.get("related_refs") or [],
                [{"type": "dev_ticket", "ticket_id": duplicate_target, "relation": "duplicate_of"}],
            )
            now = _now()
            ticket["status"] = "superseded"
            ticket["closure"] = {
                "kind": "closed",
                "reason": "duplicate",
                "actor": _text(actor) or "system",
                "duplicate_of": duplicate_target,
                "evidence_refs": [],
                "recorded_at": now,
            }
            ticket["updated_at"] = now
            self._append_history(
                ticket,
                {
                    "kind": "duplicated",
                    "actor": _text(actor) or "system",
                    "duplicate_of": duplicate_target,
                    "recorded_at": now,
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "superseded"
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        signal = self._read()["signals"].get(_text(signal_id))
        return _clone(signal) if signal else None

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ticket = self._read()["tickets"].get(_text(ticket_id))
        return _normalized_ticket(ticket) if ticket else None

    def list_tickets(
        self,
        *,
        status: str | None = None,
        status_group: str | None = None,
        target_id: str | None = None,
        target_tokens: Sequence[str] = (),
        kind: str | None = None,
        scenario_id: str | None = None,
        skill_id: str | None = None,
        modal_id: str | None = None,
        component: str | None = None,
        severity: str | None = None,
        blocking: bool | None = None,
        source: str | None = None,
        owner: str | None = None,
        updated_since: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        tickets = list(self._read()["tickets"].values())
        if status:
            allowed = {_text(part) for part in _text(status).split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("status")) in allowed]
        group = _text(status_group)
        if group:
            allowed = set()
            for part in group.split(","):
                allowed.update(TICKET_STATUS_GROUPS.get(_text(part), {_text(part)}))
            tickets = [item for item in tickets if _text(item.get("status")) in allowed]
        if target_id:
            token = _text(target_id)
            tickets = [
                item
                for item in tickets
                if _text(_mapping(item.get("target_scope")).get("id")) == token
            ]
        tokens = {_text(item) for item in target_tokens if _text(item)}
        scoped_tokens = [_text(scenario_id), _text(skill_id), _text(modal_id), _text(component)]
        tokens.update(item for item in scoped_tokens if item)
        if tokens:
            tickets = [item for item in tickets if _ticket_scope_tokens(item) & tokens]
        kind_token = _text(kind)
        if kind_token:
            allowed = {_text(part) for part in kind_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("kind")) in allowed]
        severity_token = _text(severity)
        if severity_token:
            allowed = {_text(part) for part in severity_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("severity")) in allowed]
        if blocking is not None:
            tickets = [item for item in tickets if bool(item.get("blocking")) is bool(blocking)]
        source_token = _text(source)
        if source_token:
            allowed = {_text(part) for part in source_token.split(",") if _text(part)}
            tickets = [item for item in tickets if _text(item.get("source")) in allowed]
        owner_token = _text(owner)
        if owner_token:
            tickets = [item for item in tickets if owner_token in _ticket_owner_tokens(item)]
        since_token = _text(updated_since)
        if since_token:
            tickets = [item for item in tickets if _text(item.get("updated_at") or item.get("created_at")) >= since_token]
        search_token = _text(search).lower()
        if search_token:
            tickets = [item for item in tickets if search_token in _ticket_search_text(item)]
        sorted_tickets = sorted(tickets, key=lambda item: item.get("updated_at") or item.get("created_at") or "")
        if limit is not None and int(limit) >= 0:
            sorted_tickets = sorted_tickets[-int(limit):]
        return [_normalized_ticket(item) for item in sorted_tickets]

    def list_artifacts(self, ticket_id: str | None = None) -> list[dict[str, Any]]:
        root = self.root / "artifacts"
        if not root.is_dir():
            return []
        wanted: set[str] = set()
        if _text(ticket_id):
            ticket = self.get_ticket(_text(ticket_id))
            if not ticket:
                raise KeyError(_text(ticket_id))
            for ref in [
                *_sequence_of_mappings(ticket.get("artifact_refs") or []),
                *_sequence_of_mappings(ticket.get("evidence_refs") or []),
            ]:
                artifact_id = _artifact_id_from_ref(ref)
                if artifact_id:
                    wanted.add(artifact_id)
        items: list[dict[str, Any]] = []
        for path in root.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            artifact_id = _text(manifest.get("artifact_id") or path.stem)
            if wanted and artifact_id not in wanted:
                continue
            items.append({**manifest, "manifest_path": str(path)})
        return sorted(items, key=lambda item: _text(item.get("artifact_id")))

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        token = _text(artifact_id)
        if not token or "/" in token or "\\" in token or ".." in token:
            return None
        manifest_path = self.root / "artifacts" / f"{token}.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        file_name = _text(manifest.get("file_name"))
        file_path = self.root / "artifacts" / file_name if file_name else None
        return {
            **manifest,
            "manifest_path": str(manifest_path),
            "path": str(file_path) if file_path else None,
            "exists": bool(file_path and file_path.is_file()),
        }

    def _create_builder_repair(
        self,
        ticket: Mapping[str, Any],
        *,
        mode: str,
        repair_service: BuilderRepairService | None,
    ) -> dict[str, Any]:
        service = repair_service or BuilderRepairService(state_dir=self.state_dir)
        target = _mapping(ticket.get("target_scope"))
        ticket_id = _text(ticket.get("ticket_id"))
        signal_ids = [_text(item) for item in ticket.get("signal_ids") or [] if _text(item)]
        source_refs = [
            {"type": "dev_ticket", "id": ticket_id},
            *({"type": "development_signal", "id": signal_id} for signal_id in signal_ids),
            *_sequence_of_mappings(ticket.get("evidence_refs") or []),
        ]
        report = service.report(
            project_id=_project_id_from_target(target),
            signal_type="guard",
            summary=_text(ticket.get("summary")) or "Runtime compatibility debt",
            source_refs=source_refs,
            context={
                "development_ticket": {
                    "ticket_id": ticket_id,
                    "signal_ids": signal_ids,
                    "handoff_mode": mode,
                },
                "target_scope": target,
                "development_source": development_source_options(target),
                "compatibility": _mapping(ticket.get("metadata")),
                "policy": _mapping(ticket.get("policy")),
                "acceptance": {
                    "checks": [
                        "strict skill/scenario validation passes",
                        "activation or smoke import passes",
                        "expected receiver admission passes",
                        "unrelated receiver admission remains denied",
                    ]
                },
            },
            design_time_fixable=bool(_mapping(ticket.get("policy")).get("design_time_fixable", True)),
            dedup_key=f"repair:{ticket.get('dedup_key')}",
        )
        return report["task"]

    def _link_builder_repair(self, ticket_id: str, repair: Mapping[str, Any], *, mode: str, actor: str) -> dict[str, Any]:
        repair_id = _text(repair.get("repair_id"))
        ref = {
            "type": "builder_repair_task",
            "repair_id": repair_id,
            "mode": mode,
            "status": repair.get("status"),
            "created_at": repair.get("created_at"),
        }
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            ticket["status"] = "in_builder"
            ticket["builder_refs"] = _merge_refs(ticket.get("builder_refs") or [], [ref])
            ticket["updated_at"] = _now()
            self._append_history(
                ticket,
                {
                    "kind": "builder_handoff",
                    "mode": mode,
                    "repair_id": repair_id,
                    "actor": actor,
                    "recorded_at": ticket["updated_at"],
                },
            )
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    signal["status"] = "repair_created"
                    signal["builder_ref"] = {
                        **_mapping(signal.get("builder_ref")),
                        "repair_id": repair_id,
                        "handoff_mode": mode,
                    }
                    signal["updated_at"] = ticket["updated_at"]
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _update_ticket(self, ticket_id: str, *, history_item: Mapping[str, Any] | None = None, **patch: Any) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            for key, value in patch.items():
                ticket[key] = value
            ticket["updated_at"] = _now()
            if history_item:
                self._append_history(ticket, {**dict(history_item), "recorded_at": ticket["updated_at"]})
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _append_ticket_history(self, ticket_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            ticket["updated_at"] = _now()
            self._append_history(ticket, {**dict(item), "recorded_at": ticket["updated_at"]})
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    def _close_ticket(
        self,
        ticket_id: str,
        *,
        reason: str,
        actor: str,
        evidence_refs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        reason_token = _text(reason) or "closed"
        normal_close = reason_token in {"closed", "closed_from_client", "done", "verified"}
        ticket_status = {
            "duplicate": "superseded",
            "superseded": "superseded",
            "stale": "stale",
        }.get(reason_token, "closed")
        signal_status = {
            "refused": "rejected",
            "duplicate": "superseded",
            "superseded": "superseded",
            "stale": "stale",
            "not-design-time-fixable": "not_design_time_fixable",
            "not_design_time_fixable": "not_design_time_fixable",
        }.get(reason_token)
        with _LOCK, mutation_lock(self.lock_path, timeout_s=30.0):
            state = self._read()
            ticket = state["tickets"].get(_text(ticket_id))
            if not ticket:
                raise KeyError(ticket_id)
            if normal_close and _text(ticket.get("status")) != "verified":
                raise ValueError("normal Dev Ticket closure requires verified status")
            now = _now()
            ticket["status"] = ticket_status
            ticket["closure"] = {
                "kind": "closed",
                "reason": reason_token,
                "actor": _text(actor) or "system",
                "evidence_refs": _merge_refs([], evidence_refs),
                "recorded_at": now,
            }
            ticket["updated_at"] = now
            self._append_history(ticket, {"kind": "closed", "reason": reason_token, "actor": actor, "recorded_at": now})
            for signal_id in ticket.get("signal_ids") or []:
                signal = state["signals"].get(signal_id)
                if signal:
                    if signal_status:
                        signal["status"] = signal_status
                    signal["updated_at"] = now
                    self._validate_signal(signal)
            self._validate_ticket(ticket)
            self._write(state)
            return _normalized_ticket(ticket)

    @staticmethod
    def _latest_repair_id(ticket: Mapping[str, Any]) -> str:
        for ref in reversed(_sequence_of_mappings(ticket.get("builder_refs") or [])):
            repair_id = _text(ref.get("repair_id"))
            if repair_id:
                return repair_id
        return ""

    @staticmethod
    def _append_history(record: dict[str, Any], item: Mapping[str, Any]) -> None:
        history = [dict(entry) for entry in record.get("history") or [] if isinstance(entry, Mapping)]
        history.append(dict(item))
        record["history"] = history[-100:]

    def _read(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"schema": STATE_SCHEMA, "signals": {}, "tickets": {}}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("development ticket state is corrupt")
        signals = value.get("signals")
        tickets = value.get("tickets")
        if not isinstance(signals, Mapping) or not isinstance(tickets, Mapping):
            raise ValueError("development ticket state is corrupt")
        return {"schema": STATE_SCHEMA, "signals": dict(signals), "tickets": dict(tickets)}

    def _write(self, state: Mapping[str, Any]) -> None:
        atomic_write_json(self.state_path, dict(state))

    @staticmethod
    def _validate_signal(signal: Mapping[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(_schema("development_signal.v1")).iter_errors(signal),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ValueError(f"invalid Development Signal: {errors[0].message}")

    @staticmethod
    def _validate_ticket(ticket: Mapping[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(_schema("dev_ticket.v1")).iter_errors(ticket),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ValueError(f"invalid Dev Ticket: {errors[0].message}")


@subscribe(COMPATIBILITY_RESPONSE_TOPIC)
async def _on_compatibility_pending_action_response(evt: Any) -> None:
    payload = _event_payload(evt)
    response = _mapping(payload.get("response"))
    domain_ref = _mapping(payload.get("domain_ref"))
    ticket_id = _text(domain_ref.get("ticket_id") or _mapping(response.get("payload")).get("ticket_id"))
    response_action_id = _text(payload.get("response_action_id") or response.get("response_action_id"))
    if not ticket_id or not response_action_id:
        return
    responder = _mapping(response.get("responder"))
    try:
        DevelopmentTicketService().handle_compatibility_response(
            ticket_id=ticket_id,
            response_action_id=response_action_id,
            pending_action_id=_text(payload.get("pending_action_id")),
            responder=responder,
            response_payload=_mapping(response.get("payload")),
        )
    except Exception:
        _log.warning("failed to handle compatibility ticket pending action response", exc_info=True)


__all__ = [
    "ACTIVE_SIGNAL_STATES",
    "ACTIVE_TICKET_STATES",
    "COMPATIBILITY_PENDING_ACTION_KIND",
    "COMPATIBILITY_RESPONSE_TOPIC",
    "DEVELOPMENT_SIGNAL_SCHEMA",
    "DEV_TICKET_SCHEMA",
    "DevelopmentTicketService",
    "RECEIVER_COMPATIBILITY_REASONS",
    "STATE_SCHEMA",
    "TERMINAL_TICKET_STATES",
]
