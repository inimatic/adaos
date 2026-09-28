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
        "local_beta_active": False,
        "runtime_selections": [],
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
            status="planned",
            revision=1,
        )

    def apply_operation(self, operation_id, **kwargs):
        self.applied.append((operation_id, kwargs))
        return SimpleNamespace(
            status="succeeded",
            revision=3,
            result={"ok": True, "status": "succeeded"},
        )


class _RuntimeStore:
    def __init__(self, selections):
        self.selections = list(selections)

    def list_runtime_selections(self):
        return list(self.selections)


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


def test_automatic_update_scopes_plan_identity_to_registry_snapshot(tmp_path) -> None:
    class RecordingApplicationService(_ApplicationService):
        def __init__(self) -> None:
            super().__init__(_plan())
            self.plan_keys: list[str] = []

        def plan_operation(self, application_id, kind, **kwargs):
            self.plan_keys.append(kwargs["idempotency_key"])
            return super().plan_operation(application_id, kind, **kwargs)

    application_service = RecordingApplicationService()
    service = ApplicationAutoUpdateService(
        tmp_path,
        application_service,  # type: ignore[arg-type]
    )

    first = service.run(
        subnet_ref="subnet:test",
        trigger="applications.registry.updated",
        registry_index_digest="sha256:" + "1" * 64,
    )
    second = service.run(
        subnet_ref="subnet:test",
        trigger="applications.registry.updated",
        registry_index_digest="sha256:" + "2" * 64,
    )

    assert first["outcomes"][0]["idempotency_key"] != second["outcomes"][0]["idempotency_key"]
    assert len(set(application_service.plan_keys)) == 2


def test_automatic_update_blockers_fail_closed_for_incomplete_plan() -> None:
    assert automatic_update_blockers({}) == [
        "permission_review_unavailable",
        "compatibility_unavailable",
    ]


def test_automatic_update_retries_after_a_failed_idempotent_operation(tmp_path) -> None:
    class RetryApplicationService(_ApplicationService):
        def __init__(self) -> None:
            super().__init__(_plan())
            self.plan_keys: list[str] = []

        def plan_operation(self, application_id, kind, **kwargs):
            self.plan_keys.append(kwargs["idempotency_key"])
            if len(self.plan_keys) == 1:
                return SimpleNamespace(
                    operation_id="appop.failed",
                    plan_digest="sha256:" + "d" * 64,
                    plan=self.plan,
                    status="failed",
                    revision=3,
                )
            return SimpleNamespace(
                operation_id="appop.retry",
                plan_digest="sha256:" + "e" * 64,
                plan=self.plan,
                status="planned",
                revision=1,
            )

    application_service = RetryApplicationService()
    result = ApplicationAutoUpdateService(
        tmp_path,
        application_service,  # type: ignore[arg-type]
    ).run(
        subnet_ref="subnet:test",
        trigger="applications.registry.updated",
    )

    outcome = result["outcomes"][0]
    assert result["status"] == "completed"
    assert outcome["status"] == "succeeded"
    assert outcome["operation_id"] == "appop.retry"
    assert outcome["retried_failed_operation_ids"] == ["appop.failed"]
    assert len(set(application_service.plan_keys)) == 2
    assert application_service.plan_keys[1].startswith(
        application_service.plan_keys[0] + ":retry:"
    )
    assert [call[0] for call in application_service.applied] == ["appop.retry"]


def test_automatic_update_converges_after_known_partial_operation(tmp_path) -> None:
    class PartialApplicationService(_ApplicationService):
        def __init__(self) -> None:
            super().__init__(_plan())
            self.plan_keys: list[str] = []

        def plan_operation(self, application_id, kind, **kwargs):
            self.plan_keys.append(kwargs["idempotency_key"])
            if len(self.plan_keys) == 1:
                return SimpleNamespace(
                    operation_id="appop.partial",
                    plan_digest="sha256:" + "d" * 64,
                    plan=self.plan,
                    status="unknown",
                    revision=3,
                    result={
                        "deployment_operation": {
                            "state": "partial",
                            "uncertain": False,
                            "error": {"manual_reconciliation": False},
                        }
                    },
                )
            return SimpleNamespace(
                operation_id="appop.converge",
                plan_digest="sha256:" + "e" * 64,
                plan=self.plan,
                status="planned",
                revision=1,
            )

    application_service = PartialApplicationService()
    result = ApplicationAutoUpdateService(
        tmp_path,
        application_service,  # type: ignore[arg-type]
    ).run(
        subnet_ref="subnet:test",
        trigger="applications.registry.updated",
    )

    outcome = result["outcomes"][0]
    assert result["status"] == "completed"
    assert outcome["status"] == "succeeded"
    assert outcome["operation_id"] == "appop.converge"
    assert outcome["retried_terminal_operation_ids"] == ["appop.partial"]
    assert "retried_failed_operation_ids" not in outcome
    assert len(set(application_service.plan_keys)) == 2


def test_automatic_update_retries_lock_contention_in_same_run(tmp_path) -> None:
    class LockContentionService(_ApplicationService):
        def __init__(self) -> None:
            super().__init__(_plan())
            self.plan_keys: list[str] = []
            self.apply_count = 0

        def plan_operation(self, application_id, kind, **kwargs):
            self.plan_keys.append(kwargs["idempotency_key"])
            return SimpleNamespace(
                operation_id=f"appop.lock-{len(self.plan_keys)}",
                plan_digest="sha256:" + str(len(self.plan_keys)) * 64,
                plan=self.plan,
                status="planned",
                revision=1,
            )

        def apply_operation(self, operation_id, **kwargs):
            self.applied.append((operation_id, kwargs))
            self.apply_count += 1
            if self.apply_count == 1:
                return SimpleNamespace(
                    status="failed",
                    revision=3,
                    result={
                        "deployment_operation": {
                            "state": "failed",
                            "uncertain": False,
                            "node_results": [
                                {
                                    "components": [
                                        {
                                            "error": {
                                                "type": "MutationLockTimeout",
                                                "code": "adapter_phase_failed",
                                            }
                                        }
                                    ]
                                }
                            ],
                            "error": {"manual_reconciliation": False},
                        }
                    },
                )
            return SimpleNamespace(
                status="succeeded",
                revision=3,
                result={"ok": True, "status": "succeeded"},
            )

    application_service = LockContentionService()
    result = ApplicationAutoUpdateService(
        tmp_path,
        application_service,  # type: ignore[arg-type]
    ).run(
        subnet_ref="subnet:test",
        trigger="applications.registry.updated",
    )

    outcome = result["outcomes"][0]
    assert result["status"] == "completed"
    assert outcome["status"] == "succeeded"
    assert outcome["operation_id"] == "appop.lock-2"
    assert outcome["retried_failed_operation_ids"] == ["appop.lock-1"]
    assert [call[0] for call in application_service.applied] == [
        "appop.lock-1",
        "appop.lock-2",
    ]
    assert application_service.plan_keys[1].startswith(
        "application-auto-update:"
    )
    assert ":retry:" in application_service.plan_keys[1]


def test_automatic_update_reports_unknown_receipt_as_uncertain(tmp_path) -> None:
    class UnknownReceiptService(_ApplicationService):
        def apply_operation(self, operation_id, **kwargs):
            self.applied.append((operation_id, kwargs))
            return SimpleNamespace(
                status="unknown",
                revision=3,
                result={"ok": False, "status": "unknown"},
            )

    result = ApplicationAutoUpdateService(
        tmp_path,
        UnknownReceiptService(_plan()),  # type: ignore[arg-type]
    ).run(
        subnet_ref="subnet:test",
        trigger="applications.registry.updated",
    )

    assert result["status"] == "uncertain"
    assert result["uncertain_count"] == 1
    assert result["applied_count"] == 0


def test_automatic_update_advances_existing_stable_runtime_selection(tmp_path) -> None:
    old_digest = "sha256:" + "a" * 64
    target_digest = "sha256:" + "b" * 64
    selected = SimpleNamespace(
        application_id="mail_focus_reader",
        webspace_id="desktop",
        source="stable_installation",
        release_digest=old_digest,
        runtime_root_ref="workspace",
        revision=4,
    )

    class RuntimeAwareService(_ApplicationService):
        def __init__(self) -> None:
            super().__init__(_plan())
            self.store = _RuntimeStore([selected])
            self.selections = []

        def list_models(self, **_kwargs):
            model = _model()
            model["runtime_selections"] = [
                {
                    "application_id": selected.application_id,
                    "webspace_id": selected.webspace_id,
                    "source": selected.source,
                    "release_digest": selected.release_digest,
                    "runtime_root_ref": selected.runtime_root_ref,
                    "revision": selected.revision,
                }
            ]
            return [model]

        def select_runtime(self, **kwargs):
            self.selections.append(kwargs)
            return SimpleNamespace(to_dict=lambda: {**kwargs, "revision": 5})

    application_service = RuntimeAwareService()
    result = ApplicationAutoUpdateService(tmp_path, application_service).run(
        subnet_ref="subnet:test",
        trigger="registry_sync",
    )

    assert result["status"] == "completed"
    assert result["outcomes"][0]["runtime_selection"]["status"] == "reconciled"
    assert application_service.selections == [
        {
            "webspace_id": "desktop",
            "application_id": "mail_focus_reader",
            "source": "stable_installation",
            "release_digest": target_digest,
            "runtime_root_ref": "workspace",
            "expected_revision": 4,
            "actor_ref": "service:application-auto-update",
            "subnet_ref": "subnet:test",
            "capability": "applications.apply",
        }
    ]


def test_automatic_update_repairs_stale_selection_after_package_converged(tmp_path) -> None:
    installed_digest = "sha256:" + "b" * 64
    selected = SimpleNamespace(
        application_id="mail_focus_reader",
        webspace_id="desktop",
        source="stable_installation",
        release_digest="sha256:" + "a" * 64,
        runtime_root_ref="workspace",
        revision=2,
    )

    class RepairService(_ApplicationService):
        def __init__(self) -> None:
            super().__init__(_plan())
            self.store = _RuntimeStore([selected])
            self.selections = []

        def list_models(self, **_kwargs):
            model = _model()
            model["update_available"] = False
            model["installation"]["installed_release_digest"] = installed_digest
            model["runtime_selections"] = [
                {
                    "source": "stable_installation",
                    "release_digest": selected.release_digest,
                    "runtime_root_ref": "workspace",
                }
            ]
            return [model]

        def plan_operation(self, *_args, **_kwargs):
            raise AssertionError("runtime-only repair must not plan another package update")

        def select_runtime(self, **kwargs):
            self.selections.append(kwargs)
            return SimpleNamespace(to_dict=lambda: {**kwargs, "revision": 3})

    application_service = RepairService()
    result = ApplicationAutoUpdateService(tmp_path, application_service).run(
        subnet_ref="subnet:test",
        trigger="registry_sync",
    )

    assert result["status"] == "completed"
    assert result["outcomes"][0]["reconcile_only"] is True
    assert result["outcomes"][0]["runtime_selection"]["status"] == "reconciled"
    assert application_service.applied == []
    assert application_service.selections[0]["release_digest"] == installed_digest


def test_automatic_update_does_not_replace_active_beta_runtime(tmp_path) -> None:
    class BetaService(_ApplicationService):
        def list_models(self, **_kwargs):
            model = _model()
            model["local_beta_active"] = True
            model["runtime_selections"] = [
                {
                    "source": "local_trial",
                    "release_digest": "sha256:" + "a" * 64,
                    "runtime_root_ref": "trial:candidate",
                }
            ]
            return [model]

    application_service = BetaService(_plan())
    result = ApplicationAutoUpdateService(tmp_path, application_service).run(
        subnet_ref="subnet:test",
        trigger="registry_sync",
    )

    assert result["candidate_count"] == 0
    assert result["skipped"] == [
        {
            "application_id": "mail_focus_reader",
            "reason": "active_non_stable_runtime",
        }
    ]
    assert application_service.applied == []
