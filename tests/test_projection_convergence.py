import pytest

from adaos.services.yjs import projection_convergence as convergence


@pytest.fixture(autouse=True)
def isolated_receipts(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(convergence, "_projections", OrderedDict())
    monkeypatch.setattr(convergence, "_repairs", OrderedDict())
    monkeypatch.setattr(convergence, "_navigations", OrderedDict())
    monkeypatch.setattr(convergence, "_navigation_requests", OrderedDict())


def contract(generation="g1"):
    return {"generation": generation, "scenario_id": "applications", "payload_hash": "hash", "complete": True}


def test_delayed_durable_navigation_cannot_restore_the_older_scenario(monkeypatch):
    from adaos.services.workspaces import index
    writes = []
    monkeypatch.setattr(index, '_set_workspace_current_scenario_overlay',
                        lambda desktop, scenario, **_: writes.append((desktop, scenario)))
    assert convergence.claim_navigation('b', 'old')
    assert convergence.claim_navigation('b', 'new')
    with pytest.raises(RuntimeError, match='navigation_superseded'):
        index.set_workspace_current_scenario_overlay('b', 'old-scenario', navigation_id='old')
    index.set_workspace_current_scenario_overlay('b', 'new-scenario', navigation_id='new')
    assert writes == [('b', 'new-scenario')]


def test_a_durable_commit_does_not_serialize_other_desktops():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()
    convergence.claim_navigation('a', 'first')
    def hold_commit():
        with convergence.navigation_commit('a', 'first'):
            entered.set()
            assert release.wait(3)
    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(hold_commit)
        assert entered.wait(1)
        try:
            assert executor.submit(convergence.claim_navigation, 'b', 'independent').result(timeout=1)
            assert convergence.navigation_is_current('b', 'independent')
        finally:
            release.set()
            holder.result(timeout=1)


def test_send_or_incomplete_receipt_cannot_establish_application():
    convergence.publish("desktop", contract())
    assert convergence.snapshot("desktop")["state"] == "awaiting_client"
    assert not convergence.acknowledge("desktop", "browser", contract() | {"complete": False})["ok"]
    assert convergence.snapshot("desktop")["state"] == "awaiting_client"
    assert convergence.acknowledge("desktop", "browser", contract())["state"] == "applied"


def test_persisted_yjs_identity_is_detached_before_leaving_document_owner():
    import json
    import y_py as Y

    doc = Y.YDoc()
    root = doc.get_map('materialization')
    with doc.begin_transaction() as txn:
        for key, value in contract().items():
            root.set(txn, key, value)
        root.set(txn, 'identity', Y.YMap({'application_id': 'builder',
            'nested': Y.YMap({'release': 'sha256:exact'}), 'components': Y.YArray(['one'])}))
    # This is the real gateway's retained-bootstrap shallow extraction.
    convergence.publish('persisted', dict(root.items()))
    with doc.begin_transaction() as txn:
        root.get('identity').get('nested').set(txn, 'release', 'sha256:changed')
        root.get('identity').get('components').append(txn, 'two')
    observed = convergence.snapshot('persisted')
    assert observed['identity'] == {'application_id': 'builder',
        'nested': {'release': 'sha256:exact'}, 'components': ['one']}
    json.dumps(observed, allow_nan=False)
    observed['identity']['components'].append('caller-change')
    assert convergence.snapshot('persisted')['identity']['components'] == ['one']
    assert convergence.acknowledge('persisted', 'browser', contract())['ok']


def test_plain_projection_identity_does_not_alias_its_publisher():
    identity = {'application_id': 'builder', 'components': ['one']}
    convergence.publish('plain', contract() | {'identity': identity})
    identity['components'].append('changed')
    assert convergence.snapshot('plain')['identity']['components'] == ['one']


@pytest.mark.parametrize('invalid', [object(), float('nan'), float('inf')])
def test_invalid_identity_cannot_replace_the_previous_projection(invalid):
    convergence.publish('guarded', contract())
    with pytest.raises((ValueError, TypeError)):
        convergence.publish('guarded', contract('g2') | {'identity': {'invalid': invalid}})
    assert convergence.snapshot('guarded')['generation'] == 'g1'


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
    assert convergence.claim_navigation("isolated", "nav:first") is False
    with pytest.raises(_StaleRebuildRequestError):
        _raise_if_rebuild_request_superseded("isolated", "nav:first")
    assert convergence.navigation_is_current("isolated", "nav:second")


def test_background_refresh_fence_prefers_claim_then_published_navigation():
    convergence.publish(
        "isolated",
        contract() | {"navigation_id": "nav:published"},
    )
    assert convergence.current_navigation_fence("isolated") == "nav:published"

    convergence.claim_navigation("isolated", "nav:claimed")
    assert convergence.current_navigation_fence("isolated") == "nav:claimed"
    assert convergence.current_navigation_projection("isolated") == {
        "ready": False,
        "reason": "navigation_in_progress",
        "request_id": "nav:claimed",
        "scenario_id": None,
    }

    convergence.publish(
        "isolated",
        contract("g2") | {
            "navigation_id": "nav:claimed",
            "scenario_id": "site-studio",
        },
    )
    assert convergence.current_navigation_projection("isolated") == {
        "ready": True,
        "reason": "projected_navigation",
        "request_id": "nav:claimed",
        "scenario_id": "site-studio",
    }


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


def test_phased_default_reconciliation_releases_generation_only_in_the_final_transaction():
    import y_py as Y
    from types import SimpleNamespace
    from adaos.services.scenario import webspace_runtime as runtime_module

    resolved = runtime_module.WebspaceResolverOutputs(
        webspace_id="isolated-phased", scenario_id="applications", source_mode="workspace",
        application={"desktop": {"pageSchema": {"id": "applications", "widgets": []}}},
        catalog={"apps": [], "widgets": []}, registry={}, installed={"apps": [], "widgets": []},
        desktop={}, webio={}, routing={}, skill_decls=[])
    doc, browser = Y.YDoc(), Y.YDoc()
    updates = []
    doc.observe_after_transaction(lambda event: updates.append(bytes(event.get_update())))
    runtime_module.WebspaceScenarioRuntime(SimpleNamespace())._apply_resolved_state_in_doc(doc, "isolated-phased", resolved)
    nonempty = [update for update in updates if len(update) > 2]
    assert len(nonempty) == 2
    Y.apply_update(browser, nonempty[0])
    assert browser.get_map("runtime").get("materialization")["complete"] is False
    Y.apply_update(browser, nonempty[1])
    assert browser.get_map("runtime").get("materialization")["complete"] is True
