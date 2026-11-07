from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.builder.preview_data_profiles import (
    BuilderPreviewDataProfileError,
    BuilderPreviewDataProfileService,
)


class _Workflow:
    def __init__(self) -> None:
        self.binding = {
            "schema": "adaos.builder.binding_state.v1",
            "selected_profile_id": "mock",
            "selected_mode": "mock",
            "generation": 4,
            "updated_at": "2026-11-07T00:00:00+00:00",
            "profiles": [
                {
                    "profile_id": "mock",
                    "mode": "mock",
                    "source_ref": "fixture:portrait",
                    "capabilities": [],
                    "implementation_mappings": [],
                },
                {
                    "profile_id": "readonly",
                    "mode": "live_readonly",
                    "source_ref": "cbs:portrait.read",
                    "capabilities": ["portrait.read"],
                    "implementation_mappings": [
                        {
                            "logical_ref": "portrait.read",
                            "implementation_ref": "skill:portrait_provider.read",
                            "status": "mapped",
                        }
                    ],
                },
                {
                    "profile_id": "incomplete",
                    "mode": "sandbox",
                    "source_ref": "cbs:portrait.sandbox",
                    "capabilities": ["portrait.read"],
                    "implementation_mappings": [
                        {
                            "logical_ref": "portrait.read",
                            "implementation_ref": None,
                            "status": "missing",
                        }
                    ],
                },
                {
                    "profile_id": "live-write",
                    "mode": "live",
                    "source_ref": "cbs:portrait.write",
                    "capabilities": [],
                    "implementation_mappings": [],
                },
            ],
        }

    def describe(self, object_type: str, object_id: str) -> dict:
        assert (object_type, object_id) == ("scenario", "portraits")
        return {
            "active_phase": "prototype",
            "data_binding": self.binding,
        }


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BuilderPreviewDataProfileService:
    service = BuilderPreviewDataProfileService(
        tmp_path,
        workflow_service=_Workflow(),
    )
    monkeypatch.setattr(service, "_source", lambda value: str(value or "desktop"))
    return service


def test_preview_profile_selection_is_webspace_scoped_and_rolls_back_failed_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, monkeypatch)

    initial = service.projection("portraits", webspace_id="desktop")
    assert initial["profile_id"] == "mock"
    assert initial["label"] == "mock (mock)"
    assert initial["generation"] == 0
    assert {item["id"] for item in initial["options"] if item["disabled"]} == {
        "incomplete",
        "live-write",
    }

    with pytest.raises(BuilderPreviewDataProfileError, match="explicit confirmation"):
        service.begin_selection(
            "portraits",
            "readonly",
            webspace_id="desktop",
            expected_generation=0,
        )

    pending = service.begin_selection(
        "portraits",
        "readonly",
        webspace_id="desktop",
        expected_generation=0,
        confirmed=True,
    )
    assert pending["profile_id"] == "readonly"
    assert pending["status"] == "materializing"
    assert pending["ready"] is False
    assert pending["generation"] == 1

    failed = service.finish_selection(
        "portraits",
        webspace_id="desktop",
        operation_id=pending["operation_id"],
        ok=False,
        error="owner rejected materialization",
    )
    assert failed["profile_id"] == "mock"
    assert failed["status"] == "failed"
    assert failed["generation"] == 1

    other_host = service.projection("portraits", webspace_id="reviewer")
    assert other_host["profile_id"] == "mock"
    assert other_host["generation"] == 0


def test_preview_profile_selection_uses_compare_and_set_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, monkeypatch)
    pending = service.begin_selection(
        "portraits",
        "mock",
        webspace_id="desktop",
        expected_generation=0,
    )

    with pytest.raises(BuilderPreviewDataProfileError, match="stale"):
        service.begin_selection(
            "portraits",
            "mock",
            webspace_id="desktop",
            expected_generation=0,
        )

    ready = service.finish_selection(
        "portraits",
        webspace_id="desktop",
        operation_id=pending["operation_id"],
        ok=True,
    )
    assert ready["status"] == "ready"
    assert ready["profile_id"] == "mock"
