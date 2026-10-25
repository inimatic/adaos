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
