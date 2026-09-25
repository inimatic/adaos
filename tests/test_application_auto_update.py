from __future__ import annotations

import json
from types import SimpleNamespace

from adaos.services.applications.auto_update import (
    ApplicationAutoUpdateService,
    automatic_update_blockers,
)


def _model(*, auto: bool = True) -> dict:
    return {
        "application": {"application_id": "mail_focus_reader"},
        "installation": {"revision": 3, "uncertain_operation_refs": []},
        "effective_release": {"release_digest": "sha256:" + "b" * 64},
        "auto_update_enabled": auto,
        "update_available": True,
    }


def _plan(*, approval_required: bool = False, migration_required: bool = False) -> dict:
    return {
        "conflicts": [],
        "permission_review": {"approval_required": approval_required},
        "compatibility": {"migration": {"required": migration_required}},
    }


class _ApplicationService:
    def __init__(self, plan: dict) -> None:
        self.plan = plan
        self.applied: list[tuple] = []

    def list_models(self, **_kwargs):
        return [_model()]

    def plan_operation(self, application_id, kind, **kwargs):
        assert application_id == "mail_focus_reader"
        assert kind == "update"
        assert kwargs["actor_ref"] == "service:application-auto-update"
        return SimpleNamespace(
            operation_id="appop.safe",
            plan_digest="sha256:" + "c" * 64,
            plan=self.plan,
        )

    def apply_operation(self, operation_id, **kwargs):
        self.applied.append((operation_id, kwargs))
        return SimpleNamespace(
            status="succeeded",
            revision=3,
            result={"ok": True, "status": "succeeded"},
        )


def test_automatic_update_applies_safe_exact_plan_and_persists_receipt(tmp_path) -> None:
    application_service = _ApplicationService(_plan())
    result = ApplicationAutoUpdateService(
        tmp_path,
        application_service,  # type: ignore[arg-type]
    ).run(
        subnet_ref="subnet:test",
        trigger="registry_sync",
        registry_index_digest="sha256:" + "a" * 64,
    )

    assert result["status"] == "completed"
    assert result["applied_count"] == 1
    assert result["review_required_count"] == 0
    assert result["outcomes"][0]["status"] == "succeeded"
    assert len(application_service.applied) == 1
    persisted = json.loads(
        (tmp_path / "applications" / "auto_update_runs" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted == result


def test_automatic_update_requires_review_for_authority_or_migration_change(tmp_path) -> None:
    for name, plan, blocker in (
        (
            "permission",
            _plan(approval_required=True),
            "permission_approval_required",
        ),
        ("migration", _plan(migration_required=True), "migration_required"),
    ):
        application_service = _ApplicationService(plan)
        result = ApplicationAutoUpdateService(
            tmp_path / name,
            application_service,  # type: ignore[arg-type]
        ).run(
            subnet_ref="subnet:test",
            trigger="applications.registry.updated",
        )

        assert result["applied_count"] == 0
        assert result["review_required_count"] == 1
        assert blocker in result["outcomes"][0]["blockers"]
        assert application_service.applied == []


def test_automatic_update_blockers_fail_closed_for_incomplete_plan() -> None:
    assert automatic_update_blockers({}) == [
        "permission_review_unavailable",
        "compatibility_unavailable",
    ]
