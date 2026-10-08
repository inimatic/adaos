from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from adaos.services import runtime_compatibility
from adaos.services.runtime_compatibility import classify_runtime_compatibility


def _snapshot(**patch):
    value = {
        "desired_release": {
            "admitted": True,
            "version": "1.2.0",
            "package_digest": "sha256:desired",
        },
        "installed_release": {
            "version": "1.2.0",
            "package_digest": "sha256:desired",
        },
        "loaded_runtime": {
            "module_available": True,
            "generation": "generation-2",
            "package_digest": "sha256:desired",
            "source_drift": False,
            "selection_drift": False,
        },
        "receiver_policy": {"state": "valid", "patterns": ["owned.panel"]},
        "observation": {
            "receiver": "owned.panel",
            "own_stream": True,
            "receiver_admitted": True,
        },
        "core_contract": {"supported": True},
        "eligible_update": {},
        "builder_work": [],
    }
    value.update(patch)
    return value


def test_classifier_distinguishes_stale_runtime_memory() -> None:
    result = classify_runtime_compatibility(
        _snapshot(
            loaded_runtime={
                "module_available": True,
                "generation": "generation-1",
                "package_digest": "sha256:old",
                "source_drift": False,
                "selection_drift": True,
            }
        )
    )

    assert result["code"] == "stale_runtime_memory"
    assert result["recommended_action"] == "reactivate_exact_admitted_package"
    assert result["automatic_recovery_eligible"] is True
    assert result["human_decision_required"] is False


def test_classifier_distinguishes_unavailable_module() -> None:
    result = classify_runtime_compatibility(
        _snapshot(
            loaded_runtime={
                "module_available": False,
                "generation": "generation-2",
                "package_digest": "sha256:desired",
            }
        )
    )

    assert result["code"] == "unavailable_module"
    assert result["owner"] == "runtime"
    assert result["automatic_recovery_eligible"] is True


def test_classifier_distinguishes_unsupported_core_contract() -> None:
    result = classify_runtime_compatibility(
        _snapshot(core_contract={"supported": False, "required": 4, "actual": 3})
    )

    assert result["code"] == "unsupported_core_contract"
    assert result["owner"] == "core"
    assert result["recommended_action"] == "route_to_core_upgrade"
    assert result["human_decision_required"] is False


def test_classifier_distinguishes_application_declaration_defect() -> None:
    result = classify_runtime_compatibility(
        _snapshot(receiver_policy={"state": "absent", "patterns": []})
    )

    assert result["code"] == "application_declaration_defect"
    assert result["owner"] == "application"
    assert result["recommended_action"] == "open_scoped_builder_repair"


def test_classifier_does_not_turn_foreign_broadcast_into_receiver_ownership() -> None:
    result = classify_runtime_compatibility(
        _snapshot(
            receiver_policy={"state": "absent", "patterns": []},
            observation={
                "receiver": "foreign.panel",
                "own_stream": False,
                "receiver_admitted": False,
            },
        )
    )

    assert result["code"] == "foreign_stream_observation"
    assert result["recommended_action"] == "record_diagnostic_only"
    assert result["human_decision_required"] is False


def test_classifier_reuses_existing_builder_work() -> None:
    result = classify_runtime_compatibility(
        _snapshot(builder_work=[{"repair_id": "repair.1", "status": "in_progress"}])
    )

    assert result["code"] == "existing_builder_work"
    assert result["recommended_action"] == "continue_existing_builder_work"
    assert result["existing_builder_work"][0]["repair_id"] == "repair.1"


def test_classifier_only_offers_exact_admitted_update() -> None:
    result = classify_runtime_compatibility(
        _snapshot(
            desired_release={
                "admitted": True,
                "version": "1.3.0",
                "package_digest": "sha256:new",
            },
            installed_release={
                "version": "1.2.0",
                "package_digest": "sha256:old",
            },
            loaded_runtime={
                "module_available": True,
                "generation": "generation-2",
                "package_digest": "sha256:old",
            },
            eligible_update={
                "eligible": True,
                "from_package_digest": "sha256:old",
                "package_digest": "sha256:new",
            },
        )
    )

    assert result["code"] == "eligible_exact_update"
    assert result["human_decision_required"] is True
    assert result["automatic_recovery_eligible"] is False


def test_classifier_fails_closed_when_package_identity_is_unknown() -> None:
    result = classify_runtime_compatibility(
        _snapshot(
            desired_release={"admitted": True, "package_digest": None},
            installed_release={"package_digest": None},
            receiver_policy={"state": "absent", "patterns": []},
        )
    )

    assert result["evidence_complete"] is False
    assert result["human_decision_required"] is False
    assert result["automatic_recovery_eligible"] is False
    assert result["missing_evidence"] == [
        "desired_release.package_digest",
        "installed_release.package_digest",
    ]


def test_classifier_rejects_inconsistent_package_manifest_identity() -> None:
    result = classify_runtime_compatibility(
        _snapshot(
            desired_release={
                "admitted": True,
                "package_digest": "sha256:desired",
                "manifest_digest": "sha256:desired-manifest",
            },
            installed_release={
                "package_digest": "sha256:desired",
                "manifest_digest": "sha256:other-manifest",
            },
        )
    )

    assert result["code"] == "installed_release_identity_inconsistent"
    assert result["automatic_recovery_eligible"] is False
    assert result["human_decision_required"] is False


def test_collector_binds_workspace_lock_to_selected_and_loaded_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    from adaos.services import artifact_pipeline, skills_loader_importlib
    from adaos.services.skill import declarations

    monkeypatch.setattr(
        declarations,
        "runtime_skill_declarations_snapshot",
        lambda _skill: {"receiver_patterns": ["owned.panel"]},
    )
    monkeypatch.setattr(
        skills_loader_importlib,
        "skill_handler_source_snapshot",
        lambda: {
            "items": [
                {
                    "skill": "example_skill",
                    "module": "example.handlers",
                    "loaded_at": 20.0,
                    "selected_version": "2.0.0",
                    "selected_slot": "B",
                    "selected_package_digest": "sha256:admitted",
                    "selected_package_manifest_digest": "sha256:manifest",
                    "selected_source_manifest_digest": "sha256:manifest",
                    "loaded_package_digest": "sha256:previous",
                    "loaded_package_manifest_digest": "sha256:previous-manifest",
                    "loaded_source_manifest_digest": "sha256:previous-manifest",
                    "source_drift": False,
                    "selection_drift": True,
                    "current_exists": True,
                }
            ]
        },
    )
    monkeypatch.setattr(
        artifact_pipeline,
        "load_workspace_lock",
        lambda _path: SimpleNamespace(
            lock_revision=7,
            components=(
                SimpleNamespace(
                    kind="skill",
                    artifact_id="example_skill",
                    version="2.0.0",
                    digest="sha256:admitted",
                    manifest_digest="sha256:manifest",
                ),
            ),
        ),
    )
    ctx = SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: tmp_path))

    snapshot = runtime_compatibility.collect_skill_runtime_compatibility_snapshot(
        "example_skill",
        admission={
            "reason": "stream_receiver_not_declared",
            "receiver": "owned.panel",
            "allowed": False,
        },
        ctx=ctx,
    )
    classified = classify_runtime_compatibility(snapshot)

    assert snapshot["desired_release"] == {
        "admitted": True,
        "version": "2.0.0",
        "package_digest": "sha256:admitted",
        "manifest_digest": "sha256:manifest",
        "lock_revision": 7,
    }
    assert snapshot["installed_release"]["slot"] == "B"
    assert snapshot["installed_release"]["package_digest"] == "sha256:admitted"
    assert snapshot["installed_release"]["manifest_digest"] == "sha256:manifest"
    assert snapshot["installed_release"]["source_manifest_digest"] == "sha256:manifest"
    assert snapshot["loaded_runtime"]["package_digest"] == "sha256:previous"
    assert snapshot["loaded_runtime"]["manifest_digest"] == "sha256:previous-manifest"
    assert classified["code"] == "stale_runtime_memory"
    assert classified["automatic_recovery_eligible"] is True


def test_provenance_reconciler_is_bounded_and_uses_verified_manager_adoption(
    _autocontext,
    monkeypatch,
) -> None:
    from adaos.services import artifact_pipeline
    from adaos.services.skill import manager as skill_manager
    from adaos.services.skill import runtime_migration_worker

    package = SimpleNamespace(
        kind="skill",
        artifact_id="legacy_skill",
        version="1.0.0",
        digest="sha256:package",
        manifest_digest="sha256:manifest",
        to_dict=lambda: {
            "kind": "skill",
            "artifact_id": "legacy_skill",
            "version": "1.0.0",
            "digest": "sha256:package",
            "manifest_digest": "sha256:manifest",
        },
    )
    monkeypatch.setattr(
        artifact_pipeline,
        "load_workspace_lock",
        lambda _path: SimpleNamespace(components=(package,)),
    )

    class _Manager:
        def active_runtime_package_identity(self, _skill):
            return {"version": "1.0.0", "slot": "B"}

        def adopt_active_runtime_package_identity(self, skill, *, package):
            assert skill == "legacy_skill"
            assert package["digest"] == "sha256:package"
            return {
                "adopted": True,
                "reason": "exact_package_rebuilt",
                "version": "1.0.0",
                "slot": "B",
                "package_digest": "sha256:package",
                "package_manifest_digest": "sha256:manifest",
                "source_manifest_digest": "sha256:source",
            }

    monkeypatch.setattr(skill_manager, "SkillManager", lambda **_kwargs: _Manager())
    monkeypatch.setattr(
        runtime_migration_worker,
        "runtime_mutation_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    result = runtime_compatibility.reconcile_active_runtime_package_provenance(
        ctx=_autocontext,
        limit=1,
    )

    assert result["complete"] is True
    assert result["scanned"] == 1
    assert result["adopted"] == 1
    assert result["errors"] == []
