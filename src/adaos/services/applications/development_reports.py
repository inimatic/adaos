from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from adaos.domain.artifact_release import canonical_json_bytes
from adaos.domain.development_report import (
    DevelopmentReport,
    DevelopmentReportAck,
    DevelopmentReportIntake,
    DevelopmentReportResync,
    DevelopmentReportStatusEvent,
)
from adaos.services.applications.report_admission import (
    DevelopmentReportAdmissionService,
    DevelopmentReportClassifier,
)
from adaos.services.applications.report_crypto import DevelopmentReportEnvelopeCrypto
from adaos.services.applications.report_directory import SubnetKeyDirectoryClient
from adaos.services.applications.report_keys import SubnetPurposeKeyStore
from adaos.services.applications.report_relay import (
    DevelopmentReportRelayPeer,
    DevelopmentReportRelayBackpressure,
    DevelopmentReportRelayError,
    DurableDevelopmentReportRelay,
)
from adaos.services.applications.store import ApplicationStore
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.development_tickets import DevelopmentTicketService


class DevelopmentReportServiceError(ValueError):
    pass


_PUBLISHER_TRANSITIONS = {
    "received": {"triaged", "accepted", "declined", "duplicate"},
    "triaged": {"accepted", "declined", "duplicate"},
    "accepted": {"planned"},
    "planned": {"prerelease_available", "released"},
    "prerelease_available": {"prerelease_available", "released"},
    "released": {"released"},
    "still_reproduces": {"planned", "prerelease_available", "released"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("\x1f".join(str(item) for item in parts).encode("utf-8")).hexdigest()
    return f"{prefix}.{digest[:32]}"


class DevelopmentReportStore:
    def __init__(self, state_dir: Path, *, subnet_ref: str) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.subnet_ref = str(subnet_ref).strip().lower()

    @property
    def root(self) -> Path:
        path = self.state_dir / "applications" / "development_reports" / self.subnet_ref.replace(":", "_")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.root / ".mutation.lock"

    def read(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "schema": "adaos.application.development_report_store.v1",
                "reports": {}, "intakes": {}, "raw_intake": {}, "events": {},
                "processed_messages": {}, "outbound_messages": {}, "outbox": {},
            }
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DevelopmentReportServiceError("Development Report store is unreadable") from exc
        if not isinstance(state, dict) or state.get("schema") != "adaos.application.development_report_store.v1":
            raise DevelopmentReportServiceError("Development Report store is invalid")
        state.setdefault("outbox", {})
        for field in ("reports", "intakes", "raw_intake", "events", "processed_messages", "outbound_messages", "outbox"):
            if not isinstance(state.get(field), dict):
                raise DevelopmentReportServiceError(f"Development Report {field} state is invalid")
        return state

    def mutate(self, callback: Callable[[dict[str, Any]], Any]) -> Any:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            state = self.read()
            result = callback(state)
            atomic_write_json(self.state_path, state)
            return result


class DevelopmentReportService:
    def __init__(
        self,
        state_dir: Path,
        *,
        subnet_ref: str,
        application_store: ApplicationStore,
        key_store: SubnetPurposeKeyStore,
        directory: SubnetKeyDirectoryClient,
        relay: DurableDevelopmentReportRelay,
        ticket_service: DevelopmentTicketService | None = None,
        classifier: DevelopmentReportClassifier | None = None,
        relay_peers: Mapping[str, DevelopmentReportRelayPeer] | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.subnet_ref = str(subnet_ref or "").strip().lower()
        if not self.subnet_ref.startswith("subnet:"):
            raise DevelopmentReportServiceError("subnet_ref must use subnet:<id>")
        self.application_store = application_store
        self.key_store = key_store
        self.directory = directory
        self.relay = relay
        self.relay_peers = {
            str(zone).strip().lower(): peer
            for zone, peer in dict(relay_peers or {}).items()
        }
        self.ticket_service = ticket_service or DevelopmentTicketService(state_dir=self.state_dir)
        self.admission = DevelopmentReportAdmissionService(application_store=application_store, classifier=classifier)
        self.crypto = DevelopmentReportEnvelopeCrypto(key_store=key_store, directory=directory, now=now)
        self.store = DevelopmentReportStore(self.state_dir, subnet_ref=self.subnet_ref)
        self.now = now

    def ensure_message_keys(self) -> tuple[dict[str, Any], dict[str, Any]]:
        signing = self.key_store.ensure_key(self.subnet_ref, "message_signing")
        encryption = self.key_store.ensure_key(self.subnet_ref, "message_encryption")
        return signing.to_dict(), encryption.to_dict()

    def list_reports(self) -> list[dict[str, Any]]:
        state = self.store.read()
        return [dict(state["reports"][key]) for key in sorted(state["reports"])]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        value = self.store.read()["reports"].get(report_id)
        return dict(value) if isinstance(value, dict) else None

    def list_publisher_intakes(self) -> list[dict[str, Any]]:
        state = self.store.read()
        return [dict(state["intakes"][key]) for key in sorted(state["intakes"])]

    def public_status(self, report_id: str) -> dict[str, Any] | None:
        events = self.store.read()["events"].get(report_id) or []
        return dict(events[-1]) if events else None

    def _known_idempotency(self, state: Mapping[str, Any], reporter_subnet_ref: str) -> list[str]:
        values: list[str] = []
        for report in state["raw_intake"].values():
            if report.get("reporter_subnet_ref") == reporter_subnet_ref:
                values.append(str(report.get("idempotency_key") or ""))
        for report in state["reports"].values():
            if report.get("reporter_subnet_ref") == reporter_subnet_ref:
                values.append(str(report.get("idempotency_key") or ""))
        return values

    def _recent_count(self, state: Mapping[str, Any], reporter_subnet_ref: str) -> int:
        cutoff = self.now() - timedelta(days=1)
        values = list(state["raw_intake"].values()) + list(state["reports"].values())
        return sum(1 for item in values if item.get("reporter_subnet_ref") == reporter_subnet_ref and datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00")) >= cutoff)

    def create_report(
        self,
        *,
        application_id: str,
        summary: str,
        details: str,
        idempotency_key: str,
        evidence: Sequence[Mapping[str, Any]] = (),
        installed_release_digest: str | None = None,
    ) -> dict[str, Any]:
        state = self.store.read()
        for raw in state["reports"].values():
            if raw.get("idempotency_key") == idempotency_key:
                return {"report": dict(raw), "duplicate": True, "message_id": state["outbound_messages"].get(raw["report_id"])}
        application = self.application_store.get_application(application_id)
        installation = self.application_store.get_installation(application_id)
        release_digest = str(installed_release_digest or installation.installed_release_digest)
        signing = self.key_store.active_key(self.subnet_ref, "message_signing")
        report_id = _id("report", self.subnet_ref, application_id, idempotency_key)
        report = DevelopmentReport(
            report_id=report_id, application_id=application_id, publisher_ref=application.publisher_ref,
            reporter_subnet_ref=self.subnet_ref, reporter_key_id=signing.key_id,
            installed_release_digest=release_digest,
            installation_proof={"installation_id": installation.installation_id, "application_id": application_id, "release_digest": release_digest, "installation_revision": installation.revision},
            idempotency_key=idempotency_key, summary=summary, details=details,
            evidence=tuple(dict(item) for item in evidence), status="queued", revision=1,
        )
        self.admission.admit(
            report, recent_report_count=self._recent_count(state, self.subnet_ref),
            known_idempotency_keys=self._known_idempotency(state, self.subnet_ref),
            verify_local_installation=True,
        )
        message_id = _id("msg", report.report_id, "report", report.revision)
        envelope = self.crypto.seal(
            report.to_dict(), message_kind="report", sender_subnet_ref=self.subnet_ref,
            recipient_subnet_ref=application.publisher_ref, message_id=message_id,
        )
        def persist(current: dict[str, Any]) -> None:
            existing = current["reports"].get(report.report_id)
            if existing is not None and existing != report.to_dict():
                raise DevelopmentReportServiceError("DevelopmentReport idempotency identity collision")
            current["reports"][report.report_id] = report.to_dict()
            current["outbound_messages"][report.report_id] = message_id
            current["outbox"].setdefault(message_id, {"kind": "report", "envelope": envelope.to_dict(), "status": "queued", "created_at": _iso(self.now())})
        self.store.mutate(persist)
        return {"report": report.to_dict(), "duplicate": False, "message_id": message_id, "relay": self._dispatch_outbox(message_id)}

    def _dispatch_outbox(self, message_id: str) -> dict[str, Any]:
        item = self.store.read()["outbox"].get(message_id)
        if item is None:
            return {"accepted": True, "duplicate": True, "message_id": message_id, "local_status": "sent"}
        try:
            result = self.relay.enqueue(item["envelope"])
        except DevelopmentReportRelayBackpressure as exc:
            return {"accepted": False, "duplicate": False, "message_id": message_id, "local_status": "queued", "reason": str(exc)}
        destination_zone = str(item["envelope"].get("destination_zone") or "").lower()
        if destination_zone and destination_zone != self.relay.zone_id:
            peer = self.relay_peers.get(destination_zone)
            if peer is None:
                result = {**result, "forward_status": "queued", "forward_reason": "peer_not_configured"}
            else:
                try:
                    receipt = self.relay.forward(message_id, peer)
                    result = {**result, "forward_status": "forwarded", "forward_receipt": receipt}
                except Exception as exc:
                    result = {
                        **result,
                        "forward_status": "queued",
                        "forward_reason": (
                            str(exc)
                            if isinstance(exc, DevelopmentReportRelayError)
                            else "peer_transport_unavailable"
                        ),
                    }

        def mark_sent(state: dict[str, Any]) -> None:
            current = state["outbox"].get(message_id)
            if current is None:
                return
            state["outbox"].pop(message_id, None)
            state["outbound_messages"][message_id] = {
                "status": "sent", "kind": current["kind"], "sent_at": _iso(self.now()),
            }
        self.store.mutate(mark_sent)
        return {**result, "local_status": "sent"}

    def flush_outbox(self, *, limit: int = 100) -> dict[str, Any]:
        message_ids = list(sorted(self.store.read()["outbox"]))[:max(1, min(int(limit), 500))]
        results = []
        for message_id in message_ids:
            result = self._dispatch_outbox(message_id)
            results.append(result)
            if result.get("local_status") == "queued":
                break
        return {
            "attempted": len(results),
            "remaining": len(self.store.read()["outbox"]),
            "results": results,
            "relay": self.flush_relay_forwards(limit=limit),
        }

    def flush_relay_forwards(self, *, limit: int = 100) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in self.relay.pending_forwards(limit=limit):
            peer = self.relay_peers.get(item["destination_zone"])
            if peer is None:
                results.append({**item, "status": "queued", "reason": "peer_not_configured"})
                continue
            try:
                receipt = self.relay.forward(item["message_id"], peer)
            except Exception as exc:
                results.append(
                    {
                        **item,
                        "status": "queued",
                        "reason": (
                            str(exc)
                            if isinstance(exc, DevelopmentReportRelayError)
                            else "peer_transport_unavailable"
                        ),
                    }
                )
                continue
            results.append({**item, "status": "forwarded", "receipt": receipt})
        return {
            "attempted": len(results),
            "remaining": len(self.relay.pending_forwards(limit=500)),
            "results": results,
        }

    def _append_event(self, state: dict[str, Any], event: DevelopmentReportStatusEvent) -> None:
        events = state["events"].setdefault(event.report_id, [])
        if events:
            current = DevelopmentReportStatusEvent.from_mapping(events[-1])
            if event.revision <= current.revision:
                if event.to_dict() == current.to_dict():
                    return
                raise DevelopmentReportServiceError("public report status rollback or fork detected")
            if event.revision != current.revision + 1:
                raise DevelopmentReportServiceError("public report status cursor gap detected")
        elif event.revision != 1:
            raise DevelopmentReportServiceError("public report status must start at revision 1")
        events.append(event.to_dict())

    def _next_event(
        self,
        report: DevelopmentReport,
        *,
        status: str,
        reason_code: str | None = None,
        release_digest: str | None = None,
    ) -> DevelopmentReportStatusEvent:
        state = self.store.read()
        events = state["events"].get(report.report_id) or []
        revision = int(events[-1]["revision"]) + 1 if events else 1
        return DevelopmentReportStatusEvent(
            event_id=_id("report_event", report.report_id, revision, status, release_digest or ""),
            report_id=report.report_id, application_id=report.application_id,
            publisher_ref=report.publisher_ref, reporter_subnet_ref=report.reporter_subnet_ref,
            status=status, revision=revision, reason_code=reason_code,
            release_digest=release_digest, occurred_at=_iso(self.now()),
        )

    def _send_status(self, report: DevelopmentReport, event: DevelopmentReportStatusEvent) -> dict[str, Any]:
        message_id = _id("msg", report.report_id, "status", event.revision)
        envelope = self.crypto.seal(
            event.to_dict(), message_kind="status", sender_subnet_ref=self.subnet_ref,
            recipient_subnet_ref=report.reporter_subnet_ref, message_id=message_id,
        )
        def persist(state: dict[str, Any]) -> None:
            self._append_event(state, event)
            state["outbox"].setdefault(message_id, {"kind": "status", "envelope": envelope.to_dict(), "status": "queued", "created_at": _iso(self.now())})
        self.store.mutate(persist)
        result = self._dispatch_outbox(message_id)
        return {"event": event.to_dict(), "message_id": message_id, "relay": result}

    def receive(self, *, limit: int = 20) -> list[dict[str, Any]]:
        deliveries = self.relay.poll(self.subnet_ref, limit=limit)
        results: list[dict[str, Any]] = []
        for delivery in deliveries:
            envelope = delivery["envelope"]
            message_id = envelope["message_id"]
            state = self.store.read()
            processed = state["processed_messages"].get(message_id)
            if processed is not None:
                disposition = "duplicate"
                result = dict(processed)
            else:
                try:
                    payload = self.crypto.open(envelope, recipient_subnet_ref=self.subnet_ref)
                    result = self._consume(envelope, payload)
                    disposition = "accepted"
                except Exception as exc:
                    result = {"ok": False, "message_id": message_id, "error": type(exc).__name__, "detail": str(exc)}
                    disposition = "rejected"
                self.store.mutate(lambda current: current["processed_messages"].__setitem__(message_id, dict(result)))
            ack = DevelopmentReportAck(
                message_id=message_id, recipient_subnet_ref=self.subnet_ref,
                disposition=disposition, delivery_id=delivery["delivery_id"], accepted_at=_iso(self.now()),
            )
            self.relay.acknowledge(ack)
            results.append({**result, "delivery_disposition": disposition})
        return results

    def _consume(self, envelope: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        kind = envelope["message_kind"]
        if kind == "report":
            return self._consume_report(envelope, payload)
        if kind == "status":
            return self._consume_status(envelope, payload)
        if kind == "verification":
            return self._consume_verification(envelope, payload)
        if kind == "resync":
            return self._consume_resync(envelope, payload)
        if kind == "resync_snapshot":
            return self._consume_resync_snapshot(envelope, payload)
        raise DevelopmentReportServiceError("unsupported report relay message kind")

    def _consume_report(self, envelope: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        report = DevelopmentReport.from_mapping(payload)
        if report.reporter_subnet_ref != envelope["sender_subnet_ref"] or report.publisher_ref != self.subnet_ref:
            raise DevelopmentReportServiceError("DevelopmentReport envelope identity does not match payload")
        state = self.store.read()
        existing = state["raw_intake"].get(report.report_id)
        raw_digest = f"sha256:{hashlib.sha256(canonical_json_bytes(report.to_dict())).hexdigest()}"
        if existing is not None:
            if f"sha256:{hashlib.sha256(canonical_json_bytes(existing)).hexdigest()}" != raw_digest:
                raise DevelopmentReportServiceError("DevelopmentReport identity collision")
            return {"ok": True, "duplicate": True, "report_id": report.report_id}
        admission = self.admission.admit(
            report, recent_report_count=self._recent_count(state, report.reporter_subnet_ref),
            known_idempotency_keys=self._known_idempotency(state, report.reporter_subnet_ref),
        )
        intake = DevelopmentReportIntake(
            intake_id=_id("intake", self.subnet_ref, report.report_id), report_id=report.report_id,
            application_id=report.application_id, reporter_subnet_ref=report.reporter_subnet_ref,
            raw_payload_digest=admission.raw_payload_digest,
            normalized_summary=admission.normalized_summary, normalized_details=admission.normalized_details,
            redaction_findings=admission.redaction_findings,
            model_classification=admission.model_classification, admission=admission.to_dict(),
            status="quarantined", revision=1, received_at=_iso(self.now()), updated_at=_iso(self.now()),
        )

        def persist(current: dict[str, Any]) -> None:
            current["raw_intake"][report.report_id] = report.to_dict()
            current["intakes"][report.report_id] = intake.to_dict()
        self.store.mutate(persist)
        event = self._next_event(report, status="received")
        self._send_status(report, event)
        return {"ok": True, "duplicate": False, "report_id": report.report_id, "intake_id": intake.intake_id}

    def _consume_status(self, envelope: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        event = DevelopmentReportStatusEvent.from_mapping(payload)
        report_raw = self.store.read()["reports"].get(event.report_id)
        if report_raw is None:
            raise DevelopmentReportServiceError("status event references an unknown local report")
        report = DevelopmentReport.from_mapping(report_raw)
        if envelope["sender_subnet_ref"] != report.publisher_ref or event.publisher_ref != report.publisher_ref or event.reporter_subnet_ref != self.subnet_ref:
            raise DevelopmentReportServiceError("status event publisher identity is invalid")
        if event.release_digest is not None:
            release = self.application_store.get_release(event.application_id, event.release_digest)
            if event.report_id not in release.addresses_report_ids:
                raise DevelopmentReportServiceError("status release does not address this report")

        def persist(state: dict[str, Any]) -> None:
            self._append_event(state, event)
            current = DevelopmentReport.from_mapping(state["reports"][event.report_id])
            state["reports"][event.report_id] = replace(current, status=event.status, revision=max(current.revision + 1, event.revision), updated_at=_iso(self.now())).to_dict()
        self.store.mutate(persist)
        return {"ok": True, "report_id": event.report_id, "status": event.status, "revision": event.revision}

    def triage(self, report_id: str, *, outcome: str = "triaged", reason_code: str | None = None) -> dict[str, Any]:
        if outcome not in {"triaged", "declined", "duplicate"}:
            raise DevelopmentReportServiceError("triage outcome is invalid")
        report, intake = self._publisher_records(report_id)
        if intake.status not in {"quarantined", "triaged"}:
            raise DevelopmentReportServiceError("publisher intake cannot be triaged from current state")
        updated = replace(intake, status=outcome, revision=intake.revision + 1, updated_at=_iso(self.now()))
        self.store.mutate(lambda state: state["intakes"].__setitem__(report_id, updated.to_dict()))
        return self._send_status(report, self._next_event(report, status=outcome, reason_code=reason_code))

    def _publisher_records(self, report_id: str) -> tuple[DevelopmentReport, DevelopmentReportIntake]:
        state = self.store.read()
        report_raw = state["raw_intake"].get(report_id)
        intake_raw = state["intakes"].get(report_id)
        if report_raw is None or intake_raw is None:
            raise DevelopmentReportServiceError("publisher intake is unknown")
        return DevelopmentReport.from_mapping(report_raw), DevelopmentReportIntake.from_mapping(intake_raw)

    def accept(self, report_id: str, *, actor: str, policy_ref: str | None = None) -> dict[str, Any]:
        report, intake = self._publisher_records(report_id)
        if intake.status == "accepted":
            return {"accepted": True, "duplicate": True, "intake": intake.to_dict(), "ticket_refs": list(intake.internal_ticket_refs)}
        if intake.status not in {"quarantined", "triaged"}:
            raise DevelopmentReportServiceError("publisher intake cannot be accepted from current state")
        metadata = {
            "external_development_report": {
                "report_id": report.report_id, "reporter_subnet_ref": report.reporter_subnet_ref,
                "application_id": report.application_id, "installed_release_digest": report.installed_release_digest,
                "normalized_details": intake.normalized_details,
                "redaction_findings": list(intake.redaction_findings),
                "model_classification": dict(intake.model_classification) if intake.model_classification is not None else None,
                "raw_payload_digest": intake.raw_payload_digest,
            }
        }
        signal_result = self.ticket_service.capture_signal(
            kind="development_request", summary=intake.normalized_summary,
            owner_scope={"type": "application_publisher", "id": self.subnet_ref},
            origin_scope={"type": "development_report", "id": report.report_id, "subnet_ref": report.reporter_subnet_ref},
            target_scope={"type": "application", "id": report.application_id, "release_digest": report.installed_release_digest},
            source="application_report", dedup_key=f"external-report:{self.subnet_ref}:{report.report_id}",
            evidence_refs=tuple(dict(item) for item in intake.admission.get("normalized_evidence") or ()),
            metadata=metadata, policy={"accepted_by": actor, "policy_ref": policy_ref},
            relation_refs=({"type": "development_report", "id": report.report_id},),
        )
        ticket_result = self.ticket_service.ensure_ticket_for_signal(
            signal_result["signal"], kind="development_request", status="proposed",
            source="application_report", dedup_key=f"external-report-ticket:{self.subnet_ref}:{report.report_id}",
            metadata=metadata, policy={"accepted_by": actor, "policy_ref": policy_ref},
            relation_refs=({"type": "development_report", "id": report.report_id},),
        )
        refs = (str(ticket_result["ticket"]["ticket_id"]),)
        updated = replace(intake, status="accepted", revision=intake.revision + 1, internal_ticket_refs=refs, updated_at=_iso(self.now()))
        self.store.mutate(lambda state: state["intakes"].__setitem__(report_id, updated.to_dict()))
        event_result = self._send_status(report, self._next_event(report, status="accepted"))
        return {"accepted": True, "duplicate": False, "intake": updated.to_dict(), "ticket_refs": list(refs), "public": event_result}

    def set_public_status(
        self,
        report_id: str,
        *,
        status: str,
        reason_code: str | None = None,
        release_digest: str | None = None,
    ) -> dict[str, Any]:
        report, intake = self._publisher_records(report_id)
        if intake.status != "accepted":
            raise DevelopmentReportServiceError("only an accepted publisher intake may advance work status")
        current_raw = self.public_status(report_id)
        current = str(current_raw.get("status") if current_raw else "received")
        if status not in _PUBLISHER_TRANSITIONS.get(current, set()):
            raise DevelopmentReportServiceError(f"invalid public report transition: {current} -> {status}")
        if release_digest is not None:
            release = self.application_store.get_release(report.application_id, release_digest)
            if report_id not in release.addresses_report_ids:
                raise DevelopmentReportServiceError("exact ApplicationRelease does not address this report")
            channels = self.application_store.get_channels(report.application_id).get("channels") or {}
            expected_status = "released" if channels.get("stable") == release_digest or release.lifecycle == "stable" else "prerelease_available"
            if status != expected_status:
                raise DevelopmentReportServiceError("release lifecycle does not match public status")
        return self._send_status(report, self._next_event(report, status=status, reason_code=reason_code, release_digest=release_digest))

    def announce_release(self, application_id: str, release_digest: str) -> list[dict[str, Any]]:
        release = self.application_store.get_release(application_id, release_digest)
        channels = self.application_store.get_channels(application_id).get("channels") or {}
        if channels.get("stable") == release_digest or release.lifecycle == "stable":
            status = "released"
        elif channels.get("prerelease") == release_digest or release.lifecycle in {"trial", "prerelease"}:
            status = "prerelease_available"
        else:
            raise DevelopmentReportServiceError("addressed release is not published on an installable channel")
        results = []
        for report_id in release.addresses_report_ids:
            report, intake = self._publisher_records(report_id)
            if report.application_id != application_id or intake.status != "accepted":
                raise DevelopmentReportServiceError("addressed report is not an accepted intake for this Application")
            results.append(self.set_public_status(report_id, status=status, release_digest=release_digest))
        return results

    def verify_release(self, report_id: str, *, outcome: str, release_digest: str) -> dict[str, Any]:
        if outcome not in {"verified", "still_reproduces"}:
            raise DevelopmentReportServiceError("verification outcome is invalid")
        report_raw = self.store.read()["reports"].get(report_id)
        if report_raw is None:
            raise DevelopmentReportServiceError("local DevelopmentReport is unknown")
        report = DevelopmentReport.from_mapping(report_raw)
        installation = self.application_store.get_installation(report.application_id)
        if installation.installed_release_digest != release_digest or installation.status not in {"active", "degraded"}:
            raise DevelopmentReportServiceError("guest must install the exact addressed release before verification")
        release = self.application_store.get_release(report.application_id, release_digest)
        if report_id not in release.addresses_report_ids:
            raise DevelopmentReportServiceError("installed release does not address this report")
        current = self.public_status(report_id)
        if current is None or current.get("release_digest") != release_digest or current.get("status") not in {"prerelease_available", "released", "awaiting_local_verification"}:
            raise DevelopmentReportServiceError("publisher has not announced this addressed release")
        payload = {
            "schema": "adaos.application.development_report_verification.v1",
            "verification_id": _id("verification", report_id, release_digest, outcome),
            "report_id": report_id, "application_id": report.application_id,
            "reporter_subnet_ref": self.subnet_ref, "publisher_ref": report.publisher_ref,
            "release_digest": release_digest, "outcome": outcome, "created_at": _iso(self.now()),
        }
        message_id = _id("msg", report_id, "verification", release_digest, outcome)
        envelope = self.crypto.seal(payload, message_kind="verification", sender_subnet_ref=self.subnet_ref, recipient_subnet_ref=report.publisher_ref, message_id=message_id)
        self.store.mutate(lambda state: state["outbox"].setdefault(message_id, {"kind": "verification", "envelope": envelope.to_dict(), "status": "queued", "created_at": _iso(self.now())}))
        result = self._dispatch_outbox(message_id)
        return {"verification": payload, "message_id": message_id, "relay": result}

    def _consume_verification(self, envelope: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        required = {"schema", "verification_id", "report_id", "application_id", "reporter_subnet_ref", "publisher_ref", "release_digest", "outcome", "created_at"}
        if set(payload) != required or payload.get("schema") != "adaos.application.development_report_verification.v1":
            raise DevelopmentReportServiceError("verification payload is invalid")
        report, intake = self._publisher_records(str(payload["report_id"]))
        if intake.status != "accepted" or payload["reporter_subnet_ref"] != report.reporter_subnet_ref or envelope["sender_subnet_ref"] != report.reporter_subnet_ref or payload["publisher_ref"] != self.subnet_ref:
            raise DevelopmentReportServiceError("verification identity is invalid")
        release = self.application_store.get_release(report.application_id, str(payload["release_digest"]))
        if report.report_id not in release.addresses_report_ids or payload["outcome"] not in {"verified", "still_reproduces"}:
            raise DevelopmentReportServiceError("verification release or outcome is invalid")
        event = self._next_event(report, status=str(payload["outcome"]), release_digest=release.release_digest)
        self._send_status(report, event)
        return {"ok": True, "report_id": report.report_id, "status": payload["outcome"], "release_digest": release.release_digest}

    def request_resync(self, report_id: str, *, after_revision: int, limit: int = 100) -> dict[str, Any]:
        report_raw = self.store.read()["reports"].get(report_id)
        if report_raw is None:
            raise DevelopmentReportServiceError("local DevelopmentReport is unknown")
        report = DevelopmentReport.from_mapping(report_raw)
        request = DevelopmentReportResync(
            request_id=_id("resync", report_id, after_revision, limit), report_id=report_id,
            requester_subnet_ref=self.subnet_ref, after_revision=after_revision, limit=limit,
            created_at=_iso(self.now()),
        )
        message_id = _id("msg", report_id, "resync", after_revision, limit)
        envelope = self.crypto.seal(request.to_dict(), message_kind="resync", sender_subnet_ref=self.subnet_ref, recipient_subnet_ref=report.publisher_ref, message_id=message_id)
        self.store.mutate(lambda state: state["outbox"].setdefault(message_id, {"kind": "resync", "envelope": envelope.to_dict(), "status": "queued", "created_at": _iso(self.now())}))
        return {"request": request.to_dict(), "message_id": message_id, "relay": self._dispatch_outbox(message_id)}

    def _consume_resync(self, envelope: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        request = DevelopmentReportResync.from_mapping(payload)
        report, _ = self._publisher_records(request.report_id)
        if request.requester_subnet_ref != report.reporter_subnet_ref or envelope["sender_subnet_ref"] != report.reporter_subnet_ref:
            raise DevelopmentReportServiceError("resync requester is invalid")
        events = self.store.read()["events"].get(report.report_id) or []
        selected = [dict(item) for item in events if int(item["revision"]) > request.after_revision][:request.limit]
        snapshot = {
            "schema": "adaos.application.development_report_resync_snapshot.v1",
            "request_id": request.request_id, "report_id": report.report_id,
            "from_revision": request.after_revision, "events": selected,
            "has_more": bool(events and selected and int(selected[-1]["revision"]) < int(events[-1]["revision"])),
            "generated_at": _iso(self.now()),
        }
        message_id = _id("msg", report.report_id, "resync_snapshot", request.request_id)
        sealed = self.crypto.seal(snapshot, message_kind="resync_snapshot", sender_subnet_ref=self.subnet_ref, recipient_subnet_ref=report.reporter_subnet_ref, message_id=message_id)
        self.store.mutate(lambda state: state["outbox"].setdefault(message_id, {"kind": "resync_snapshot", "envelope": sealed.to_dict(), "status": "queued", "created_at": _iso(self.now())}))
        return {"ok": True, "report_id": report.report_id, "message_id": message_id, "relay": self._dispatch_outbox(message_id)}

    def _consume_resync_snapshot(self, envelope: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        required = {"schema", "request_id", "report_id", "from_revision", "events", "has_more", "generated_at"}
        if set(payload) != required or payload.get("schema") != "adaos.application.development_report_resync_snapshot.v1" or not isinstance(payload.get("events"), list):
            raise DevelopmentReportServiceError("resync snapshot is invalid")
        report_raw = self.store.read()["reports"].get(str(payload["report_id"]))
        if report_raw is None:
            raise DevelopmentReportServiceError("resync report is unknown")
        report = DevelopmentReport.from_mapping(report_raw)
        if envelope["sender_subnet_ref"] != report.publisher_ref:
            raise DevelopmentReportServiceError("resync snapshot publisher is invalid")
        events = [DevelopmentReportStatusEvent.from_mapping(item) for item in payload["events"]]
        for event in events:
            if event.report_id != report.report_id or event.application_id != report.application_id or event.publisher_ref != report.publisher_ref or event.reporter_subnet_ref != self.subnet_ref:
                raise DevelopmentReportServiceError("resync status event identity is invalid")
            if event.release_digest is not None:
                release = self.application_store.get_release(report.application_id, event.release_digest)
                if report.report_id not in release.addresses_report_ids:
                    raise DevelopmentReportServiceError("resync status release does not address report")

        def persist(state: dict[str, Any]) -> None:
            for event in events:
                existing = {
                    int(item["revision"]): item
                    for item in state["events"].get(event.report_id) or []
                }
                if event.revision in existing:
                    if existing[event.revision] != event.to_dict():
                        raise DevelopmentReportServiceError("resync snapshot conflicts with local status history")
                    continue
                self._append_event(state, event)
            if events:
                current = DevelopmentReport.from_mapping(state["reports"][report.report_id])
                state["reports"][report.report_id] = replace(current, status=events[-1].status, revision=max(current.revision + 1, events[-1].revision), updated_at=_iso(self.now())).to_dict()
        self.store.mutate(persist)
        return {"ok": True, "report_id": report.report_id, "events_applied": len(events), "has_more": bool(payload["has_more"])}
