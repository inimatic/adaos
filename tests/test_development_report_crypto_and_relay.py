from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.development_report import DevelopmentReportAck
from adaos.services.applications.report_crypto import DevelopmentReportCryptoError, DevelopmentReportEnvelopeCrypto
from adaos.services.applications.report_directory import SubnetKeyDirectoryAuthority, SubnetKeyDirectoryClient
from adaos.services.applications.report_keys import SubnetKeyError, SubnetPurposeKeyStore
from adaos.services.applications.report_relay import (
    DevelopmentReportRelayBackpressure,
    DevelopmentReportRelayError,
    DurableDevelopmentReportRelay,
)


def _directory(tmp_path: Path, clock: list[datetime], zones: dict[str, str]):
    stores = {subnet: SubnetPurposeKeyStore(tmp_path / subnet.replace(":", "_"), now=lambda: clock[0]) for subnet in zones}
    authority = SubnetKeyDirectoryAuthority(tmp_path / "directory", zone_id="directory", now=lambda: clock[0])
    projection = None
    for subnet, zone in zones.items():
        stores[subnet].ensure_key(subnet, "message_signing")
        stores[subnet].ensure_key(subnet, "message_encryption")
        projection = authority.publish_subnet(subnet, home_zone=zone, keys=stores[subnet].list_public(subnet))
    client = SubnetKeyDirectoryClient()
    client.update(projection)
    return stores, authority, client


def test_envelope_crypto_survives_overlap_but_fails_closed_after_revocation(tmp_path: Path) -> None:
    clock = [datetime(2026, 9, 5, tzinfo=timezone.utc)]
    stores, authority, client = _directory(tmp_path, clock, {"subnet:guest": "zone_a", "subnet:publisher": "zone_a"})
    crypto = DevelopmentReportEnvelopeCrypto(key_store=stores["subnet:guest"], directory=client, now=lambda: clock[0])
    publisher_crypto = DevelopmentReportEnvelopeCrypto(key_store=stores["subnet:publisher"], directory=client, now=lambda: clock[0])

    envelope = crypto.seal({"value": "secret"}, message_kind="report", sender_subnet_ref="subnet:guest", recipient_subnet_ref="subnet:publisher")
    assert publisher_crypto.open(envelope, recipient_subnet_ref="subnet:publisher") == {"value": "secret"}

    old_key = stores["subnet:publisher"].active_key("subnet:publisher", "message_encryption")
    stores["subnet:publisher"].rotate("subnet:publisher", "message_encryption", actor="owner", overlap=timedelta(days=2))
    projection = authority.publish_subnet("subnet:publisher", home_zone="zone_a", keys=stores["subnet:publisher"].list_public("subnet:publisher"))
    client.update(projection)
    assert publisher_crypto.open(envelope, recipient_subnet_ref="subnet:publisher") == {"value": "secret"}

    stores["subnet:publisher"].revoke(old_key.key_id, actor="owner", reason="compromised", evidence_ref="incident:1")
    projection = authority.publish_subnet("subnet:publisher", home_zone="zone_a", keys=stores["subnet:publisher"].list_public("subnet:publisher"))
    client.update(projection)
    with pytest.raises((SubnetKeyError, DevelopmentReportCryptoError), match="usable"):
        publisher_crypto.open(envelope, recipient_subnet_ref="subnet:publisher")


def test_recovery_requires_existing_owner_factor(tmp_path: Path) -> None:
    store = SubnetPurposeKeyStore(tmp_path)
    store.ensure_key("subnet:guest", "message_signing")
    with pytest.raises(SubnetKeyError, match="owner recovery factor"):
        store.recover("subnet:guest", "message_signing", owner_factor_ref="", actor="owner")
    replacement = store.recover("subnet:guest", "message_signing", owner_factor_ref="recovery:trusted-device", actor="owner")
    assert replacement.status == "active"
    assert len([item for item in store.list_public("subnet:guest") if item.status == "active"]) == 1


def test_purpose_key_abi_and_signed_directory_rollback_protection(tmp_path: Path) -> None:
    clock = [datetime(2026, 9, 5, tzinfo=timezone.utc)]
    stores, authority, client = _directory(tmp_path, clock, {"subnet:guest": "zone_a"})
    key = stores["subnet:guest"].active_key("subnet:guest", "message_encryption")
    schema = json.loads((Path(__file__).parents[1] / "src" / "adaos" / "abi" / "subnet.purpose-key.v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(key.to_dict())
    old_projection = authority.projection()
    stores["subnet:guest"].rotate("subnet:guest", "message_signing", actor="owner")
    new_projection = authority.publish_subnet("subnet:guest", home_zone="zone_a", keys=stores["subnet:guest"].list_public("subnet:guest"))
    client.update(new_projection)
    with pytest.raises(ValueError, match="rollback"):
        client.update(old_projection)


def test_same_zone_relay_is_at_least_once_and_deletes_ciphertext_on_ack(tmp_path: Path) -> None:
    clock = [datetime(2026, 9, 5, tzinfo=timezone.utc)]
    stores, _, client = _directory(tmp_path, clock, {"subnet:guest": "zone_a", "subnet:publisher": "zone_a"})
    crypto = DevelopmentReportEnvelopeCrypto(key_store=stores["subnet:guest"], directory=client, now=lambda: clock[0])
    envelope = crypto.seal({"report": 1}, message_kind="report", sender_subnet_ref="subnet:guest", recipient_subnet_ref="subnet:publisher", message_id="msg.offline")
    relay = DurableDevelopmentReportRelay(tmp_path / "relay", zone_id="zone_a", directory=client, now=lambda: clock[0])

    assert relay.enqueue(envelope)["duplicate"] is False
    assert relay.enqueue(envelope)["duplicate"] is True
    first = relay.poll("subnet:publisher")[0]
    second = relay.poll("subnet:publisher")[0]
    assert first["envelope"] == second["envelope"]
    with pytest.raises(DevelopmentReportRelayError, match="stale"):
        relay.acknowledge(DevelopmentReportAck(message_id="msg.offline", recipient_subnet_ref="subnet:publisher", disposition="accepted", delivery_id=first["delivery_id"]))
    receipt = relay.acknowledge(DevelopmentReportAck(message_id="msg.offline", recipient_subnet_ref="subnet:publisher", disposition="accepted", delivery_id=second["delivery_id"]))
    assert receipt["ciphertext_deleted_at"]
    assert relay.poll("subnet:publisher") == []

    forged = envelope.to_dict()
    forged["message_id"] = "msg.forged"
    with pytest.raises(DevelopmentReportRelayError, match="signature"):
        relay.enqueue(forged)


def test_relay_ttl_dead_letter_backpressure_and_cross_zone_forward(tmp_path: Path) -> None:
    clock = [datetime(2026, 9, 5, tzinfo=timezone.utc)]
    stores, _, client = _directory(tmp_path, clock, {"subnet:guest": "zone_a", "subnet:publisher": "zone_b"})
    crypto = DevelopmentReportEnvelopeCrypto(key_store=stores["subnet:guest"], directory=client, now=lambda: clock[0])
    envelope = crypto.seal({"report": 1}, message_kind="report", sender_subnet_ref="subnet:guest", recipient_subnet_ref="subnet:publisher", ttl=timedelta(hours=1), message_id="msg.cross")
    source = DurableDevelopmentReportRelay(tmp_path / "source", zone_id="zone_a", directory=client, now=lambda: clock[0], max_per_recipient=1)
    destination = DurableDevelopmentReportRelay(tmp_path / "destination", zone_id="zone_b", directory=client, now=lambda: clock[0])
    source.trust_peer(destination.public_identity())
    destination.trust_peer(source.public_identity())
    source.enqueue(envelope)
    with pytest.raises(DevelopmentReportRelayBackpressure):
        source.enqueue(crypto.seal({"report": 2}, message_kind="report", sender_subnet_ref="subnet:guest", recipient_subnet_ref="subnet:publisher", message_id="msg.cross2"))
    assert source.forward("msg.cross", destination)["disposition"] == "forwarded"
    assert destination.poll("subnet:publisher")[0]["envelope"]["message_id"] == "msg.cross"

    expiring = crypto.seal({"report": 3}, message_kind="report", sender_subnet_ref="subnet:guest", recipient_subnet_ref="subnet:publisher", ttl=timedelta(minutes=1), message_id="msg.expiring")
    source.enqueue(expiring)
    clock[0] += timedelta(minutes=2)
    assert source.sweep()["dead_letters"] == 1
