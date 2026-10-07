from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.pending_action_inventory import (
    audit_pending_action_projection,
    build_pending_action_baseline,
    load_pending_action_inventory,
    validate_legacy_publication,
)


ROOT = Path(__file__).resolve().parents[1]
ABI = ROOT / "src" / "adaos" / "abi"


def test_inventory_covers_all_sixteen_roadmap_families_and_validates() -> None:
    schema = json.loads(
        (ABI / "pending_action.producer_inventory.v1.schema.json").read_text(encoding="utf-8")
    )
    inventory = load_pending_action_inventory()

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({key: value for key, value in inventory.items() if key != "digest"})
    assert {item["roadmap_id"] for item in inventory["families"]} == {
        f"PA6-{index:02d}" for index in range(1, 17)
    }
    assert inventory["digest"].startswith("sha256:")


def test_inventory_source_evidence_resolves_to_real_symbols() -> None:
    inventory = load_pending_action_inventory()

    for family in inventory["families"]:
        for evidence in (family["publisher"], family["consumer"]["source"]):
            if evidence is None:
                continue
            source = ROOT / evidence["path"]
            assert source.is_file(), f"{family['roadmap_id']}: missing {source}"
            assert evidence["symbol"] in source.read_text(encoding="utf-8"), (
                f"{family['roadmap_id']}: missing symbol {evidence['symbol']}"
            )


def test_inventory_does_not_treat_topics_or_direct_methods_as_consumer_proof() -> None:
    inventory = load_pending_action_inventory()
    by_id = {item["roadmap_id"]: item for item in inventory["families"]}

    for roadmap_id in ("PA6-12", "PA6-13", "PA6-14", "PA6-15", "PA6-16"):
        assert by_id[roadmap_id]["consumer"]["status"] == "missing"
        assert by_id[roadmap_id]["status"] == "missing_consumer"
    assert by_id["PA6-08"]["status"] == "missing_publisher"


def test_dynamic_clarification_choices_are_bounded_by_family_contract() -> None:
    contract = validate_legacy_publication(
        kind="nlu.teacher.clarification",
        response_topic="nlp.teacher.candidate.confirmation.response",
        choices=["media_indexer", "media_server", "postpone"],
    )

    assert contract["roadmap_id"] == "PA6-05"
    with pytest.raises(ValueError, match="choice_contract_mismatch"):
        validate_legacy_publication(
            kind="nlu.teacher.clarification",
            response_topic="nlp.teacher.candidate.confirmation.response",
            choices=["postpone"],
        )


def test_projection_audit_is_redacted_and_reports_orphans_and_latency() -> None:
    snapshot = {
        "by_id": {
            "secret-active": {
                "kind": "skill.setup.approval",
                "status": "pending",
                "created_at": 80,
                "expires_at": 90,
                "title": "private title",
            },
            "secret-closed": {
                "kind": "application.access.revoke",
                "status": "responded",
                "created_at": 10,
                "finished_at": 20,
                "response": {"actor": "private actor"},
            },
            "secret-unknown": {"kind": "third.party.dynamic", "status": "pending", "created_at": 95},
        }
    }

    report = audit_pending_action_projection(snapshot, now=100)

    assert report["counts"] == {
        "total": 3,
        "active": 2,
        "terminal": 1,
        "expired_due": 1,
        "unknown_kind": 1,
        "missing_consumer": 1,
    }
    assert report["active_age_seconds"] == {"p50": 5.0, "p95": 20.0, "max": 20.0}
    assert report["closure_latency_seconds"] == {"p50": 10.0, "p95": 10.0}
    serialized = json.dumps(report, ensure_ascii=False)
    assert "secret-" not in serialized
    assert "private" not in serialized


def test_baseline_requires_all_revisions_and_positive_sample() -> None:
    revisions = {
        "core": "core-r1",
        "client": "client-r1",
        "application": "app-r1",
        "runtime": "runtime-r1",
        "sdk": "sdk-r1",
        "prompt": "prompt-r1",
    }
    evidence = build_pending_action_baseline(
        {"by_id": {}},
        component_revisions=revisions,
        topology={"mode": "isolated_fixture", "nodes": 1},
        sdk_discovery={"query": "human decision", "matches": []},
        sample={"size": 1, "failures": 0},
        now=100,
    )

    assert evidence["component_revisions"] == revisions
    assert evidence["digest"].startswith("sha256:")

    with pytest.raises(ValueError, match="missing component revisions: prompt"):
        build_pending_action_baseline(
            {"by_id": {}},
            component_revisions={key: value for key, value in revisions.items() if key != "prompt"},
            topology={},
            sdk_discovery={},
            sample={"size": 1},
        )
