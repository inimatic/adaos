from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import jsonschema

from adaos.domain.development_report import (
    DevelopmentReport,
    DevelopmentReportAck,
    DevelopmentReportAppeal,
    DevelopmentReportEnvelope,
    DevelopmentReportIntake,
    DevelopmentReportResync,
    DevelopmentReportStatusEvent,
)


ABI_ROOT = Path(__file__).parents[1] / "src" / "adaos" / "abi"
DIGEST = "sha256:" + "a" * 64
KEY = "sha256:" + "b" * 64
NOW = "2026-09-05T12:00:00+00:00"


def _contracts() -> list[tuple[str, object]]:
    report = DevelopmentReport(
        report_id="report.example", application_id="app_example", publisher_ref="subnet:publisher",
        reporter_subnet_ref="subnet:guest", reporter_key_id=KEY, installed_release_digest=DIGEST,
        installation_proof={"installation_id": "installation:example", "application_id": "app_example", "release_digest": DIGEST, "installation_revision": 1},
        idempotency_key="guest-report-1", summary="Failure", details="Expected A, observed B",
        status="queued", created_at=NOW, updated_at=NOW,
    )
    envelope = DevelopmentReportEnvelope(
        message_id="msg.example", message_kind="report", sender_subnet_ref="subnet:guest",
        sender_key_id=KEY, recipient_subnet_ref="subnet:publisher", recipient_key_id=DIGEST,
        source_zone="zone_a", destination_zone="zone_a", route_generation=1, hop_limit=4,
        ephemeral_public_key_b64="AA==", nonce_b64="AA==", ciphertext_b64="AA==",
        signature_b64="AA==", created_at=NOW, expires_at="2026-09-19T12:00:00+00:00",
    )
    intake = DevelopmentReportIntake(
        intake_id="intake.example", report_id=report.report_id, application_id=report.application_id,
        reporter_subnet_ref=report.reporter_subnet_ref, raw_payload_digest=DIGEST,
        normalized_summary=report.summary, normalized_details=report.details,
        redaction_findings=("bearer_token",), model_classification=None,
        admission={"checks": ["schema"]}, received_at=NOW, updated_at=NOW,
    )
    event = DevelopmentReportStatusEvent(
        event_id="event.example", report_id=report.report_id, application_id=report.application_id,
        publisher_ref=report.publisher_ref, reporter_subnet_ref=report.reporter_subnet_ref,
        status="accepted", revision=2, occurred_at=NOW,
    )
    ack = DevelopmentReportAck(
        message_id=envelope.message_id, recipient_subnet_ref=envelope.recipient_subnet_ref,
        disposition="accepted", delivery_id="delivery.example", accepted_at=NOW,
    )
    resync = DevelopmentReportResync(
        request_id="resync.example", report_id=report.report_id,
        requester_subnet_ref=report.reporter_subnet_ref, after_revision=2, created_at=NOW,
    )
    appeal = DevelopmentReportAppeal(
        appeal_id="appeal.example", report_id=report.report_id,
        application_id=report.application_id, publisher_ref=report.publisher_ref,
        reporter_subnet_ref=report.reporter_subnet_ref,
        idempotency_key="appeal-example", statement="Please reconsider this report.",
        created_at=NOW, updated_at=NOW,
    )
    return [
        ("application.development-report.v1.schema.json", report),
        ("application.development-report-envelope.v1.schema.json", envelope),
        ("application.development-report-intake.v1.schema.json", intake),
        ("application.development-report-status-event.v1.schema.json", event),
        ("application.development-report-ack.v1.schema.json", ack),
        ("application.development-report-resync.v1.schema.json", resync),
        ("application.development-report-appeal.v1.schema.json", appeal),
    ]


def test_development_report_contracts_round_trip_and_validate() -> None:
    for schema_name, contract in _contracts():
        payload = contract.to_dict()
        schema = json.loads((ABI_ROOT / schema_name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(payload)
        assert type(contract).from_mapping(payload) == contract

    envelope = _contracts()[1][1]
    schema = json.loads(
        (ABI_ROOT / "application.development-report-envelope.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for message_kind in ("appeal", "appeal_response"):
        jsonschema.Draft202012Validator(schema).validate(
            replace(envelope, message_kind=message_kind).to_dict()
        )
