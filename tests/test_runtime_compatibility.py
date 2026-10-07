from __future__ import annotations

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
