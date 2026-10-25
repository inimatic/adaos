"""Contract evolution proves consumer scope, identity and work policy."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from adaos.services.contract_evolution.records import digest, seal, validate
from adaos.services.contract_evolution.service import ContractEvolutionService, USAGE


def usage(service, *, app="application:demo", consumer="scenario:demo", source="source", contract="sdk:example.run", baseline="old", complete=True):
    return service.register_usage(seal(USAGE, application_ref=app, consumer_ref=consumer,
        consumer_digest=digest(source), producer_digest=digest("compiler"), complete=complete,
        unresolved=[] if complete else [{"code": "dynamic_usage"}], edges=[{
            "contract_ref": contract, "contract_digest": digest(baseline),
            "surface": ["run"], "exposure": "compiled"}]))


def change(service, classification="breaking", **kwargs):
    return service.publish_change(contract_ref="sdk:example.run", contract_kind="sdk_operation",
        owner_ref="core:sdk", before=digest("old"), after=digest("new"),
        classification=classification, **kwargs)


def assess(service, entry, delta):
    wave = service.create_wave([delta["change_id"]], owner_ref="core:sdk")
    return service.assess(wave_id=wave["wave_id"], usage_lock_id=entry["usage_lock_id"])[0]


def test_reads_do_not_create_state_and_tampering_is_rejected(tmp_path):
    service = ContractEvolutionService(tmp_path)
    assert service.application_projection("application:none")["posture"] == "unknown"
    assert not service.store.path.exists()
    record = change(service)
    repeated = change(service)
    assert record == repeated
    altered = deepcopy(record)
    altered["after"] = digest("forged")
    with pytest.raises(ValueError, match="digest/identity"):
        validate(altered)
    assert len(service.store.events()["items"]) == 1


@pytest.mark.parametrize("classification,contract,complete,status,action", [
    ("breaking", "sdk:unused", True, "unaffected", False),
    ("additive", "sdk:example.run", True, "compatible", False),
    ("deprecated", "sdk:example.run", True, "deprecated_compatible", False),
    ("potentially_breaking", "sdk:example.run", True, "verification_due", False),
    ("breaking", "sdk:example.run", True, "incompatible", True),
    ("breaking", "sdk:unused", False, "unknown", False),
])
def test_noise_policy(tmp_path, classification, contract, complete, status, action):
    service = ContractEvolutionService(tmp_path)
    entry = usage(service, contract=contract, complete=complete)
    result = assess(service, entry, change(service, classification))
    assert (result["compatibility"], result["action_required"]) == (status, action)
    if not action:
        assert service.materialize_ticket(result["assessment_id"]) is None
        assert not (tmp_path / "development_tickets" / "state.json").exists()


def test_ticket_materialization_is_idempotent_and_causally_scoped(tmp_path):
    service = ContractEvolutionService(tmp_path)
    delta, entry = change(service), usage(service)
    result = assess(service, entry, delta)
    ticket = service.materialize_ticket(result["assessment_id"])
    again = service.materialize_ticket(result["assessment_id"])
    assert ticket["ticket_id"] == again["ticket_id"]
    assert again["occurrence_count"] == 1
    context = ticket["metadata"]["contract_evolution"]
    assert context["target_contract_digest"] == delta["after"]
    assert context["baseline_digest"] == entry["consumer_digest"]
    assert context["purpose"] == "migration"
    assert ticket["target_scope"]["id"] == "demo"
    projection = service.application_projection("application:demo", consumer_digest=entry["consumer_digest"])
    assert projection["posture"] == "action_required"
    assert projection["ticket_count"] == 1
    assert projection["assessment_count"] == 1


def test_verification_is_exact_and_does_not_close_native_ticket(tmp_path):
    service = ContractEvolutionService(tmp_path)
    delta, entry = change(service), usage(service)
    result = assess(service, entry, delta)
    ticket = service.materialize_ticket(result["assessment_id"])
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    # Evidence from a different build cannot heal the affected build.
    service.record_verification(application_ref="application:demo", consumer_digest=digest("other"),
        change_id=delta["change_id"], environment_ref="profile:local/default", kind="contract_test",
        result="passed", report_digest=digest("test-report"), valid_until=expires, runner_ref="test:pytest")
    assert assess(service, entry, delta)["compatibility"] == "incompatible"
    service.record_verification(application_ref="application:demo", consumer_digest=entry["consumer_digest"],
        change_id=delta["change_id"], environment_ref="profile:local/default", kind="contract_test",
        result="passed", report_digest=digest("test-report"), valid_until=expires, runner_ref="test:pytest")
    healed = assess(service, entry, delta)
    assert healed["compatibility"] == "compatible"
    from adaos.services.development_tickets import DevelopmentTicketService
    assert DevelopmentTicketService(state_dir=tmp_path).get_ticket(ticket["ticket_id"])["status"] == "proposed"
    assert service.wave_projection(healed["wave_id"])["coverage"] == {"compatible": 1}


def test_wrong_baseline_never_uses_additive_compatibility_promise(tmp_path):
    service = ContractEvolutionService(tmp_path)
    result = assess(service, usage(service, baseline="unrelated"), change(service, "additive"))
    assert result["compatibility"] == "unknown"
    assert result["reason"] == "BaselineDigestMismatch"


def test_usage_lock_never_imports_consumer_and_retains_dynamic_unknowns():
    from adaos.services.contract_evolution.usage import build_usage_lock
    units = {"sdk:adaos.sdk.example.run": {"contract": {"digest": digest("run")}}}
    files = {"handlers/main.py": b"from adaos.sdk import example\nexample.run(**options)\nraise RuntimeError('never execute')"}
    record = build_usage_lock(files, application_ref="application:demo", consumer_ref="skill:demo",
                              consumer_digest=digest(files["handlers/main.py"].hex()), contracts=units)
    assert record["edges"][0]["contract_ref"] == "sdk:adaos.sdk.example.run"
    assert record["complete"] is False
    assert record["unresolved"][0]["code"] == "dynamic_sdk_arguments"


def test_sdk_publisher_uses_operation_units_and_dimension_findings(tmp_path):
    from adaos.services.contract_evolution.producers import publish_sdk_changes
    def exported(required):
        return {"tools": [{"kind": "sdk_function", "name": "adaos.sdk.example.run",
            "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": required},
            "contract": {"digest": digest(required)}}]}
    service = ContractEvolutionService(tmp_path)
    changes = publish_sdk_changes(service, exported([]), exported(["key"]))
    assert len(changes) == 1
    assert changes[0]["classification"] == "breaking"
    assert changes[0]["contract_ref"] == "sdk:adaos.sdk.example.run"
    assert "required_inputs_added" in {finding["kind"] for finding in changes[0]["findings"]}


def test_trusted_current_contract_index_contains_sdk_and_mcp_surfaces():
    from adaos.services.contract_evolution.producers import current_contracts, unit_digest
    units = current_contracts()
    assert "sdk:adaos.sdk.applications.get_application" in units
    assert "mcp:applications.show" in units
    assert unit_digest(units["mcp:applications.show"]).startswith("sha256:")


def test_safe_webui_migration_heals_only_its_exact_snapshot(tmp_path):
    import json
    from adaos.services.interface_deprecations import deprecation_contract, migrate_deprecated_interfaces
    from adaos.services.contract_evolution.usage import build_usage_lock
    from adaos.services.contract_evolution.producers import ui_units
    payload = {"ui": {"application": {"desktop": {"pageSchema": {"id": "demo", "widgets": [
        {"id": "open", "type": "ui.actions", "actions": [{"type": "openModal", "params": {"modalId": "details"}}]}]}},
        "modals": {"details": {"title": "Details", "schema": {}}}}}}
    original = deepcopy(payload)
    rule = deprecation_contract()["deprecated_interfaces"][0]
    units = ui_units({"authoring_lifecycle": {"deprecated_interfaces": [rule]}})
    service = ContractEvolutionService(tmp_path)
    entry = service.register_usage(build_usage_lock({"webui.json": json.dumps(payload).encode()},
        application_ref="application:demo", consumer_ref="scenario:demo", consumer_digest=digest("package"), contracts=units))
    delta = service.publish_change(contract_ref="webui:" + rule["interface"], contract_kind="webui", owner_ref="core:client",
        before=digest("old-interface"), after=digest(rule), classification="deprecated",
        migration_route="declarative_projection", migrator_refs=["migrator:" + rule["migrator"]])
    effective, result = migrate_deprecated_interfaces(payload, owner="demo")
    receipt = result["contract_receipt"]
    assert receipt["validated"]
    service.record_migration(receipt, usage_lock_id=entry["usage_lock_id"], change_id=delta["change_id"])
    healed = assess(service, entry, delta)
    assert healed["compatibility"] == "compatible"
    assert healed["reason"] == "TransparentMigrationVerified"
    assert service.materialize_ticket(healed["assessment_id"]) is None
    assert payload == original and effective != original
    other = usage(service)
    with pytest.raises(ValueError, match="exact consumer snapshot"):
        service.record_migration(receipt, usage_lock_id=other["usage_lock_id"], change_id=delta["change_id"])


def test_build_usage_evidence_is_reproducible():
    from adaos.services.contract_evolution.usage import build_usage_lock
    options = dict(application_ref="project:demo", consumer_ref="scenario:demo", consumer_digest=digest("source"), contracts={})
    assert build_usage_lock({}, **options) == build_usage_lock({}, **options)


def test_builder_repair_context_keeps_contract_evolution_acceptance(tmp_path):
    import json
    from adaos.services.development_tickets import _autonomous_repair_brief
    service = ContractEvolutionService(tmp_path)
    result = assess(service, usage(service), change(service))
    ticket = service.materialize_ticket(result["assessment_id"])
    brief = json.loads(_autonomous_repair_brief(ticket, {"repair_id": "repair:demo"}, target={"type": "scenario", "id": "demo"}))
    assert brief["contract_evolution"]["assessment_id"] == result["assessment_id"]
    assert brief["contract_evolution"]["purpose"] == "migration"
