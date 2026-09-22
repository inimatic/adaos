from adaos.services.applications.conditions import application_condition_projection


def _model(**overrides):
    value = {
        "application": {"application_id": "app-notes", "aggregate_backed": True},
        "installed": True,
        "available": True,
        "installation": {"revision": 3, "status": "active"},
        "subscription": {"status": "active"},
        "operation": {"status": "succeeded", "revision": 2},
        "execution_placement": {"managed": False, "status": "not_materialized"},
        "update_available": False,
        "retired": False,
    }
    value.update(overrides)
    return value


def test_current_application_has_ready_attention():
    projection = application_condition_projection(_model())

    assert projection["attention"] == {
        "status": "current",
        "reason": "Ready",
        "message": "The installed Application is ready and current.",
        "icon": "checkmark-circle-outline",
        "color": "success",
        "priority": 20,
        "requires_action": False,
    }
    assert (
        next(item for item in projection["conditions"] if item["type"] == "Ready")[
            "status"
        ]
        == "True"
    )
    assert projection["release_cycle"] == {
        "source": "installed",
        "label": "Installed",
        "accent": "success",
    }


def test_failed_operation_has_degraded_attention():
    projection = application_condition_projection(
        _model(
            operation={
                "status": "failed",
                "revision": 4,
                "recovery_reason": "rollback required",
            }
        )
    )

    assert projection["attention"]["status"] == "degraded"
    assert projection["attention"]["reason"] == "OperationFailed"
    assert projection["attention"]["message"] == "rollback required"


def test_degraded_installation_is_not_reported_as_ready():
    projection = application_condition_projection(
        _model(
            operation=None,
            installation={
                "revision": 5,
                "status": "degraded",
                "updated_at": "2026-09-22T05:00:00Z",
            },
        )
    )

    assert projection["attention"]["status"] == "degraded"
    assert projection["attention"]["reason"] == "InstallationDegraded"
    ready = next(
        item for item in projection["conditions"] if item["type"] == "Ready"
    )
    assert ready["observed_generation"] == 5
    assert ready["last_transition_time"] == "2026-09-22T05:00:00Z"


def test_installing_application_is_progressing():
    projection = application_condition_projection(
        _model(operation=None, installation={"revision": 6, "status": "updating"})
    )

    assert projection["attention"]["status"] == "progressing"
    assert projection["attention"]["reason"] == "OperationUpdating"


def test_incomplete_placement_requires_action_before_update_notice():
    projection = application_condition_projection(
        _model(
            update_available=True,
            execution_placement={
                "managed": True,
                "status": "active",
                "desired_component_count": 2,
                "observed_active_count": 1,
            },
        )
    )

    assert projection["attention"]["status"] == "action_required"
    assert projection["attention"]["reason"] == "PlacementIncomplete"


def test_uninstalled_catalog_application_is_available_not_unknown():
    projection = application_condition_projection(
        _model(installed=False, installation=None, operation=None)
    )

    assert projection["attention"]["status"] == "available"
    assert projection["attention"]["icon"] == "cloud-download-outline"
    assert projection["release_cycle"]["source"] == "marketplace"


def test_beta_and_outdated_installations_have_distinct_release_cycle_accents():
    beta = application_condition_projection(_model(local_beta_active=True))
    outdated = application_condition_projection(_model(update_available=True))

    assert beta["release_cycle"] == {
        "source": "beta",
        "label": "Beta",
        "accent": "warning",
    }
    assert outdated["release_cycle"]["source"] == "update_available"
    assert outdated["release_cycle"]["accent"] == "tertiary"
