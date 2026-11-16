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


def test_runtime_composition_drift_is_visible_even_when_installation_is_active():
    projection = application_condition_projection(_model(runtime_revisions={
        "status": "drift", "summary": "Expected exact release; observed another package."}))
    assert projection["attention"]["status"] == "degraded"
    assert projection["attention"]["reason"] == "RuntimeCompositionDrift"
    assert next(c for c in projection["conditions"] if c["type"] == "RuntimeIntegrity")["status"] == "False"
    assert next(c for c in projection["conditions"] if c["type"] == "Ready")["status"] == "False"


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


def test_a_superseded_failure_does_not_degrade_the_new_edition_or_hide_a_live_fence():
    model = _model(lifecycle={'operation': {'status':'failed', 'superseded_by_runtime':True}})
    assert application_condition_projection(model)['attention']['status'] == 'current'
    model['lifecycle']['transition'] = {'runtime_fenced':True,'status':'recovery_required','operation_id':'live-transition'}
    assert application_condition_projection(model)['attention']['requires_action']


def test_summary_inventory_uses_the_same_release_and_selection_evidence():
    model = _model(operation={'status':'failed','release_digest':'old','updated_at':'2026-11-16T11:00:00Z'},
        active_release={'release_digest':'new'},runtime_selections=[{'release_digest':'new','updated_at':'2026-11-16T12:00:00Z'}])
    assert application_condition_projection(model)['attention']['status']=='current'
    model['runtime_selections'][0]['updated_at']='2026-11-16T10:00:00Z'
    assert application_condition_projection(model)['attention']['status']=='degraded'


def test_unknown_authoritative_lifecycle_is_visible_ahead_of_update_available():
    projection = application_condition_projection(_model(update_available=True, lifecycle={
        "operation": {"status": "unknown", "revision": 4,
                      "message": "Contract admission was rejected before activation."}}))
    assert projection["attention"]["status"] == "action_required"
    assert projection["attention"]["reason"] == "OperationOutcomeUnknown"
    assert projection["attention"]["message"] == "Contract admission was rejected before activation."
    assert next(c for c in projection["conditions"] if c["type"] == "Ready")["status"] == "False"


def test_conclusive_lifecycle_failure_shows_the_original_error_message():
    projection = application_condition_projection(_model(lifecycle={"operation": {
        "status": "failed", "revision": 4, "recovery_reason": "native_cbs_admission_required",
        "message": "Contract identity is already bound to another schema."}}))
    assert projection["attention"]["status"] == "degraded"
    assert projection["attention"]["message"] == "Contract identity is already bound to another schema."


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


def test_ready_component_count_takes_precedence_over_active_instance_count():
    projection = application_condition_projection(
        _model(
            execution_placement={
                "managed": True,
                "status": "planned",
                "desired_component_count": 2,
                "observed_active_count": 1,
                "observed_ready_component_count": 2,
            },
        )
    )

    assert projection["attention"]["status"] == "current"
    assert projection["attention"]["reason"] == "Ready"


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
