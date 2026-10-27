import pytest

from adaos.services.yjs import projection_convergence as convergence


@pytest.fixture(autouse=True)
def isolated_receipts(monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(convergence, "_projections", OrderedDict())
    monkeypatch.setattr(convergence, "_repairs", OrderedDict())


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
