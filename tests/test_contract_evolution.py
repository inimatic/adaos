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


def test_expired_verification_reselects_prior_assessment_without_mutating_history(tmp_path, monkeypatch):
    from adaos.services.contract_evolution import service as module
    service = ContractEvolutionService(tmp_path)
    entry, delta = usage(service), change(service)
    original = assess(service, entry, delta)
    service.record_verification(application_ref=entry["application_ref"], consumer_digest=entry["consumer_digest"],
        change_id=delta["change_id"], environment_ref="profile:local/default", kind="contract_test", result="passed",
        report_digest=digest("passed"), valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), runner_ref="test:pytest")
    verified = assess(service, entry, delta)
    assert verified["compatibility"] == "compatible"
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)
    monkeypatch.setattr(module, "datetime", Later)
    expired = assess(service, entry, delta)
    assert expired["assessment_id"] == original["assessment_id"]
    assert service.application_projection(entry["application_ref"])["posture"] == "action_required"
    assert service.store.get(verified["assessment_id"])["compatibility"] == "compatible"


def test_new_target_supersedes_old_contract_attention(tmp_path):
    service = ContractEvolutionService(tmp_path)
    entry = usage(service)
    original = assess(service, entry, change(service))
    service.materialize_ticket(original["assessment_id"])
    service.publish_change(contract_ref="sdk:example.run", contract_kind="sdk_operation", owner_ref="core:sdk",
        before=digest("new"), after=digest("third"), classification="additive", required_evidence=[])
    service.reconcile()
    posture = service.application_projection(entry["application_ref"])
    # Unknown baseline remains visible; the old target Ticket is not counted
    # against an unrelated/new target as though its assessment were current.
    assert posture["posture"] == "verification_due"
    assert posture["ticket_count"] == 0


def test_ui_schema_fragment_changes_do_not_affect_unrelated_components(tmp_path):
    from adaos.services.contract_evolution.producers import publish_ui_changes
    catalog = {"components": [
        {"id": "ui.form", "manifest": {"widget_type": "ui.form", "input_schema": "webui.v1.schema.json#/$defs/formInputs"}},
        {"id": "ui.list", "manifest": {"widget_type": "ui.list", "input_schema": "webui.v1.schema.json#/$defs/listInputs"}},
    ]}
    old = {"$defs": {"formInputs": {"type": "object"}, "listInputs": {"type": "object"}}}
    new = deepcopy(old)
    new["$defs"]["formInputs"]["required"] = ["resource"]
    changes = publish_ui_changes(ContractEvolutionService(tmp_path), catalog, catalog, previous_schema=old, current_schema=new)
    assert [entry["contract_ref"] for entry in changes] == ["webui:ui.form"]


def test_mcp_publisher_ignores_title_but_detects_required_argument(tmp_path):
    from adaos.services.contract_evolution.producers import publish_mcp_changes
    old = {"applications.show": {"id": "applications.show", "title": "Show", "input_schema": {"type": "object", "required": []}}}
    new = deepcopy(old)
    new["applications.show"]["title"] = "Inspect"
    service = ContractEvolutionService(tmp_path)
    assert publish_mcp_changes(service, old, new) == []
    new["applications.show"]["input_schema"]["required"] = ["application_id"]
    changes = publish_mcp_changes(service, old, new)
    assert changes[0]["classification"] == "breaking"
    assert changes[0]["contract_ref"] == "mcp:applications.show"


def test_an_unassessed_target_never_leaves_an_application_aligned(tmp_path):
    service = ContractEvolutionService(tmp_path)
    entry = usage(service)
    assess(service, entry, change(service, "additive"))
    assert service.application_projection(entry["application_ref"])["posture"] == "aligned"
    service.publish_change(contract_ref="sdk:another.read", contract_kind="sdk_operation", owner_ref="core:sdk",
        before=digest("a"), after=digest("b"), classification="additive")
    projection = service.application_projection(entry["application_ref"])
    assert projection["posture"] == "verification_due"
    assert not projection["coverage_complete"]
    assert projection["unassessed_change_count"] == 1
    service.reconcile()
    assert service.application_projection(entry["application_ref"])["posture"] == "aligned"


def test_queries_never_silently_truncate_and_product_read_degrades_safely(tmp_path, monkeypatch):
    from adaos.services.contract_evolution.store import EvolutionQueryLimitError
    service = ContractEvolutionService(tmp_path)
    entry = usage(service)
    assess(service, entry, change(service))
    change(service, "additive")
    with pytest.raises(EvolutionQueryLimitError, match="coverage is incomplete"):
        service.store.list("adaos.contract_change.v1", limit=1)
    def full(*args, **kwargs):
        raise EvolutionQueryLimitError("test bounded population exceeded")
    monkeypatch.setattr(service.store, "list", full)
    projection = service.application_projection(entry["application_ref"])
    assert projection["posture"] == "unknown"
    assert projection["reason"] == "AssessmentQueryLimitExceeded"
    assert projection["coverage_complete"] is False


def test_head_index_upgrade_preserves_other_consumers(tmp_path):
    service = ContractEvolutionService(tmp_path)
    delta = change(service, "additive")
    one, two = usage(service), usage(service, app="application:other")
    assess(service, one, delta)
    assess(service, two, delta)
    with service.store.connection(write=True) as db:
        db.execute("DROP TABLE assessment_heads")
    assert service.application_projection(two["application_ref"])["posture"] == "aligned"
    assess(service, one, delta)  # Initializes the index for all historical consumers.
    assert service.application_projection(two["application_ref"])["posture"] == "aligned"


def test_contract_descriptor_is_bound_to_the_published_digest(tmp_path):
    service = ContractEvolutionService(tmp_path)
    with pytest.raises(ValueError, match="descriptor does not match"):
        change(service, after_descriptor={"required": ["new"]})
    descriptor = {"required": ["new"]}
    record = service.publish_change(contract_ref="sdk:example.run", contract_kind="sdk_operation", owner_ref="core:sdk",
        before=digest("old"), after=digest(descriptor), after_descriptor=descriptor, classification="breaking")
    entry = usage(service)
    ticket = service.materialize_ticket(assess(service, entry, record)["assessment_id"])
    assert ticket["metadata"]["contract_evolution"]["target_contract"] == descriptor


def test_module_alias_escape_is_unknown_even_with_an_indexed_call():
    from adaos.services.contract_evolution.usage import build_usage_lock
    record = build_usage_lock({"handler.py": b"from adaos.sdk import example\nexample.run()\nsaved = example\n"},
        application_ref="application:demo", consumer_ref="skill:demo", consumer_digest=digest("source"),
        contracts={"sdk:adaos.sdk.example.run": {"contract": {"digest": digest("run")}}})
    assert not record["complete"]
    assert "sdk_alias_dataflow_unknown" in {item["code"] for item in record["unresolved"]}


def test_adoption_event_observes_native_installation_without_changing_it(tmp_path):
    from adaos.domain.application import Application, ApplicationInstallation
    from adaos.services.applications.store import ApplicationStore
    service = ContractEvolutionService(tmp_path)
    entry, delta = usage(service), change(service, "additive")
    assess(service, entry, delta)
    store = ApplicationStore(tmp_path)
    store.save_application(Application(application_id="demo", legacy_project_id="demo", publisher_ref="subnet:sn_home",
        slug="demo", display={"title": "Demo", "summary": None}, visibility="public",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:demo"},),
        publisher={"publisher_ref": "subnet:sn_home", "display_name": "Home", "subnet_short_ref": "sn_home",
                   "release_key_ref": "subnet-key:release-signing:1", "release_key_fingerprint": digest("key"),
                   "home_zone": "local-dev", "trust_relation": "local"}), expected_revision=0)
    installation = store.save_installation(ApplicationInstallation(
        installation_id="installation:demo", application_id="demo", installed_release_digest=entry["consumer_digest"],
        component_refs=({"component_ref": "scenario:demo", "package_digest": digest("package"), "lifecycle": "bound"},),
        data_policy="retain", status="active", revision=1), expected_revision=0)
    assert service.observe_adoptions()[0]["installation_revision"] == 1
    service.observe_adoptions()
    events = service.store.events(limit=1000)["items"]
    assert len([item for item in events if item["type"] == "compatible_release.activated"]) == 1
    assert store.get_installation("demo") == installation


@pytest.mark.parametrize("classification", ["additive", "deprecated", "compatible_by_policy"])
def test_incomplete_usage_is_not_sufficient_compatibility_proof(tmp_path, classification):
    service = ContractEvolutionService(tmp_path)
    entry = usage(service, complete=False)
    result = assess(service, entry, change(service, classification))
    assert result["compatibility"] == "unknown"
    assert result["reason"] == "UsageIncomplete"
    assert not result["action_required"]
