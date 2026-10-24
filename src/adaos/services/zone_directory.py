"""Signed public placement directory. Never send node credentials to discovery peers."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_BYTES = 65536
PINNED_TRUST = {
    "schema": "inimatic.zone_directory_trust.v1",
    "keys": {"zone-directory-20261022-1": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA3GNxJqDRokYeDebsDX1Q5PhWbj7BIA3mF9aoNDybhBA=\n-----END PUBLIC KEY-----\n"},
    "sources": ["https://de.api.adaos.io/v1/root/zones", "https://api.inimatic.com/v1/root/zones", "https://ru.api.inimatic.com/v1/root/zones"],
}


def exact(value, names):
    return isinstance(value, dict) and set(value) == set(names.split())


def decode(value, maximum=MAX_BYTES):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value) or len(value) > maximum:
        raise ValueError("invalid directory encoding")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
        raise ValueError("noncanonical directory encoding")
    return raw


def public_origin(value):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("invalid API origin")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port or parsed.path or
            parsed.query or parsed.fragment or "." not in host or host == "localhost" or
            not re.fullmatch(r"[a-z0-9.-]+", host) or re.fullmatch(r"[0-9.]+", host) or value != "https://" + host):
        raise ValueError("normalized HTTPS DNS origin required")
    return value


def verify(envelope, keys=PINNED_TRUST["keys"], now=None):
    now = time.time() if now is None else now
    if not exact(envelope, "keyId payload signature") or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(envelope.get("keyId", ""))) or envelope["keyId"] not in keys:
        raise ValueError("untrusted directory key")
    key = serialization.load_pem_public_key(keys[envelope["keyId"]].encode())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Ed25519 public key required")
    raw = decode(envelope["payload"])
    signature = decode(envelope["signature"], 128)
    if len(signature) != 64:
        raise ValueError("invalid signature size")
    key.verify(signature, raw)
    value = json.loads(raw)
    if not exact(value, "schema sequence issuedAt expiresAt defaultZoneId zones"):
        raise ValueError("invalid directory fields")
    if (value["schema"] != "inimatic.zone_directory.v1" or
            any(type(value[x]) is not int or abs(value[x]) > 2**53-1 for x in ("sequence", "issuedAt", "expiresAt")) or
            value["sequence"] < 1 or value["issuedAt"] > now + 60 or value["expiresAt"] <= now or
            not 0 < value["expiresAt"] - value["issuedAt"] <= 7*86400 or
            not isinstance(value["zones"], list) or not 1 <= len(value["zones"]) <= 32):
        raise ValueError("invalid or expired directory")
    ids = set()
    for zone in value["zones"]:
        if (not exact(zone, "id label apiBase environment state") or not isinstance(zone["id"], str) or
                not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", zone["id"]) or zone["id"] == "lo" or zone["id"] in ids or
                not isinstance(zone["label"], str) or not zone["label"].strip() or len(zone["label"]) > 64 or
                re.search(r"[\x00-\x1f]", zone["label"]) or zone["environment"] not in ("production", "rehearsal") or
                zone["state"] not in ("active", "enrollment", "disabled")):
            raise ValueError("invalid zone record")
        public_origin(zone["apiBase"])
        ids.add(zone["id"])
    if not any(z["id"] == value["defaultZoneId"] and z["state"] == "active" and z["environment"] == "production" for z in value["zones"]):
        raise ValueError("invalid default zone")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("directory redirects refused")


def fetch(source):
    request = Request(source, headers={"Accept": "application/json"})
    with build_opener(NoRedirect()).open(request, timeout=3) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("directory response too large")
    return json.loads(raw)


class Replica:
    def __init__(self, cache, trust=PINNED_TRUST, clock=time.time, fetcher=fetch):
        self.cache, self.trust, self.clock, self.fetcher = Path(cache), trust, clock, fetcher
        self.envelope, self.value, self.high_water, self.digest = None, None, 0, ""
        self.lock = threading.RLock()
        if not exact(trust, "schema keys sources") or trust["schema"] != "inimatic.zone_directory_trust.v1" or not isinstance(trust["keys"], dict) or not 1 <= len(trust["keys"]) <= 4 or not isinstance(trust["sources"], list) or len(trust["sources"]) > 8:
            raise ValueError("invalid trust")
        for pem in trust["keys"].values():
            if not isinstance(serialization.load_pem_public_key(pem.encode()), Ed25519PublicKey):
                raise ValueError("Ed25519 public trust required")
        for source in trust["sources"]:
            if source != public_origin(source.removesuffix("/v1/root/zones")) + "/v1/root/zones":
                raise ValueError("invalid discovery source")
        if self.cache.is_symlink():
            raise ValueError("directory cache symlink refused")
        if self.cache.exists():
            if self.cache.stat().st_size > MAX_BYTES:
                raise ValueError("directory cache too large")
            envelope = json.loads(self.cache.read_bytes())
            issued = json.loads(decode(envelope.get("payload")))["issuedAt"]
            self._admit(envelope, issued, persist=False)

    def _admit(self, envelope, now, persist=True):
        value = verify(envelope, self.trust["keys"], now)
        if value["issuedAt"] > self.clock() + 60:
            raise ValueError("future cache refused")
        digest = hashlib.sha256(decode(envelope["payload"])).hexdigest()
        with self.lock:
            if value["sequence"] < self.high_water or value["sequence"] == self.high_water and digest != self.digest:
                raise ValueError("directory rollback/conflict refused")
            if value["sequence"] == self.high_water:
                return False
            if persist:
                self.cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if any(p.is_symlink() for p in (self.cache, *self.cache.parents)):
                    raise ValueError("directory cache symlink refused")
                descriptor, temporary = tempfile.mkstemp(prefix=".zone-directory-", dir=self.cache.parent)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(json.dumps(envelope).encode()); output.flush(); os.fsync(output.fileno())
                    os.replace(temporary, self.cache)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            self.envelope, self.value = json.loads(json.dumps(envelope)), value
            self.high_water, self.digest = value["sequence"], digest
            return True

    def accept(self, envelope):
        return self._admit(envelope, self.clock())

    def snapshot(self):
        with self.lock:
            if self.value is None or self.value["expiresAt"] <= self.clock():
                raise ValueError("signed zone directory unavailable or expired")
            return json.loads(json.dumps(self.value))

    def refresh(self):
        for source in self.trust["sources"]:
            try:
                self.accept(self.fetcher(source))
            except Exception:
                pass  # Invalid peer never replaces a verified fresh cache.

    def resolve(self, zone_id):
        value = self.snapshot()
        for zone in value["zones"]:
            if zone["id"] == zone_id and zone["state"] in ("active", "enrollment"):
                return zone
        raise ValueError("zone not admitted by signed directory")


_configured = None
_configuration_lock = threading.Lock()


def configured_replica():
    """Opt-in preserves existing installs; new installers persist explicit admission."""
    if os.environ.get("ADAOS_ZONE_DIRECTORY_ENABLED") != "1":
        return None
    global _configured
    with _configuration_lock:
        if _configured is None:
            cache = os.environ.get("ADAOS_ZONE_DIRECTORY_CACHE_FILE")
            if not cache or not Path(cache).is_absolute():
                raise ValueError("absolute durable directory cache required")
            _configured = Replica(cache)
            def refresh_loop():
                while True:
                    _configured.refresh()
                    time.sleep(60)
            threading.Thread(target=refresh_loop, name="zone-directory", daemon=True).start()
        return _configured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["resolve"])
    parser.add_argument("--zone", required=True)
    parser.add_argument("--cache", required=True)
    args = parser.parse_args()
    try:
        replica = Replica(args.cache)
        replica.refresh()
        print(replica.resolve(args.zone)["apiBase"])
    except Exception:
        parser.exit(1, "No fresh signed placement for requested zone; installation stopped.\n")


if __name__ == "__main__":
    main()
