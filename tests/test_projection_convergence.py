import pytest

from adaos.services.yjs import projection_convergence as convergence


@pytest.fixture(autouse=True)
def isolated_receipts(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(convergence, "_projections", OrderedDict())
    monkeypatch.setattr(convergence, "_repairs", OrderedDict())
    monkeypatch.setattr(convergence, "_navigations", OrderedDict())


def contract(generation="g1"):
    return {"generation": generation, "scenario_id": "applications", "payload_hash": "hash", "complete": True}


def test_send_or_incomplete_receipt_cannot_establish_application():
    convergence.publish("desktop", contract())
    assert convergence.snapshot("desktop")["state"] == "awaiting_client"
    assert not convergence.acknowledge("desktop", "browser", contract() | {"complete": False})["ok"]
    assert convergence.snapshot("desktop")["state"] == "awaiting_client"
    assert convergence.acknowledge("desktop", "browser", contract())["state"] == "applied"


def test_stale_generation_cannot_acknowledge_a_new_projection():
    convergence.publish("desktop", contract())
    convergence.acknowledge("desktop", "browser", contract())
    convergence.publish("desktop", contract("g2"))
    assert convergence.snapshot("desktop")["state"] == "awaiting_client"
    assert convergence.acknowledge("desktop", "browser", contract())["error"] == "projection_identity_mismatch"
    assert convergence.acknowledge("desktop", "browser", contract("g2"))["ok"]


def test_idempotent_publish_keeps_ack_and_storage_is_bounded():
    convergence.publish("desktop", contract())
    convergence.acknowledge("desktop", "browser", contract())
    convergence.publish("desktop", contract())
    assert convergence.snapshot("desktop")["state"] == "applied"
    for i in range(300):
        convergence.publish(str(i), contract())
    assert len(convergence._projections) == convergence._MAX_WEBSPACES


def test_repair_circuit_is_scoped_to_client_and_generation():
    assert convergence.admit_repair("desktop", "a", "g1")
    assert not convergence.admit_repair("desktop", "a", "g1")
    assert convergence.admit_repair("desktop", "b", "g1")
    assert convergence.admit_repair("desktop", "a", "g2")


def test_new_navigation_fences_the_old_one_before_rebuild_status_changes():
    from adaos.services.scenario.webspace_runtime import _raise_if_rebuild_request_superseded, _StaleRebuildRequestError

    convergence.claim_navigation("isolated", "nav:first")
    convergence.claim_navigation("isolated", "nav:second")
    with pytest.raises(_StaleRebuildRequestError):
        _raise_if_rebuild_request_superseded("isolated", "nav:first")
    assert convergence.navigation_is_current("isolated", "nav:second")


def test_obsolete_read_is_fenced_without_cancelling_a_business_operation():
    from fastapi import HTTPException
    from adaos.apps.api.tool_bridge import ToolCall, _reject_obsolete_projection_read

    convergence.publish("desktop", contract("new"))
    body = ToolCall(tool="example:read", context={"webspace_id": "desktop", "projection_generation": "old"})
    with pytest.raises(HTTPException) as failure:
        _reject_obsolete_projection_read(body, mutating=False)
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == "obsolete_projection_read"
    _reject_obsolete_projection_read(body, mutating=True)


def test_partial_branch_failure_cannot_publish_a_complete_generation(monkeypatch):
    import y_py as Y
    from types import SimpleNamespace
    from adaos.services.scenario import webspace_runtime as runtime_module

    resolved = runtime_module.WebspaceResolverOutputs(
        webspace_id="isolated-apply-failure", scenario_id="applications", source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "applications", "widgets": []}}},
        catalog={"apps": [], "widgets": []}, registry={}, installed={"apps": [], "widgets": []},
        desktop={}, webio={}, routing={}, skill_decls=[])
    payload = runtime_module._resolved_outputs_to_materialized_payload(resolved)
    replace = runtime_module._replace_map_value

    def fail_catalog(y_map, txn, key, value):
        if key == "catalog":
            raise ValueError("injected_branch_failure")
        return replace(y_map, txn, key, value)

    monkeypatch.setattr(runtime_module, "_replace_map_value", fail_catalog)
    doc = Y.YDoc()
    runtime = runtime_module.WebspaceScenarioRuntime(SimpleNamespace())
    with pytest.raises(ValueError, match="injected_branch_failure"):
        runtime.apply_materialized_payload_to_doc(doc, "isolated-apply-failure", payload, replace_changed_branches=True)
    assert doc.get_map("runtime").get("materialization")["complete"] is False
    assert convergence.snapshot("isolated-apply-failure")["state"] == "unpublished"
