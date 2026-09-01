from __future__ import annotations

from pathlib import Path

from adaos.domain import Event, enrich_event_payload
from adaos.services.context_control import ContextControlService
from adaos.services.context_events import record_context_invalidation_event


def test_skill_release_event_records_idempotent_context_invalidation(tmp_path: Path) -> None:
    service = ContextControlService(tmp_path)
    event = Event(
        type="skills.updated",
        source="artifact.subscription",
        ts=1.0,
        payload=enrich_event_payload(
            {
                "skill_name": "demo_metrics_skill",
                "release_digest": "sha256:current",
                "project_id": "demo_metrics",
            },
            event_id="evt-skill-update-1",
            source_authority="artifact_registry",
        ),
    )

    first = record_context_invalidation_event(event, service=service)
    second = record_context_invalidation_event(event, service=service)

    assert {item["subject_ref"] for item in first} == {
        "skill:demo_metrics_skill",
        "project:demo_metrics",
    }
    assert second == first
    assert len(service.list_invalidations(event_ref="event:evt-skill-update-1")) == 2
