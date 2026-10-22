import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adaos.services import zone_directory as directory
from adaos.services import zone_hosts


@pytest.fixture
def authority():
    key = Ed25519PrivateKey.generate()
    trust = {"schema": "inimatic.zone_directory_trust.v1", "keys": {"test": key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()}, "sources": ["https://directory.example.com/v1/root/zones"]}
    def signed(sequence=10, issued=1000, expires=2000, **changes):
        value = {"schema": "inimatic.zone_directory.v1", "sequence": sequence, "issuedAt": issued, "expiresAt": expires, "defaultZoneId": "ru", "zones": [
            {"id": "ru", "label": "RU", "apiBase": "https://ru.api.inimatic.com", "environment": "production", "state": "active"},
            {"id": "de", "label": "DE", "apiBase": "https://de.api.adaos.io", "environment": "production", "state": "active"}]}
        value.update(changes)
        raw = json.dumps(value).encode()
        encode = lambda x: base64.urlsafe_b64encode(x).decode().rstrip("=")
        return {"keyId": "test", "payload": encode(raw), "signature": encode(key.sign(raw))}
    return trust, signed


def test_signed_de_beats_legacy_alias_and_survives_restart(tmp_path, authority, monkeypatch):
    trust, signed = authority
    replica = directory.Replica(tmp_path / "cache.json", trust, clock=lambda: 1500)
    assert replica.accept(signed())
    assert replica.resolve("de")["apiBase"] == "https://de.api.adaos.io"
    monkeypatch.setenv("ADAOS_ZONE_DIRECTORY_ENABLED", "1")
    monkeypatch.setattr(directory, "_configured", replica)
    assert zone_hosts.canonical_zone_id("de") == "de"
    assert zone_hosts.zone_public_base_url("de") == "https://de.api.adaos.io"
    assert zone_hosts.zone_public_nats_ws_url("de") == "wss://de.api.adaos.io/nats"
    assert zone_hosts.resolve_zone_for_server("de", "de") == "de"
    from adaos.services.node_config import resolve_effective_root_base_url
    assert resolve_effective_root_base_url("https://ru.api.inimatic.com", zone_id="de") == "https://de.api.adaos.io"
    restored = directory.Replica(tmp_path / "cache.json", trust, clock=lambda: 1500)
    assert restored.resolve("de")["id"] == "de"
    with pytest.raises(ValueError):
        restored.accept(signed(sequence=9))
    with pytest.raises(ValueError):
        restored.accept(signed(sequence=10, expires=1999))


def test_expired_cache_keeps_high_water_without_redirect(tmp_path, authority, monkeypatch):
    trust, signed = authority
    replica = directory.Replica(tmp_path / "cache.json", trust, clock=lambda: 1500)
    replica.accept(signed())
    replica = directory.Replica(tmp_path / "cache.json", trust, clock=lambda: 2100)
    assert replica.high_water == 10
    with pytest.raises(ValueError):
        replica.resolve("de")
    with pytest.raises(ValueError):
        replica.accept(signed(sequence=9, issued=2000, expires=3000))
    replica.accept(signed(sequence=11, issued=2000, expires=3000))
    assert replica.resolve("de")["id"] == "de"


@pytest.mark.parametrize("change", [{"sequence": True}, {"sequence": 0}, {"issued": 1601}, {"expires": 1500}, {"expires": 1000+7*86400+1}, {"defaultZoneId": "absent"}, {"zones": []}, {"unexpected": True}])
def test_invalid_contract_rejected(authority, change):
    trust, signed = authority
    with pytest.raises(ValueError):
        directory.verify(signed(**change), trust["keys"], now=1500)


@pytest.mark.parametrize("origin", ["http://de.example.com", "https://127.0.0.1", "https://de.example.com/", "https://de.example.com/x", "https://user:pass@de.example.com", "https://de.example.com:444", "https://de.example.com?token=x"])
def test_unsafe_origins_rejected(origin):
    with pytest.raises(ValueError):
        directory.public_origin(origin)


def test_untrusted_key_tamper_and_private_key_refused(authority):
    trust, signed = authority
    envelope = signed()
    with pytest.raises(ValueError):
        directory.verify({**envelope, "keyId": "other"}, trust["keys"], 1500)
    with pytest.raises(Exception):
        directory.verify({**envelope, "payload": envelope["payload"][:-1] + "A"}, trust["keys"], 1500)
    private = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    with pytest.raises(ValueError):
        directory.verify(envelope, {"test": private}, 1500)


def test_peer_failure_retains_valid_cache(tmp_path, authority):
    trust, signed = authority
    requests = []
    def peer(source):
        requests.append(source)
        return {"bad": "response"}
    replica = directory.Replica(tmp_path / "cache.json", trust, clock=lambda: 1500, fetcher=peer)
    replica.accept(signed())
    replica.refresh()
    assert requests == trust["sources"]
    assert replica.resolve("de")["id"] == "de"


def test_redirect_handler_refuses_credential_leak():
    with pytest.raises(ValueError):
        directory.NoRedirect().redirect_request(None, None, None, None, "https://other.example.com")
