"""Private Root role HTTP service behind the existing certificate-verifying nginx.

No host port is published. User RPC uses verified Hub certificate headers;
inter-Root RPC uses a distinct Root-held HMAC key and HTTPS. Provider commands
and their private state remain inside the assigned infrastructure role.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping
import urllib.error
import urllib.request

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.public_site_provider import CloudflarePublicationDelivery
from adaos.services.public_site_root import PublicSiteRootService, PublicationRootError
from adaos.services.public_site_root_client import RootPublicationDelivery
from adaos.services.site_studio.contracts import digest

MAX_RPC_BYTES = 1600 * 1024
log = logging.getLogger("adaos.public-site-root")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicationRootError("root_redirect_refused", "Root publication redirect was refused.", 502)


class RootPeer:
    def __init__(self, origin: str, key: bytes):
        if origin not in {"https://api.inimatic.com", "https://ru.api.inimatic.com"}:
            raise ValueError("peer must be an admitted infrastructure Root")
        if len(key) < 32:
            raise ValueError("Root peer key is too short")
        self.origin, self.key = origin, key
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, envelope: Mapping[str, Any], *, timeout: float = 550) -> dict[str, Any]:
        request = envelope.get('request') if envelope.get('kind') == 'forward' else envelope
        safe = isinstance(request, Mapping) and request.get('action') in {'health','begin','chunk','status','site_status','usage'}
        deadline = time.monotonic() + timeout
        for attempt in range(3 if safe else 1):
            try:
                return self._call_once(envelope, timeout=max(0.1, deadline-time.monotonic()))
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                pause = float(attempt+1)
                if not safe or attempt == 2 or time.monotonic()+pause >= deadline:
                    raise
                time.sleep(pause)
        raise AssertionError('unreachable Root peer retry state')

    def _call_once(self, envelope: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        body = canonical(envelope)
        stamp = str(int(time.time()))
        signature = hmac.new(self.key, stamp.encode()+b"\n"+body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(self.origin+"/v1/public-sites/peer", data=body,
            headers={"Content-Type": "application/json", "X-AdaOS-Root-Time": stamp,
                     "X-AdaOS-Root-Signature": signature}, method="POST")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read(16384))
            except ValueError:
                error = {}
            raise PublicationRootError(error.get("error", "root_peer_unavailable"), error.get("message", "Infrastructure Root peer is unavailable."), exc.code) from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise PublicationRootError("invalid_peer_response", "Infrastructure Root peer returned an invalid response.", 502)
        return result["result"]

    def verify(self, body: bytes, stamp: str, signature: str) -> bool:
        if not re.fullmatch(r"[0-9]{10,12}", stamp) or abs(time.time()-int(stamp)) > 120:
            return False
        expected = hmac.new(self.key, stamp.encode()+b"\n"+body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


class PeerHttp:
    base_url = "root-peer:regru"

    def __init__(self, peer: RootPeer):
        self.peer = peer

    def request(self, method, path, *, json, timeout=550):
        if method != "POST" or path != "/v1/public-sites/rpc":
            raise ValueError("unsupported Root peer method")
        return {"ok": True, "result": self.peer.call({"kind": "mirror", **json}, timeout=timeout)}


class ZonedRootDelivery(CloudflarePublicationDelivery):
    """Global Root owns Cloudflare/Firebase; REG.RU runs only on RU Root."""
    def __init__(self, *args, peer: RootPeer, **kwargs):
        super().__init__(*args, **kwargs)
        self.mirror = RootPublicationDelivery(PeerHttp(peer))

    def cloudflare_adapter(self, environment):
        import importlib.util
        spec = importlib.util.spec_from_file_location("root_publication_provider", self.script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.bootstrap.load_secrets(self.secrets, {"CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID", "CLOUDFLARE_API_TOKEN",
            "CLOUDFLARE_R2_ACCESS_KEY_ID", "CLOUDFLARE_R2_SECRET_ACCESS_KEY"})
        return module.CloudflareDeliveryAdapter(module.foundation.Api(), module.foundation.read_config(), environment)

    def prepare(self, *, site_dir, workspace, platform_path, environment="prod"):
        from adaos.services.public_site_delivery import build_delivery_bundle
        site_id = build_delivery_bundle(site_dir).site_id
        binding = self.cloudflare_adapter(environment).kv_get("binding:path:"+platform_path)
        if binding is not None and binding.get("site_id") != site_id:
            raise PublicationRootError("site_address_conflict", "Live public address belongs to another site.", 403)
        return super().prepare(site_dir=site_dir, workspace=workspace, platform_path=platform_path, environment=environment)

    def _run_regru(self, command, *, site_dir, workspace, plan_digest=None):
        if command == "mirror-plan":
            bundle = __import__("adaos.services.public_site_delivery", fromlist=["build_delivery_bundle"]).build_delivery_bundle(site_dir)
            site_id = bundle.site_id
            result = self.mirror.prepare(site_dir=site_dir, workspace=workspace,
                platform_path="st_"+hashlib.sha256(site_id.encode()).hexdigest()[:16])
            return result
        if command == "mirror-apply":
            plan = json.loads((workspace/"publication-plan.json").read_text("utf-8"))
            return self.mirror.publish(site_dir=site_dir, workspace=workspace,
                platform_path=plan["platform_path"], environment="prod", plan_digest=plan_digest)
        raise ValueError("unsupported regional publication command")

    def _run_gc(self, **kwargs):
        # The previous GC command requires both providers' credentials locally.
        # Retain objects until split-role GC is separately admitted.
        raise RuntimeError("split-Root garbage collection is deferred")

    def publish(self, **kwargs):
        with mutation_lock(self.state_dir/"firebase-publication.lock", timeout_s=1):
            return super().publish(**kwargs)

    def activate_domain_for_plan(self, *args, **kwargs):
        with mutation_lock(self.state_dir/"firebase-publication.lock", timeout_s=1):
            return super().activate_domain_for_plan(*args, **kwargs)


class RegionalMirrorDelivery(CloudflarePublicationDelivery):
    """RU role: exact immutable REG.RU mirror, no Cloudflare/Firebase authority."""
    def prepare(self, *, site_dir, workspace, platform_path, environment="prod"):
        result = self._run_regru("mirror-plan", site_dir=site_dir, workspace=workspace)
        plan = {"schema": "adaos.public_site_regional_mirror_plan.v1", "environment": environment,
                "host": "reg.p.adaos.io", "platform_path": platform_path,
                "public_url": "https://reg.p.adaos.io/"+platform_path+"/",
                "provider_plan_digest": result["plan_sha256"],
                "pointer": {"deployment_digest": result["deployment_digest"], "pointer_digest": digest({"mirror": result["plan_sha256"]})}}
        atomic_write_json(workspace/"publication-plan.json", plan)
        return {"ok": True, "plan_sha256": digest(plan), **plan["pointer"]}

    def publish(self, *, site_dir, workspace, platform_path, plan_digest, environment="prod"):
        plan = json.loads((workspace/"publication-plan.json").read_text("utf-8"))
        if digest(plan) != plan_digest or plan["platform_path"] != platform_path or plan["environment"] != environment:
            raise ValueError("regional mirror plan changed after review")
        result = self._run_regru("mirror-apply", site_dir=site_dir, workspace=workspace, plan_digest=plan["provider_plan_digest"])
        return {"ok": True, **plan["pointer"], "url": plan["public_url"], "receipt_id": result.get("receipt", "")}


class RootRole:
    def __init__(self, config: Mapping[str, Any]):
        self.config = dict(config)
        self.zone = config["zone"]
        if self.zone not in {"global", "ru"}:
            raise ValueError("unknown publication Root role")
        key = Path(config["peer_key_file"]).read_bytes()
        peer_origin = "https://ru.api.inimatic.com" if self.zone == "global" else "https://api.inimatic.com"
        self.peer = RootPeer(peer_origin, key)
        state = Path(config["state_dir"])
        adapter_type = ZonedRootDelivery if self.zone == "global" else RegionalMirrorDelivery
        self.delivery = adapter_type(Path(config["adaos_root"]), state_dir=state,
            **({"peer": self.peer} if self.zone == "global" else {}))
        self.engine = PublicSiteRootService(state/"root-publication", self.delivery, publication_authority=self.zone=="global")
        self.ownership_admission = state/"ownership-admission.json"
        self.opener = urllib.request.build_opener(NoRedirect())
        self.policy_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def migrate(self, path: Path) -> dict[str, Any]:
        """Operator-only bootstrap command, never exposed on the public RPC."""
        if self.zone != "global":
            raise ValueError("site ownership is admitted only by Global Root")
        import importlib.util
        source = self.delivery.script
        spec = importlib.util.spec_from_file_location("root_publication_migration", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.bootstrap.load_secrets(self.delivery.secrets, {
            "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_ZONE_ID", "CLOUDFLARE_API_TOKEN",
            "CLOUDFLARE_R2_ACCESS_KEY_ID", "CLOUDFLARE_R2_SECRET_ACCESS_KEY"})
        payload = json.loads(path.read_text("utf-8"))
        if payload.get("schema") != "adaos.public_site_root_migration.v1":
            raise ValueError("invalid migration schema")
        adapters = {environment:module.CloudflareDeliveryAdapter(module.foundation.Api(), module.foundation.read_config(), environment)
                    for environment in ("prod", "dev")}
        live = {(environment,key[5:-8]) for environment,adapter in adapters.items()
                for key in adapter.kv_keys("site:") if key.endswith(":current")}
        declared = {(site["environment"],site["site_id"]) for site in payload["sites"]}
        if declared != live or len(declared) != len(payload["sites"]):
            raise ValueError("ownership migration must cover the complete live site inventory in both public channels")
        admitted = []
        for site in payload["sites"]:
            adapter = adapters[site["environment"]]
            pointer = adapter.kv_get("site:"+site["site_id"]+":current")
            binding = adapter.kv_get("binding:path:"+site["platform_path"])
            if not isinstance(binding, dict) or binding.get("site_id") != site["site_id"]:
                raise ValueError("live platform binding differs from migration")
            self.engine.adopt(subnet=site["subnet_id"], site_id=site["site_id"], platform_path=site["platform_path"],
                environment=site["environment"], operation_id=site["operation_id"],
                expected_pointer_digest=site["pointer_digest"], verify=lambda: pointer, authoring=site.get("authoring"))
            if site.get("routing_descriptor"):
                from adaos.services.site_studio.contracts import validate_contract
                descriptor = site["routing_descriptor"]
                validate_contract("public_site_routing_descriptor.v1.schema.json", descriptor)
                if (descriptor["deployment_digest"] != pointer["deployment_digest"]
                        or descriptor["platform_path"] != site["platform_path"]
                        or descriptor["site_id"] != site["site_id"]
                        or descriptor["pointer_digest"] != pointer["pointer_digest"]
                        or digest({k:v for k,v in descriptor.items() if k!="descriptor_digest"}) != descriptor["descriptor_digest"]):
                    raise ValueError("routing migration does not match live publication")
                target = self.engine._path(site["operation_id"])/"provider/cloudflare/publication-plan.json"
                atomic_write_json(target, {"platform_path": site["platform_path"], "routing_descriptor": descriptor})
            for domain in site.get("domains", []):
                hostname = domain["hostname"]
                status = self.delivery.status_domain(hostname)
                if status.get("status") != "active" or status.get("ssl_status") != "active":
                    raise ValueError("existing custom domain is not live and verified")
                with self.engine._db() as db:
                    existing = db.execute("SELECT subnet_id,site_id FROM domains WHERE hostname=?", (hostname,)).fetchone()
                    if existing and tuple(existing) != (site["subnet_id"], site["site_id"]):
                        raise ValueError("domain migration ownership conflict")
                    db.execute("INSERT INTO domains VALUES(?,?,?,?,?) ON CONFLICT(hostname) DO NOTHING",
                        (hostname, site["subnet_id"], site["site_id"], "active", json.dumps(status)))
            admitted.append(site["site_id"])
        atomic_write_json(self.ownership_admission, {"schema":"adaos.public_site_ownership_admission.v1",
            "authority_id":self.engine.authority_id,"migration_digest":digest(payload),"site_channels":sorted(live)})
        return {"ok": True, "admitted_sites": admitted}

    def ownership_admitted(self) -> bool:
        if self.zone != "global":
            return True
        try:
            marker = json.loads(self.ownership_admission.read_text("utf-8"))
            return (marker.get("schema") == "adaos.public_site_ownership_admission.v1"
                    and marker.get("authority_id") == self.engine.authority_id)
        except (OSError, ValueError):
            return False

    def policy(self, subnet: str, headers: Mapping[str, str]) -> dict[str, Any]:
        # Same verified Hub identity, forwarded only over the private Root network.
        for backend in ("backend-a", "backend-b"):
            request = urllib.request.Request("http://"+backend+":3030/v1/hub/economic/entitlement", headers={
                "X-SSL-Client-Verify": "SUCCESS", "X-Client-Subject": "CN=subnet:"+subnet,
                "X-Client-Issuer": headers.get("X-Client-Issuer", ""), "X-Client-Cert": headers.get("X-Client-Cert", "")})
            try:
                with self.opener.open(request, timeout=10) as response:
                    result = json.load(response)
                if result.get("subnet_id") != subnet:
                    continue
                policy = result.get("entitlement", {}).get("public_sites")
                if isinstance(policy, dict):
                    return policy
            except (OSError, ValueError):
                continue
        raise PublicationRootError("subscription_authority_unavailable", "Root subscription policy is unavailable.", 503)

    def dispatch(self, subnet: str, policy: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        action, arguments = payload.get("action"), payload.get("arguments") or {}
        if action == "health":
            ready = self.delivery.secrets.is_file() and (self.zone == "ru" or self.delivery.client_release.is_file()) and self.ownership_admitted()
            if self.zone == "global" and ready:
                ready = self.peer.call({"kind": "mirror", "action": "health", "arguments": {}}, timeout=10).get("ready") is True
            return {"ready": ready, "zone": self.zone}
        if action not in {"status", "site_status"} and not self.ownership_admitted():
            raise PublicationRootError("publication_bootstrap_pending", "Verified publication ownership migration is required before admission.", 503)
        if action == "usage":
            return {**self.engine.usage(subnet, policy), "features": dict(policy)}
        if action == "begin":
            return self.engine.begin(subnet, arguments, policy)
        if action == "chunk":
            self.engine._policy(policy)
            return self.engine.chunk(subnet, arguments)
        if action == "prepare":
            return self.engine.prepare(subnet, arguments, policy)
        if action == "publish":
            return self.engine.publish(subnet, arguments, policy)
        if action == "status":
            return self.engine.status(subnet, arguments)
        if action == "site_status":
            return self.engine.site_status(subnet, arguments)
        if action == "domain" and self.zone == "global":
            return self.engine.domain(subnet, arguments, policy)
        raise PublicationRootError("invalid_action", "Unsupported publication operation.", 400)

    def rpc(self, payload: Mapping[str, Any], headers: Mapping[str, str], *, peer: bool = False, raw: bytes = b"") -> dict[str, Any]:
        if peer:
            if not self.peer.verify(raw, headers.get("X-AdaOS-Root-Time", ""), headers.get("X-AdaOS-Root-Signature", "")):
                raise PublicationRootError("root_peer_required", "Authenticated infrastructure Root peer required.", 403)
            kind = payload.get("kind")
            if self.zone == "ru" and kind == "mirror":
                return self.dispatch("infrastructure-global", {"publication": True, "published_sites_limit": None}, payload)
            if self.zone == "global" and kind == "forward":
                return self.dispatch(payload["subnet_id"], payload["policy"], payload["request"])
            raise PublicationRootError("root_role_mismatch", "Operation is not admitted for this Root role.", 403)
        subject = headers.get("X-Client-Subject", "")
        match = re.search(r"(?:^|[,/])\s*CN=subnet:([A-Za-z0-9_.-]+)(?:[,/]|$)", subject)
        if headers.get("X-SSL-Client-Verify") != "SUCCESS" or not match:
            raise PublicationRootError("hub_certificate_required", "Verified Hub certificate required.", 403)
        subnet = match[1]
        action = payload.get("action")
        if action in {"health", "status", "site_status"} or (action == "domain" and (payload.get("arguments") or {}).get("command") == "status"):
            policy = {}
        elif action == "chunk" and self.policy_cache.get(subnet, (0, {}))[0] > time.monotonic():
            policy = self.policy_cache[subnet][1]
        else:
            policy = self.policy(subnet, headers)
            self.policy_cache[subnet] = (time.monotonic()+20, policy)
        if self.zone == "ru":
            return self.peer.call({"kind": "forward", "subnet_id": subnet, "policy": policy, "request": payload})
        return self.dispatch(subnet, policy, payload)


class RootServer(ThreadingHTTPServer):
    daemon_threads = True
    role: RootRole
    admission = threading.BoundedSemaphore(24)


class Handler(BaseHTTPRequestHandler):
    server: RootServer

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path != "/healthz":
            self.send_error(404)
            return
        self.respond(200, {"ok": True, "zone": self.server.role.zone})

    def respond(self, status: int, value: Mapping[str, Any]):
        body = canonical(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in {"/v1/public-sites/rpc", "/v1/public-sites/peer"}:
            self.send_error(404)
            return
        if not self.server.admission.acquire(blocking=False):
            self.respond(503, {"ok": False, "error": "root_publication_busy", "message": "Root publication service is busy."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= MAX_RPC_BYTES or self.headers.get("Transfer-Encoding"):
                raise PublicationRootError("invalid_rpc_size", "Publication request exceeds its bounds.", 413)
            self.connection.settimeout(60)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("invalid request")
            result = self.server.role.rpc(payload, self.headers, peer=self.path.endswith("/peer"), raw=raw)
            self.respond(200, {"ok": True, "result": result})
        except PublicationRootError as exc:
            self.respond(exc.status, {"ok": False, "error": exc.code, "message": str(exc)})
        except (ValueError, TypeError, KeyError):
            self.respond(400, {"ok": False, "error": "invalid_publication_request", "message": "Publication request failed validation."})
        except Exception:
            log.exception("Root publication operation failed; private diagnostics retained on Root")
            self.respond(503, {"ok": False, "error": "root_publication_operation_failed", "message": "Root publication operation requires status reconciliation."})
        finally:
            self.server.admission.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--migrate", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    role = RootRole(json.loads(args.config.read_text("utf-8")))
    if args.migrate:
        print(json.dumps(role.migrate(args.migrate)))
        return
    server = RootServer(("0.0.0.0", 8787), Handler)
    server.role = role
    server.serve_forever()


if __name__ == "__main__":
    main()
